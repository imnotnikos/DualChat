"""DualChat: YouTube + Twitch live chat in one window, one box to reply to both."""
import codecs
import ctypes
import io
import json
import queue
import re
import socket
import ssl
import sys
import threading
import time
import tkinter as tk
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import font as tkfont, messagebox, scrolledtext

from PIL import Image, ImageTk

# as an .exe, __file__ is in a temp unpack folder; config and tokens live next to the .exe
HERE = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
ASSETS = Path(getattr(sys, "_MEIPASS", HERE))  # bundled files (the icon) unpack here in the .exe
TOKENS = HERE / "tokens.json"
YT_API = "https://www.googleapis.com/youtube/v3/"
YT_STREAM = "https://youtube.googleapis.com/youtube/v3/liveChat/messages/stream"
MAX_LEN = 200  # YouTube's chat limit; Twitch allows 500
POPUP_MS = 12_000
TWITCH_SUBS = {"sub", "resub", "subgift", "submysterygift", "giftpaidupgrade", "primepaidupgrade"}
YT_MEMBER_EVENTS = {"newSponsorEvent": "became a member!", "membershipGiftingEvent": "gifted memberships!",
                    "memberMilestoneChatEvent": "hit a membership milestone!"}
CFG = {}
_token_lock = threading.Lock()

OAUTH = {
    "youtube": {"device": "https://oauth2.googleapis.com/device/code",
                "token": "https://oauth2.googleapis.com/token",
                "scope_param": "scope", "scope": "https://www.googleapis.com/auth/youtube"},
    "twitch": {"device": "https://id.twitch.tv/oauth2/device",
               "token": "https://id.twitch.tv/oauth2/token",
               "scope_param": "scopes", "scope": "chat:read chat:edit moderator:read:followers"},
}


def http(url, form=None, body=None, token=None, auth="Bearer", headers=None):
    headers, data = dict(headers or {}), None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"{auth} {token}"
    with urllib.request.urlopen(urllib.request.Request(url, data, headers), timeout=30) as r:
        return json.load(r)


def error_body(e):
    try:
        body = json.loads(e.read() or b"{}")
    except ValueError:
        return {}
    if isinstance(body, list):  # the streaming endpoint wraps its error in a list
        body = body[0] if body else {}
    return body if isinstance(body, dict) else {}


def clean(text):
    """Collapse to one line: a newline would let typed text inject raw IRC commands."""
    return " ".join(text.split())


# ---- OAuth (device-code flow, same shape for Google and Twitch) ----

def load_tokens():
    return json.loads(TOKENS.read_text()) if TOKENS.exists() else {}


def expire(name):
    with _token_lock:
        saved = load_tokens()
        if name in saved:
            saved[name]["expires_at"] = 0
            TOKENS.write_text(json.dumps(saved, indent=2))


def get_token(name, log):
    with _token_lock:
        saved = load_tokens()
        old = saved.get(name, {})
        if old.get("expires_at", 0) > time.time() + 60:
            return old["access_token"]
        creds = {"client_id": CFG[name]["client_id"]}
        if CFG[name].get("client_secret"):
            creds["client_secret"] = CFG[name]["client_secret"]
        new = None
        if old.get("refresh_token"):
            try:
                new = http(OAUTH[name]["token"], form={**creds, "grant_type": "refresh_token",
                                                       "refresh_token": old["refresh_token"]})
            except urllib.error.HTTPError:
                log(f"{name}: saved login expired, signing in again")
        if new is None:
            new = device_login(name, creds, log)
        new.setdefault("refresh_token", old.get("refresh_token"))  # Google omits it on refresh
        new["expires_at"] = time.time() + new.get("expires_in", 3600)
        saved[name] = new
        TOKENS.write_text(json.dumps(saved, indent=2))
        return new["access_token"]


def device_login(name, creds, log):
    o = OAUTH[name]
    d = http(o["device"], form={"client_id": creds["client_id"], o["scope_param"]: o["scope"]})
    url = d.get("verification_url") or d["verification_uri"]  # Google / Twitch spelling
    log(f"{name}: sign in at {url}   code: {d['user_code']}")
    webbrowser.open(url)
    poll = {**creds, "device_code": d["device_code"],
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code"}
    if name == "twitch":
        poll["scopes"] = o["scope"]
    interval, deadline = d.get("interval", 5), time.time() + d.get("expires_in", 1800)
    while time.time() < deadline:
        time.sleep(interval)
        try:
            return http(o["token"], form=poll)
        except urllib.error.HTTPError as e:
            err = error_body(e)
            reason = err.get("error") or err.get("message")  # Google / Twitch
            if reason == "slow_down":
                interval += 5
            elif reason != "authorization_pending":
                raise RuntimeError(f"{name} sign-in failed: {err}") from e
    raise RuntimeError(f"{name} sign-in timed out")


# ---- Twitch (IRC over TLS) ----

def unescape_tag(v):
    return re.sub(r"\\(.)", lambda m: {":": ";", "s": " ", "r": "\r", "n": "\n"}.get(m[1], m[1]), v)


def parse_irc(line):
    """'@a=1;b=2 :nick!nick@host CMD #chan :text' -> ({'a': '1', 'b': '2'}, 'nick', 'CMD', 'text')"""
    tags = {}
    if line.startswith("@"):
        raw, _, line = line.partition(" ")
        for kv in raw[1:].split(";"):
            k, _, v = kv.partition("=")
            tags[k] = unescape_tag(v)
    prefix = ""
    if line.startswith(":"):
        prefix, _, line = line.partition(" ")
    cmd, _, rest = line.partition(" ")
    return tags, prefix[1:].split("!", 1)[0], cmd, rest.partition(" :")[2]


class Twitch:
    tag = "TW"

    def __init__(self, ui):
        self.ui, self.outbox, self.online = ui, queue.Queue(), False
        self.chan = self.viewers = None

    def run(self):
        threading.Thread(target=self.watch_viewers, daemon=True).start()
        while True:
            try:
                self.session()
            except Exception as e:
                self.ui.status(f"Twitch: {e} (reconnecting in 5s)")
            self.online = False
            time.sleep(5)

    def session(self):
        token = get_token("twitch", self.ui.status)
        self.login = http("https://id.twitch.tv/oauth2/validate", token=token, auth="OAuth")["login"]
        self.chan = (CFG["twitch"].get("channel") or self.login).lower().lstrip("#")
        self.outbox = queue.Queue()  # drop anything typed while disconnected
        raw = socket.create_connection(("irc.chat.twitch.tv", 6697), timeout=10)
        with ssl.create_default_context().wrap_socket(raw, server_hostname="irc.chat.twitch.tv") as s:
            s.settimeout(0.5)  # wake often to flush the outbox; all socket I/O stays on this thread
            s.sendall(f"CAP REQ :twitch.tv/tags twitch.tv/commands\r\n"  # tags carry sub events + display names
                      f"PASS oauth:{token}\r\nNICK {self.login}\r\nJOIN #{self.chan}\r\n".encode())
            buf, last = b"", time.time()
            while True:
                while not self.outbox.empty():
                    s.sendall(self.outbox.get())
                try:
                    chunk = s.recv(4096)
                except TimeoutError:
                    if time.time() - last > 360:  # Twitch PINGs about every 5 minutes
                        raise ConnectionError("connection went quiet")
                    continue
                if not chunk:
                    raise ConnectionError("disconnected")
                last = time.time()
                *lines, buf = (buf + chunk).split(b"\r\n")
                for line in lines:
                    self.handle(s, line.decode("utf-8", "replace"))

    def handle(self, s, line):
        if line.startswith("PING"):
            s.sendall(f"PONG{line[4:]}\r\n".encode())
        elif line.endswith(f" JOIN #{self.chan}"):
            self.online = True
            self.ui.status(f"Twitch: joined #{self.chan}")
        elif "authentication failed" in line or "Improperly formatted auth" in line:
            expire("twitch")
        else:
            tags, nick, cmd, text = parse_irc(line)
            if cmd == "PRIVMSG":
                self.ui.add(self.tag, tags.get("display-name") or nick, text)
            # gifts from a mystery gift each get their own notice; the one summary popup is enough
            elif (cmd == "USERNOTICE" and tags.get("msg-id") in TWITCH_SUBS
                  and not tags.get("msg-param-community-gift-id")):
                self.ui.alert(self.tag, tags.get("system-msg") or "New sub!", text)

    def watch_viewers(self):
        # own thread: a slow HTTP call must not stall the IRC socket loop
        broadcaster, seen = None, None
        while True:
            if self.chan:
                try:
                    token = get_token("twitch", self.ui.status)
                    def api(path):
                        return http("https://api.twitch.tv/helix/" + path, token=token,
                                    headers={"Client-Id": CFG["twitch"]["client_id"]})
                    chan = urllib.parse.quote(self.chan)
                    r = api("streams?user_login=" + chan)
                    self.viewers = r["data"][0]["viewer_count"] if r["data"] else None
                    broadcaster = broadcaster or api("users?login=" + chan)["data"][0]["id"]
                    follows = api(f"channels/followers?broadcaster_id={broadcaster}&first=20")["data"]
                    if seen is not None:  # first fetch only records who already follows
                        for f in reversed(follows):  # newest-first from the API, so flip it
                            if f["user_id"] not in seen:
                                self.ui.alert(self.tag, f"{f['user_name']} just followed!")
                    seen = (seen or set()) | {f["user_id"] for f in follows}
                except Exception as e:
                    self.ui.status(f"Twitch viewers/follows: {e}")
            time.sleep(10)

    def send(self, text):
        if not self.online:
            return self.ui.status("Twitch: not connected, message not sent")
        self.outbox.put(f"PRIVMSG #{self.chan} :{text}\r\n".encode())
        self.ui.add(self.tag, self.login, text)  # Twitch doesn't echo your own messages


# ---- YouTube (Data API v3, chat over one long-lived streamList connection) ----

def json_stream(chunks):
    """Yield each JSON object from a streamed body, either SSE ('data: {...}') or a JSON array ('[{...},{...}]')."""
    decode, text, buf = json.JSONDecoder().raw_decode, codecs.getincrementaldecoder("utf-8")(), ""
    for chunk in chunks:
        buf += text.decode(chunk)
        while True:
            buf = buf.lstrip(" \t\r\n[],")
            if buf.startswith("data:"):
                buf = buf[5:]
                continue
            if buf and buf[0] != "{":  # other SSE lines: ': keepalive', 'event: ...'
                nl = buf.find("\n")
                if nl < 0:
                    break
                buf = buf[nl + 1:]
                continue
            try:
                obj, end = decode(buf)
            except json.JSONDecodeError:
                break  # object not fully received yet
            yield obj
            buf = buf[end:]


class YouTube:
    tag = "YT"

    def __init__(self, ui):
        self.ui, self.chat_id, self.video_id, self.viewers, self.seen_subs = ui, None, None, None, None

    def run(self):
        threading.Thread(target=self.watch_stats, daemon=True).start()
        page = None
        while True:
            try:
                token = get_token("youtube", self.ui.status)
                if not self.chat_id:
                    live = http(YT_API + "liveBroadcasts?part=snippet&broadcastStatus=active&broadcastType=all",
                                token=token)["items"]
                    if not live:
                        self.ui.status("YouTube: no live stream right now, checking every 60s")
                        time.sleep(60)
                        continue
                    self.video_id, page, self.since = live[0]["id"], None, time.time()
                    self.chat_id = live[0]["snippet"]["liveChatId"]
                    self.ui.status(f"YouTube: connected to \"{live[0]['snippet']['title']}\"")
                # no alt=sse: this endpoint rejects it and streams a chunked JSON array instead
                q = {"liveChatId": self.chat_id, "part": "snippet,authorDetails"}
                if page:
                    q["pageToken"] = page  # resume where the last connection stopped
                req = urllib.request.Request(YT_STREAM + "?" + urllib.parse.urlencode(q),
                                             headers={"Authorization": f"Bearer {token}"})
                started = time.time()
                # 5 quota units per connection, however long it stays open
                with urllib.request.urlopen(req, timeout=300) as r:
                    for resp in json_stream(iter(lambda: r.read1(65536), b"")):
                        if "error" in resp:
                            raise RuntimeError(resp["error"].get("message", resp["error"]))
                        for m in resp.get("items", []):
                            name, kind = m["authorDetails"]["displayName"], m["snippet"]["type"]
                            self.ui.add(self.tag, name, m["snippet"].get("displayMessage", ""))
                            # the first connection replays recent history; only alert for what's new since then
                            sent = datetime.fromisoformat(m["snippet"]["publishedAt"]).timestamp()
                            if kind in YT_MEMBER_EVENTS and sent >= self.since:
                                self.ui.alert(self.tag, f"{name} {YT_MEMBER_EVENTS[kind]}")
                        page = resp.get("nextPageToken", page)
                        if resp.get("offlineAt"):
                            self.chat_id = self.viewers = None
                            self.ui.status("YouTube: stream went offline")
                            break
                # the server closed the stream: reconnect, but never in a tight loop that burns quota
                time.sleep(max(0, 5 - (time.time() - started)))
            except TimeoutError:
                continue  # chat was quiet for 5 minutes; just reconnect
            except urllib.error.HTTPError as e:
                err = error_body(e).get("error")
                if e.code == 401:
                    expire("youtube")
                if e.code in (403, 404):  # chat ended or quota used up: look for the stream again
                    self.chat_id = self.viewers = None
                msg = err.get("message") if isinstance(err, dict) else e.reason
                self.ui.status(f"YouTube: {msg} (retrying in 30s)")
                time.sleep(30)
            except Exception as e:
                self.ui.status(f"YouTube: {e} (retrying in 30s)")
                time.sleep(30)

    def watch_stats(self):
        # own thread: the chat stream can sit quiet for minutes, the viewer count shouldn't
        while True:
            if self.chat_id and self.video_id:
                try:
                    token = get_token("youtube", self.ui.status)
                    v = http(YT_API + "videos?part=liveStreamingDetails&id=" + self.video_id, token=token)["items"]
                    d = v[0].get("liveStreamingDetails", {}) if v else {}
                    live = d.get("actualStartTime") and not d.get("actualEndTime")
                    # YouTube leaves concurrentViewers out entirely while nobody is watching
                    self.viewers = int(d.get("concurrentViewers", 0)) if live else None
                    self.check_subs(token)
                except Exception as e:
                    self.ui.status(f"YouTube viewers: {e}")
            time.sleep(30)  # 2 quota units per round

    def check_subs(self, token):
        # own try: a failure here must not count as "chat ended" in run()
        try:
            subs = http(YT_API + "subscriptions?part=subscriberSnippet&myRecentSubscribers=true&maxResults=50",
                        token=token)["items"]
        except Exception as e:
            return self.ui.status(f"YouTube subscribers: {e}")
        if self.seen_subs is not None:  # first fetch only records existing subscribers
            for s in reversed(subs):  # newest-first from the API, so flip it
                if s["id"] not in self.seen_subs:
                    self.ui.alert(self.tag, f"{s['subscriberSnippet']['title']} subscribed!")
        self.seen_subs = (self.seen_subs or set()) | {s["id"] for s in subs}

    def send(self, text):
        if not self.chat_id:
            return self.ui.status("YouTube: no live chat, message not sent")
        body = {"snippet": {"liveChatId": self.chat_id, "type": "textMessageEvent",
                            "textMessageDetails": {"messageText": text}}}
        # no local echo: the message comes back through the chat stream
        http(YT_API + "liveChat/messages?part=snippet", body=body, token=get_token("youtube", self.ui.status))


# ---- Emoji ----
# Tk on Windows draws emoji in black and white and can't join sequences (skin tones, families, flags),
# so each emoji is shown as a Twemoji image instead.

_EMOJI_BASE = r"[\U0001F000-\U0001FAFF]|[⌚⌛⏩-⏬⏰⏳◽◾☔☕♈-♓" \
              r"♿⚓⚡⚪⚫⚽⚾⛄⛅⛎⛔⛪⛲⛳⛵⛺" \
              r"⛽✅✊✋✨❌❎❓-❕❗❤➕-➗➰➿" \
              r"⬛⬜⭐⭕]|[ -⯿〰〽㊗㊙]️"  # other symbols only with FE0F
EMOJI_RE = re.compile(
    r"[\U0001F1E6-\U0001F1FF]{2}"  # flags
    r"|[0-9#*]️?⃣"  # keycaps
    rf"|(?:{_EMOJI_BASE})[️\U0001F3FB-\U0001F3FF\U000E0020-\U000E007F]*"  # + skin tone / subdivision-flag tags
    rf"(?:‍(?:{_EMOJI_BASE}|[ -⯿])[️\U0001F3FB-\U0001F3FF]*)*"  # ZWJ-joined parts
)
EMOJI_CDN = "https://cdn.jsdelivr.net/gh/jdecked/twemoji@15.1.0/assets/72x72/"
_emoji_png = {}  # sequence -> PNG bytes, or None when Twemoji has no image for it


def twemoji_name(seq):
    if "‍" not in seq:  # Twemoji drops FE0F except inside ZWJ sequences
        seq = seq.replace("️", "")
    return "-".join(f"{ord(c):x}" for c in seq)


def fetch_emoji(text):
    """Download each new emoji's image once. Called on the chat threads so the window never waits on it."""
    for seq in EMOJI_RE.findall(text):
        if seq in _emoji_png:
            continue
        for name in dict.fromkeys((twemoji_name(seq), twemoji_name(seq.replace("️", "")))):
            try:
                with urllib.request.urlopen(EMOJI_CDN + name + ".png", timeout=5) as r:
                    _emoji_png[seq] = r.read()
                break
            except urllib.error.HTTPError:
                _emoji_png[seq] = None  # no such image: show the plain character
            except (urllib.error.URLError, OSError):
                break  # offline: leave uncached so it's retried next time


# ---- Window ----

def viewer_line(counts):
    """[('YT', 12), ('TW', None)] -> 'YT 12   TW offline   Total 12'"""
    parts = [f"{tag} {'offline' if n is None else n}" for tag, n in counts]
    live = [n for _, n in counts if n is not None]
    if len(counts) > 1 and live:
        parts.append(f"Total {sum(live)}")
    return "Viewers:   " + "   ".join(parts)


THEMES = {
    "dark": {"bg": "#0e0e10", "panel": "#18181b", "field": "#26262c", "text": "#efeff1", "border": "#3a3a3d",
             "select": "#5c3fa3", "YT": "#ff6b6b", "TW": "#bf94ff", "sys": "#8a8a93", "alert": "#ffd75e"},
    # chat colors darkened so they stay readable on white
    "light": {"bg": "#e9e9ee", "panel": "#ffffff", "field": "#ffffff", "text": "#18181b", "border": "#c4c4cc",
              "select": "#d4c2fb", "YT": "#c4170c", "TW": "#6a2fd8", "sys": "#6b6b76", "alert": "#9a5b00"},
}


class App:
    def __init__(self, services):
        self.q, self.last = queue.Queue(), None
        self.alerts, self.popups = queue.Queue(), {}  # popups: window -> stacking slot
        self.root = tk.Tk()
        try:
            self.root.iconbitmap(default=str(ASSETS / "dualchat.ico"))  # default= covers every window
        except tk.TclError:
            pass  # icon file missing: keep Tk's default
        scale = self.root.winfo_fpixels("1i") / 96  # fonts scale with DPI on their own; pixel sizes don't
        self.px = lambda n: round(n * scale)
        self.root.title("DualChat")
        self.root.geometry(f"{self.px(540)}x{self.px(760)}")
        self.bar = tk.Frame(self.root, padx=self.px(10), pady=self.px(10))
        self.bar.pack(side="bottom", fill="x")
        self.entry = tk.Entry(self.bar, font=("Segoe UI", 12), relief="flat", highlightthickness=self.px(2),
                              highlightcolor="#9146ff")
        self.entry.pack(fill="x", ipady=self.px(6))
        self.top = tk.Frame(self.root)
        self.top.pack(side="top", fill="x")
        self.toggle = tk.Button(self.top, font=("Segoe UI", 10), relief="flat", borderwidth=0, cursor="hand2",
                                padx=self.px(10), pady=self.px(3), command=self.flip_theme)
        self.toggle.pack(side="right", padx=self.px(10))
        self.counts = tk.Label(self.top, anchor="w", font=("Segoe UI Semibold", 11),
                               padx=self.px(12), pady=self.px(8))
        self.counts.pack(side="left", fill="x", expand=True)
        self.log = scrolledtext.ScrolledText(self.root, state="disabled", wrap="word", relief="flat", borderwidth=0,
                                             font=("Segoe UI", 11), padx=self.px(12), pady=self.px(10),
                                             spacing1=self.px(3), spacing3=self.px(3), highlightthickness=0)
        self.log.pack(fill="both", expand=True)
        self.log.tag_config("alert", font=("Segoe UI", 11, "bold"))
        self.emoji_px = round(tkfont.Font(root=self.root, font=("Segoe UI", 11)).metrics("linespace") * 0.85)
        self.emoji_img = {}  # sequence -> PhotoImage; Tk drops images nobody holds a reference to
        self.theme = CFG.get("theme") if CFG.get("theme") in THEMES else "dark"
        self.apply_theme()
        self.entry.bind("<Return>", self.submit)
        self.entry.focus_set()
        self.services = [cls(self) for cls in services]
        for s in self.services:
            threading.Thread(target=s.run, daemon=True).start()
        self.root.after(100, self.pump)

    def apply_theme(self):
        t = THEMES[self.theme]
        for w in (self.root, self.bar, self.top):
            w.configure(bg=t["bg"])
        self.counts.configure(bg=t["bg"], fg=t["text"])
        self.toggle.configure(text="☀  Light" if self.theme == "dark" else "☾  Dark",  # names the mode you switch to
                              bg=t["field"], fg=t["text"], activebackground=t["border"], activeforeground=t["text"])
        self.entry.configure(bg=t["field"], fg=t["text"], insertbackground=t["text"],
                             highlightbackground=t["border"], selectbackground=t["select"], selectforeground=t["text"])
        self.log.configure(bg=t["panel"], fg=t["text"], selectbackground=t["select"], selectforeground=t["text"])
        for tag in ("YT", "TW", "sys", "alert"):
            self.log.tag_config(tag, foreground=t[tag])

    def flip_theme(self):
        self.theme = "light" if self.theme == "dark" else "dark"
        self.apply_theme()
        CFG["theme"] = self.theme
        try:  # remember it for next launch
            (HERE / "config.json").write_text(json.dumps(CFG, indent=2), encoding="utf-8")
        except OSError as e:
            self.status(f"Couldn't save theme: {e}")

    # add/status are called from worker threads; only pump touches the widgets
    def add(self, tag, user, text):
        fetch_emoji(user + text)
        self.q.put((tag, f"[{tag}] {user}: ", text))

    def status(self, msg):
        if msg != self.last:  # don't repeat the same "waiting..." line
            self.last = msg
            self.q.put(("sys", "", msg))

    def alert(self, tag, title, body=""):
        fetch_emoji(title + body)
        self.alerts.put((tag, title, body))
        self.q.put((tag, f"[{tag}] ★ ", title + (f"  {body}" if body else "")))

    def popup(self, tag, title, body):
        w = tk.Toplevel(self.root)
        w.overrideredirect(True)  # no title bar
        w.attributes("-topmost", True)  # stay above the game/OBS
        px = self.px
        tk.Label(w, text=title, bg={"YT": "#cc0000", "TW": "#9146ff"}[tag], fg="white", font=("Segoe UI Semibold", 14),
                 wraplength=px(380), justify="left", anchor="w", padx=px(16), pady=px(12)).pack(fill="x")
        if body:
            t = THEMES[self.theme]
            tk.Label(w, text=body, bg=t["panel"], fg=t["text"], font=("Segoe UI", 11),
                     wraplength=px(380), justify="left", anchor="w", padx=px(16), pady=px(10)).pack(fill="x")
        slot = min(set(range(len(self.popups) + 1)) - set(self.popups.values()))
        self.popups[w] = slot
        w.geometry(f"+{w.winfo_screenwidth() - px(440)}+{px(40) + slot * px(150)}")

        def close(_event=None):
            self.popups.pop(w, None)
            w.destroy()
        w.bind("<Button-1>", close)  # click to dismiss early
        w.after(POPUP_MS, close)

    def pump(self):
        self.counts.configure(text=viewer_line([(s.tag, s.viewers) for s in self.services]))
        while not self.alerts.empty():
            self.popup(*self.alerts.get())
        if not self.q.empty():
            at_bottom = self.log.yview()[1] >= 0.999
            self.log.configure(state="normal")
            while not self.q.empty():
                tag, head, body = self.q.get()
                self.insert_rich(head, tag)
                self.insert_rich(body + "\n", "sys" if tag == "sys" else "alert" if "★" in head else ())
            self.log.configure(state="disabled")
            if at_bottom:
                self.log.see("end")
        self.root.after(100, self.pump)

    def insert_rich(self, text, style):
        """Insert text into the log with each emoji as an inline color image."""
        pos = 0
        for m in EMOJI_RE.finditer(text):
            img = self.emoji(m.group())
            if img:
                self.log.insert("end", text[pos:m.start()], style)
                self.log.image_create("end", image=img, align="center", padx=self.px(1))
                pos = m.end()
        self.log.insert("end", text[pos:], style)

    def emoji(self, seq):
        if seq not in self.emoji_img:
            if not _emoji_png.get(seq):
                return None  # not downloaded (yet): plain character this time
            im = Image.open(io.BytesIO(_emoji_png[seq])).convert("RGBA")
            self.emoji_img[seq] = ImageTk.PhotoImage(
                im.resize((self.emoji_px, self.emoji_px), Image.Resampling.LANCZOS), master=self.root)
        return self.emoji_img[seq]

    def submit(self, _event):
        text = clean(self.entry.get())
        if not text:
            return
        if len(text) > MAX_LEN:
            return self.status(f"Too long: {len(text)}/{MAX_LEN} characters (YouTube's limit)")
        self.entry.delete(0, "end")
        for s in self.services:
            threading.Thread(target=self.deliver, args=(s, text), daemon=True).start()

    def deliver(self, service, text):
        try:
            service.send(text)
        except Exception as e:
            self.status(f"{service.tag}: send failed ({e})")


def fail(msg):
    """The .exe has no console, so startup problems go in a dialog box."""
    messagebox.showerror("DualChat", msg)
    raise SystemExit(msg)


def main():
    windows_setup()
    path = HERE / "config.json"
    if not path.exists():
        fail(f"No config.json found in\n{HERE}\n\nCopy config.example.json to config.json and fill in "
             "your client IDs (README.md has the steps).")
    try:
        CFG.update(json.loads(path.read_text(encoding="utf-8-sig")))  # -sig: some editors add a BOM
    except ValueError as e:
        fail(f"config.json isn't valid JSON:\n{e}")
    services = [cls for name, cls in (("youtube", YouTube), ("twitch", Twitch))
                if not CFG.get(name, {}).get("client_id", "YOUR_").startswith("YOUR_")]
    if not services:
        fail("No platform set up in config.json yet (README.md has the steps).")
    App(services).root.mainloop()


def windows_setup():
    """Sharp text and our own taskbar icon (instead of python.exe's) on Windows."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor; stops Windows bitmap-stretching the window
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("DualChat")
    except (AttributeError, OSError):
        pass  # not Windows, or too old to have shcore


if __name__ == "__main__":
    main()
