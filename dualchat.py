"""DualChat: YouTube + Twitch live chat in one window, one box to reply to both."""
import codecs
import collections
import ctypes
import html
import json
import os
import queue
import re
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

import webview

# as an .exe, __file__ is in a temp unpack folder; config and tokens live next to the .exe
HERE = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
ASSETS = Path(getattr(sys, "_MEIPASS", HERE))  # bundled files (UI, icon) unpack here in the .exe
CONFIG = HERE / "config.json"
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


def _open_in_browser(name, url, code):
    webbrowser.open(url)


# the window replaces these so sign-in happens inside the app
DEVICE_PROMPT = _open_in_browser  # (platform, url, code) when a sign-in code is ready
DEVICE_DONE = lambda name: None  # (platform) once that sign-in succeeded

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
    DEVICE_PROMPT(name, url, d["user_code"])
    poll = {**creds, "device_code": d["device_code"],
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code"}
    if name == "twitch":
        poll["scopes"] = o["scope"]
    interval, deadline = d.get("interval", 5), time.time() + d.get("expires_in", 1800)
    while time.time() < deadline:
        time.sleep(interval)
        try:
            token = http(o["token"], form=poll)
            DEVICE_DONE(name)
            return token
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


# ---- Setup: keys, captured from the Google / Twitch pages or pasted in ----

PAGES = {
    "google_project": ("https://console.cloud.google.com/projectcreate",
                       "Sign in, then create a project (any name). Make sure it's selected at the top."),
    "google_api": ("https://console.cloud.google.com/apis/library/youtube.googleapis.com",
                   "Click Enable to turn on the YouTube Data API v3."),
    "google_auth": ("https://console.cloud.google.com/auth/overview/create",
                    "Any app name, your email, audience External, your email again, agree, Create."),
    "google_audience": ("https://console.cloud.google.com/auth/audience",
                        "Test users, Add users: the Google account that owns your channel, then Save."),
    "google_client": ("https://console.cloud.google.com/auth/clients/create",
                      "Type: TVs and Limited Input devices, then Create. DualChat saves the keys it shows."),
    "twitch_security": ("https://www.twitch.tv/settings/security",
                        "Turn on Two-Factor Authentication if it's off, then go back."),
    "twitch_register": ("https://dev.twitch.tv/console/apps/create",
                        "Any unique name, redirect URL http://localhost, category Chat Bot, client type Public, then Create."),
    "twitch_apps": ("https://dev.twitch.tv/console/apps",
                    "Click Manage on your DualChat app. DualChat saves its Client ID."),
}
GOOGLE_ID = r"\d{6,}-[a-z0-9]{20,}\.apps\.googleusercontent\.com"
GOOGLE_ID_RE = re.compile(rf"\b{GOOGLE_ID}\b")
GOOGLE_SECRET_RE = re.compile(r"\bGOCSPX-[A-Za-z0-9_-]{20,}")
TWITCH_APP_RE = re.compile(r"^https://dev\.twitch\.tv/console/apps/([a-z0-9]{30})(?:[/?#]|$)")


def capture_google(text):
    """The client-created dialog shows exactly one ID and one secret; anything else is ambiguous."""
    ids, secrets = set(GOOGLE_ID_RE.findall(text)), set(GOOGLE_SECRET_RE.findall(text))
    return (ids.pop(), secrets.pop()) if len(ids) == 1 and len(secrets) == 1 else None


def capture_twitch(url):
    m = TWITCH_APP_RE.match(url or "")
    return m and m.group(1)


def check_keys(platform, vals):
    """Validate pasted keys -> (dict to merge into CFG, error message or None)."""
    vals = {k: (v or "").strip() for k, v in vals.items()}
    if platform == "youtube":
        if not re.fullmatch(GOOGLE_ID, vals.get("client_id", "")):
            return None, "That Client ID doesn't look right. It ends in .apps.googleusercontent.com"
        secret = vals.get("client_secret") or CFG.get("youtube", {}).get("client_secret", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,}", secret):
            return None, "Paste the Client secret too (it usually starts with GOCSPX-)"
        return {"client_id": vals["client_id"], "client_secret": secret}, None
    if not re.fullmatch(r"[a-z0-9]{30}", vals.get("client_id", "")):
        return None, "A Twitch Client ID is 30 lowercase letters and numbers"
    if not re.fullmatch(r"[A-Za-z0-9_]{0,25}", vals.get("channel", "").lstrip("#")):
        return None, "Channel names only use letters, numbers and _"
    return {"client_id": vals["client_id"], "channel": vals.get("channel", "").lstrip("#").lower()}, None


def key(name, field):
    """A saved key, or "" while it's still config.example.json's YOUR_... placeholder."""
    v = CFG.get(name, {}).get(field) or ""
    return "" if v.startswith("YOUR_") else v


def is_ready(name):
    return bool(key(name, "client_id")) and (name != "youtube" or bool(key(name, "client_secret")))


def save_config():
    """Write config.json atomically, so a crash mid-write can't destroy saved keys."""
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(CFG, indent=2), encoding="utf-8")
    os.replace(tmp, CONFIG)


FILL_CODE_JS = r"""
(code => {
  const i = [...document.querySelectorAll('input')].find(x => x.offsetParent && /^(text|tel|)$/.test(x.type));
  if (!i || i.value) return false;
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(i, code);
  i.dispatchEvent(new Event('input', {bubbles: true}));
  return true;
})
"""


# ---- Window ----

THEMES = {  # header colors, also used for the Windows title bar so the two blend together
    "dark": {"hdr": (0x1A, 0x14, 0x24), "text": (0xEF, 0xEA, 0xF7), "bg": "#0F0D14", "panel": "#16131D",
             "fg": "#EFEAF7", "ok": "#3DDC84"},
    "light": {"hdr": (0xF3, 0xEE, 0xFB), "text": (0x1B, 0x17, 0x24), "bg": "#FAF8FD", "panel": "#FFFFFF",
              "fg": "#1B1724", "ok": "#1E9E57"},
}
POPUP_W, POPUP_SLOT = 420, 150
RESIZE_EDGES = {"top": 3, "topleft": 4, "topright": 5}  # WMSZ_* codes; sides and bottom keep the native border


def hex_color(rgb):
    return "#%02X%02X%02X" % rgb


# ---- Custom title bar (Windows) ----
# The window keeps its normal resizable frame, so snapping, Win+arrows, the shadow and side/bottom resizing all
# still work; a window-procedure hook only drops the caption strip, and the page draws its own header and buttons.

class _NCCALCSIZE_PARAMS(ctypes.Structure):
    _fields_ = [("rgrc", wintypes.RECT * 3), ("lppos", ctypes.c_void_p)]


_SUBCLASSPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                   wintypes.LPARAM, ctypes.c_size_t, ctypes.c_size_t)
if sys.platform == "win32":
    _user32, _comctl32 = ctypes.windll.user32, ctypes.windll.comctl32
    _comctl32.DefSubclassProc.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _comctl32.DefSubclassProc.restype = ctypes.c_ssize_t
    _comctl32.SetWindowSubclass.argtypes = [wintypes.HWND, _SUBCLASSPROC, ctypes.c_size_t, ctypes.c_size_t]
    _user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND] + [ctypes.c_int] * 4 + [wintypes.UINT]


def _no_caption(hwnd, msg, wparam, lparam, _id, _ref):
    if msg == 0x0083 and wparam:  # WM_NCCALCSIZE: let Windows size the borders, then give the caption to the page
        rect = ctypes.cast(lparam, ctypes.POINTER(_NCCALCSIZE_PARAMS)).contents.rgrc[0]
        left, top = rect.left, rect.top
        result = _comctl32.DefSubclassProc(hwnd, msg, wparam, lparam)
        # a maximized window hangs one border-width off every screen edge; keep the top of the page on screen
        rect.top = top + (rect.left - left if _user32.IsZoomed(hwnd) else 0)
        return result
    return _comctl32.DefSubclassProc(hwnd, msg, wparam, lparam)


_no_caption_proc = _SUBCLASSPROC(_no_caption)  # module-level: Windows calls it for the window's lifetime


def enable_drag_regions():
    """Let CSS `app-region: drag` move the window natively (with snapping and double-click to maximize).
    pywebview applies its WebView2 settings in on_webview_ready, before the first page loads; add ours there."""
    try:
        from webview.platforms import edgechromium
    except Exception:
        return  # not Windows / no WebView2: the page simply isn't draggable
    original = edgechromium.EdgeChrome.on_webview_ready

    def on_webview_ready(self, sender, args):
        if args.IsSuccess:
            sender.CoreWebView2.Settings.IsNonClientRegionSupportEnabled = True
        original(self, sender, args)
    edgechromium.EdgeChrome.on_webview_ready = on_webview_ready


class SetupBar:
    """Native strip docked across the top while a Google / Twitch setup page shows. The page gets the rest of the
    window, so nothing on it is covered, and it resizes with the window. It is the title bar there: drag it,
    double-click it, resize from its top edge. Built from WinForms on the window's own thread."""
    HOVER = {"dark": (0x2A, 0x23, 0x37), "light": (0xEE, 0xE8, 0xF7)}
    BORDER = {"dark": (0x4A, 0x41, 0x5E), "light": (0xC9, 0xC0, 0xD8)}

    def __init__(self, app):
        import System.Drawing as SD
        import System.Windows.Forms as WF
        self.SD, self.WF, self.app = SD, WF, app
        self.form, self.hwnd, self.last_down = app.window.native, app.hwnd(), (0, 0, 0)
        scale = _user32.GetDpiForWindow(self.hwnd) / 96
        self.px = px = lambda n: int(round(n * scale))
        names = {f.Name for f in SD.FontFamily.Families}
        self.glyphs = SD.Font("Segoe Fluent Icons" if "Segoe Fluent Icons" in names else "Segoe MDL2 Assets", 7.5)
        self.icon_font = SD.Font(self.glyphs.FontFamily, 9.0)

        self.bar = WF.Panel(); self.bar.Dock = WF.DockStyle.Top; self.bar.Height = px(46); self.bar.Visible = False
        self.label = WF.Label(); self.label.AutoEllipsis = True
        self.label.TextAlign = SD.ContentAlignment.MiddleLeft; self.label.Padding = WF.Padding(px(12), 0, px(6), 0)
        self.label.Font = SD.Font("Segoe UI", 9.75)
        self.tip = WF.ToolTip()
        self.accent = WF.Panel(); self.accent.Dock = WF.DockStyle.Bottom; self.accent.Height = px(2)
        self.accent.Paint += self.paint_accent
        self.accent.Resize += lambda s, e: self.accent.Invalidate()

        self.close = self.window_button("", "close", "Close")
        self.max = self.window_button("", "max", "Maximize")
        self.min = self.window_button("", "min", "Minimize")
        self.back = self.text_button("◀  Back", lambda: self.app.go_home(), "Back to DualChat")
        self.external = self.text_button("Open In Browser", self.open_external, "Open this page in your normal browser")
        self.external.TextImageRelation = WF.TextImageRelation.TextBeforeImage
        self.actions = (self.external, self.back)  # left to right
        for c in (self.label, self.close, self.max, self.min) + self.actions + (self.accent,):
            self.bar.Controls.Add(c)
        for c in (self.bar, self.label):
            c.MouseDown += self.on_mouse_down
            c.MouseMove += self.on_mouse_move
        self.bar.Resize += lambda s, e: self.layout()
        self.form.Controls.Add(self.bar)  # behind the page in z-order, so it docks first and the page fills the rest
        self.form.Resize += lambda s, e: self.update_max()

    def layout(self):
        """One row when there's room; otherwise the step text (and, narrowest, the action buttons) drop to
        their own rows. The bar grows to fit and the page below shrinks to match, so nothing overlaps."""
        SD, px, W, row = self.SD, self.px, self.bar.Width, self.px(44)
        x = W
        for b in (self.close, self.max, self.min):  # window buttons: always the top-right corner
            x -= b.Width
            b.Location = SD.Point(x, 0)
        acts = [b for b in self.actions if b.Visible]
        acts_w = sum(b.Width + px(8) for b in acts)
        if x - acts_w >= px(260):  # wide: text | actions | window buttons
            mode, ax, ay = 1, x - acts_w, 0
        elif x - acts_w >= px(8):  # medium: actions beside the window buttons, text underneath
            mode, ax, ay = 2, x - acts_w, 0
        else:  # narrowest: window buttons, then actions on their own row, then text
            mode, ax, ay = 3, px(12), row
        for b in acts:
            b.Location = SD.Point(ax, ay + (row - b.Height) // 2)
            ax += b.Width + px(8)
        if mode == 1:
            self.label.AutoEllipsis = True
            self.label.Bounds = SD.Rectangle(0, 0, x - acts_w, row)
            height = row
        else:
            top = row * (mode - 1)
            self.label.AutoEllipsis = False  # wraps onto as many lines as it needs
            need = self.WF.TextRenderer.MeasureText(self.label.Text or " ", self.label.Font, SD.Size(W - px(18), 0),
                                                   self.WF.TextFormatFlags.WordBreak).Height
            self.label.Bounds = SD.Rectangle(0, top, W, need + px(14))
            height = top + need + px(14)
        height += self.accent.Height
        if self.bar.Height != height:
            self.bar.Height = height

    def window_button(self, glyph, cmd, name):
        b = self.base_button(glyph, lambda: self.app.window_cmd(cmd), name)
        b.Font, b.Size, b.Margin = self.glyphs, self.SD.Size(self.px(46), self.px(44)), self.WF.Padding(0)
        b.FlatAppearance.BorderSize = 0
        return b

    def text_button(self, text, action, tip):
        b = self.base_button(text, action, tip)
        b.Font = self.SD.Font("Segoe UI Semibold", 9.0)
        b.AutoSize, b.AutoSizeMode = True, self.WF.AutoSizeMode.GrowAndShrink
        b.Padding, b.Margin = self.WF.Padding(self.px(6), 0, self.px(6), 0), self.WF.Padding(0, self.px(8), self.px(8), self.px(8))
        return b

    def base_button(self, text, action, tip):
        b = self.WF.Button()
        b.Text, b.AccessibleName, b.FlatStyle, b.Cursor = text, tip, self.WF.FlatStyle.Flat, self.WF.Cursors.Hand
        b.TabStop = False
        b.Click += lambda s, e: threading.Thread(target=action, daemon=True).start()  # keep the window's thread free
        self.tip.SetToolTip(b, tip)
        return b

    def open_external(self):
        webbrowser.open(self.app.window.get_current_url() or "")

    def paint_accent(self, sender, e):
        r = sender.ClientRectangle
        if r.Width > 0 and r.Height > 0:
            from System.Drawing.Drawing2D import LinearGradientBrush
            brush = LinearGradientBrush(r, self.SD.Color.FromArgb(0xE6, 0x21, 0x17), self.SD.Color.FromArgb(0x91, 0x46, 0xFF), 0.0)
            e.Graphics.FillRectangle(brush, r)
            brush.Dispose()

    def glyph_image(self, glyph, color):
        """Icons can't share a button's text font, so the Open In Browser icon is drawn into a small image."""
        from System.Drawing.Text import TextRenderingHint
        size = self.px(16)
        bmp = self.SD.Bitmap(size, size)
        g = self.SD.Graphics.FromImage(bmp)
        g.TextRenderingHint = TextRenderingHint.AntiAliasGridFit
        brush = self.SD.SolidBrush(color)
        g.DrawString(glyph, self.icon_font, brush, self.SD.PointF(0, self.px(1)))
        brush.Dispose(); g.Dispose()
        return bmp

    def apply_theme(self, theme):
        C = lambda rgb: self.SD.Color.FromArgb(*rgb)
        t = THEMES[theme]
        hdr, text, hover, border = C(t["hdr"]), C(t["text"]), C(self.HOVER[theme]), C(self.BORDER[theme])
        for c in (self.bar, self.label):
            c.BackColor = hdr
        self.label.ForeColor = text
        for b in (self.min, self.max, self.close, self.external):
            b.BackColor, b.ForeColor = hdr, text
            b.FlatAppearance.MouseOverBackColor = b.FlatAppearance.MouseDownBackColor = hover
        self.external.FlatAppearance.BorderColor = border
        self.close.FlatAppearance.MouseOverBackColor = self.close.FlatAppearance.MouseDownBackColor = C((0xE8, 0x11, 0x23))
        self.back.BackColor, self.back.ForeColor = C((0x91, 0x46, 0xFF)), C((0xFF, 0xFF, 0xFF))
        self.back.FlatAppearance.BorderSize = 0
        self.back.FlatAppearance.MouseOverBackColor = self.back.FlatAppearance.MouseDownBackColor = C((0xA2, 0x66, 0xFF))
        self.external.Image = self.glyph_image("", text)  # "open in new window"

    def show(self, page, text):
        self.apply_theme(self.app.theme)
        self.set_text(text)
        self.update_max()
        self.bar.Visible = True
        self.layout()

    def hide(self):
        self.bar.Visible = False

    def set_text(self, text):
        if self.label.Text != text:
            self.label.Text = text
            self.tip.SetToolTip(self.label, text)  # full text when the window is too narrow for it
            self.layout()

    def update_max(self):
        zoomed = bool(_user32.IsZoomed(self.hwnd))
        self.max.Text = "" if zoomed else ""
        self.max.AccessibleName = "Restore" if zoomed else "Maximize"
        self.tip.SetToolTip(self.max, self.max.AccessibleName)

    def on_mouse_move(self, sender, e):
        top = sender.Top + e.Y if sender is self.label else e.Y
        edge = top < self.px(4) and not _user32.IsZoomed(self.hwnd)
        sender.Cursor = self.WF.Cursors.SizeNS if edge else self.WF.Cursors.Default

    def on_mouse_down(self, sender, e):
        if e.Button != self.WF.MouseButtons.Left:
            return
        top = sender.Top + e.Y if sender is self.label else e.Y
        now, (t0, x0, y0) = time.monotonic(), self.last_down
        pos = self.WF.Control.MousePosition
        self.last_down = (now, pos.X, pos.Y)
        # the move loop below swallows Windows' own double-click detection, so spot double-clicks here
        if now - t0 < self.WF.SystemInformation.DoubleClickTime / 1000 and abs(pos.X - x0) < 6 and abs(pos.Y - y0) < 6:
            self.last_down = (0, 0, 0)
            threading.Thread(target=self.app.window_cmd, args=("max",), daemon=True).start()
            return
        _user32.ReleaseCapture()
        if top < self.px(4) and not _user32.IsZoomed(self.hwnd):
            _user32.SendMessageW(self.hwnd, 0x0112, 0xF003, 0)  # WM_SYSCOMMAND SC_SIZE + top edge
        else:
            _user32.SendMessageW(self.hwnd, 0x00A1, 2, 0)  # WM_NCLBUTTONDOWN on HTCAPTION: Windows moves it, with snapping


class Api:
    """Called from ui.html. Setup pages from Google and Twitch load in this same window and can see these
    methods too, so every call is refused unless DualChat's own page is the one showing."""

    def __init__(self, app):
        self._app = app

    def _home(self):
        return self._app.at_home()

    def hello(self):
        if self._home():
            return self._app.hello()

    def poll(self, after=0):
        if self._home():
            return self._app.poll(after)

    def send(self, text):
        if self._home():
            self._app.send(text)

    def toggle_theme(self):
        if self._home():
            self._app.toggle_theme()

    def window_cmd(self, cmd):
        if self._home() and cmd in ("min", "max", "close"):
            self._app.window_cmd(cmd)

    def begin_resize(self, edge):
        if self._home() and edge in RESIZE_EDGES:
            self._app.begin_resize(edge)

    def open_page(self, key):
        if self._home() and key in PAGES:
            self._app.browse(key)

    def open_signin(self, platform):
        if self._home() and platform in self._app.signins:
            self._app.browse("signin:" + platform)

    def open_signin_external(self, platform):
        if self._home() and platform in self._app.signins:
            webbrowser.open(self._app.signins[platform][0])

    def save_keys(self, platform, vals):
        if self._home() and platform in OAUTH and isinstance(vals, dict):
            return self._app.save_keys(platform, vals)

    def finish_setup(self):
        if self._home():
            return self._app.finish_setup()


class PopupApi:
    def __init__(self):
        self._window = None

    def close(self):
        window, self._window = self._window, None  # closed by click and by timer: only destroy once
        if window:
            window.destroy()

    def fit(self, height):
        if self._window:
            self._window.resize(POPUP_W, min(int(height), POPUP_SLOT - 10))


class App:
    def __init__(self, config_note=None):
        self.events, self.seq, self.lock = collections.deque(maxlen=800), 0, threading.Lock()
        self.services, self.started, self.last = [], set(), None
        self.signins, self.popups = {}, {}  # platform -> (url, code); popup window -> stacking slot
        self.mode, self.page, self.origin, self.home_url, self._bar = "home", None, None, None, None
        self.theme = CFG.get("theme") if CFG.get("theme") in THEMES else "dark"
        self.config_note = config_note
        global DEVICE_PROMPT, DEVICE_DONE
        DEVICE_PROMPT, DEVICE_DONE = self.device_prompt, self.device_done
        self.window = webview.create_window(
            "DualChat", url=str(ASSETS / "ui.html"), js_api=Api(self), width=560, height=780, min_size=(420, 520),
            background_color=hex_color(THEMES[self.theme]["hdr"]))
        self.window.events.loaded += self.on_loaded
        self.window.events.shown += self.on_shown

    def run(self):
        enable_drag_regions()
        storage = Path(os.environ.get("LOCALAPPDATA", HERE)) / "DualChat" / "webview"  # setup-page sign-ins
        webview.start(http_server=True, private_mode=False, storage_path=str(storage),
                      icon=str(ASSETS / "dualchat.ico"))

    def on_shown(self):
        self.paint_title_bar()
        self.strip_title_bar()

    def hwnd(self):
        return int(self.window.native.Handle.ToInt64())

    def on_ui(self, fn):
        """Run fn on the window's own thread (Windows only lets that thread hook or size the window)."""
        from System import Action
        self.window.native.BeginInvoke(Action(fn))

    def strip_title_bar(self):
        if sys.platform != "win32":
            return
        hwnd = self.hwnd()

        def apply():
            _comctl32.SetWindowSubclass(hwnd, _no_caption_proc, 1, 0)
            _user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x37)  # NOSIZE|NOMOVE|NOZORDER|NOACTIVATE|FRAMECHANGED
        self.on_ui(apply)

    def window_cmd(self, cmd):
        if cmd == "close":
            self.window.destroy()
        elif cmd == "min":
            self.window.minimize()
        elif self.maximized():
            self.window.restore()
        else:
            self.window.maximize()

    def maximized(self):
        try:
            return bool(_user32.IsZoomed(self.hwnd()))
        except Exception:
            return False

    def begin_resize(self, edge):
        """The page covers the old caption, so the top edge resizes through here: hand the drag to Windows."""
        hwnd = self.hwnd()
        self.on_ui(lambda: (_user32.ReleaseCapture(),
                            _user32.SendMessageW(hwnd, 0x0112, 0xF000 + RESIZE_EDGES[edge], 0)))  # WM_SYSCOMMAND SC_SIZE

    # -- the window's own page vs. setup pages --

    def on_loaded(self):
        url = self.window.get_current_url() or ""
        if self.origin is None and url.startswith("http://127.0.0.1"):
            self.home_url = url
            self.origin = "/".join(url.split("/")[:3]) + "/"  # http://127.0.0.1:<port>/
            if self.config_note:
                self.status(self.config_note)
            self.start_services()

    def at_home(self):
        url = self.window.get_current_url() or ""
        return bool(self.origin) and url.startswith(self.origin)

    def browse(self, page):
        self.mode, self.page = "browse", page
        url = self.signins[page.split(":")[1]][0] if page.startswith("signin:") else PAGES[page][0]
        text = self.bar_text(page)
        self.on_ui(lambda: self.setup_bar().show(page, text))
        self.window.load_url(url)
        threading.Thread(target=self.watch_page, daemon=True).start()

    def go_home(self):
        self.mode, self.page = "home", None
        self.on_ui(lambda: self._bar and self._bar.hide())
        self.window.load_url(self.home_url)

    def setup_bar(self):
        """Created on first use, on the window's thread."""
        if self._bar is None:
            self._bar = SetupBar(self)
        return self._bar

    def watch_page(self):
        """While a setup page shows: keep the bar's text current, fill in sign-in codes, capture keys."""
        page, filled, left, shown = self.page, False, False, None
        while self.mode == "browse" and self.page == page:
            try:
                url = self.window.get_current_url() or ""
                if self.at_home():
                    if left:  # the page itself navigated back to DualChat
                        self.mode = "home"
                        self.on_ui(lambda: self._bar and self._bar.hide())
                        return
                    time.sleep(0.2)  # setup page hasn't started loading yet
                    continue
                left = True
                if page == "signin:youtube" and not filled and "google" in url:
                    filled = bool(self.window.evaluate_js(f"{FILL_CODE_JS}({json.dumps(self.signins.get('youtube', ('', ''))[1])})"))
                self.capture(url)
                text = self.bar_text(page)
                if text != shown:
                    shown = text
                    self.on_ui(lambda t=text: self._bar and self._bar.set_text(t))
            except Exception:
                pass  # page is mid-navigation; try again next tick
            time.sleep(1)

    def capture(self, url):
        if "console.cloud.google.com" in url:
            found = capture_google(self.window.evaluate_js("document.body ? document.body.innerText : ''") or "")
            if found and (CFG.get("youtube", {}).get("client_id"), CFG.get("youtube", {}).get("client_secret")) != found:
                CFG.setdefault("youtube", {}).update(client_id=found[0], client_secret=found[1])
                save_config()
                self.status("YouTube keys captured and saved.")
        tid = capture_twitch(url)
        if tid and CFG.get("twitch", {}).get("client_id") != tid:
            CFG.setdefault("twitch", {}).update(client_id=tid)
            CFG["twitch"].setdefault("channel", "")
            save_config()
            self.status("Twitch Client ID captured and saved.")

    def bar_text(self, page):
        if page.startswith("signin:"):
            name = page.split(":")[1]
            return (f"Sign in to {'YouTube' if name == 'youtube' else 'Twitch'}, then approve DualChat.   "
                    f"Code: {self.signins.get(name, ('', ''))[1]}")
        platform = "twitch" if "twitch" in page else "youtube"
        got = f"   ✓ {dict(youtube='YouTube', twitch='Twitch')[platform]} keys saved" if is_ready(platform) else ""
        return PAGES[page][1] + got

    # -- ui.html calls these (through Api) --

    def hello(self):
        with self.lock:
            return {"view": "chat" if self.started or any(map(is_ready, OAUTH)) else "setup",
                    "events": [e for _, e in self.events], "seq": self.seq}

    def poll(self, after):
        with self.lock:
            new = [e for s, e in self.events if s > after]
            seq = self.seq
        return {"events": new, "seq": seq, "state": self.state()}

    def state(self):
        running = {s.tag: s for s in self.services}
        platforms = [{"tag": tag, "viewers": running[tag].viewers if tag in running else None,
                      "connected": tag in running and bool(getattr(running[tag], "online", None)
                                                           or getattr(running[tag], "chat_id", None))}
                     for name, tag in (("youtube", "YT"), ("twitch", "TW")) if is_ready(name) or tag in running]
        return {"theme": self.theme, "running": bool(self.services), "platforms": platforms,
                "maximized": self.maximized(),
                "keys": {"youtube": {"ready": is_ready("youtube"), "client_id": key("youtube", "client_id"),
                                     "has_secret": bool(key("youtube", "client_secret"))},  # the secret never goes to the page
                         "twitch": {"ready": is_ready("twitch"), "client_id": key("twitch", "client_id"),
                                    "channel": key("twitch", "channel")}}}

    def save_keys(self, platform, vals):
        keys, error = check_keys(platform, vals)
        if error:
            return {"ok": False, "error": error}
        CFG.setdefault(platform, {}).update(keys)
        save_config()
        return {"ok": True}

    def finish_setup(self):
        if not any(map(is_ready, OAUTH)):
            return {"ok": False}
        self.start_services()
        return {"ok": True}

    def start_services(self):
        for name, cls in (("youtube", YouTube), ("twitch", Twitch)):
            if is_ready(name) and name not in self.started:
                self.started.add(name)
                s = cls(self)
                self.services.append(s)
                threading.Thread(target=s.run, daemon=True).start()

    def send(self, text):
        text = clean(text)
        if not text:
            return
        if len(text) > MAX_LEN:
            return self.status(f"Too long: {len(text)}/{MAX_LEN} characters (YouTube's limit)")
        for s in self.services:
            threading.Thread(target=self.deliver, args=(s, text), daemon=True).start()

    def deliver(self, service, text):
        try:
            service.send(text)
        except Exception as e:
            self.status(f"{service.tag}: send failed ({e})")

    def toggle_theme(self):
        self.theme = "light" if self.theme == "dark" else "dark"
        CFG["theme"] = self.theme
        try:  # remember it for next launch
            save_config()
        except OSError as e:
            self.status(f"Couldn't save theme: {e}")
        self.paint_title_bar()

    def paint_title_bar(self):
        """Color the Windows title bar to match the header (Windows 11; older Windows ignores it).
        Returns the DWM result codes, 0 meaning applied."""
        try:
            hwnd = int(self.window.native.Handle.ToInt64())
            t = THEMES[self.theme]
            codes = []
            for attr, value in ((20, int(self.theme == "dark")),  # dark title-bar buttons
                                (35, t["hdr"][0] | t["hdr"][1] << 8 | t["hdr"][2] << 16),  # caption
                                (34, t["hdr"][0] | t["hdr"][1] << 8 | t["hdr"][2] << 16),  # border
                                (36, t["text"][0] | t["text"][1] << 8 | t["text"][2] << 16)):  # title text
                v = ctypes.c_uint(value)
                codes.append(ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), 4))
            return codes
        except Exception:
            return None

    # -- services and sign-in call these from worker threads --

    def push(self, event):
        with self.lock:
            self.seq += 1
            self.events.append((self.seq, event))

    def add(self, tag, user, text):
        self.push({"type": "msg", "tag": tag, "user": user, "text": text})

    def status(self, msg):
        if msg != self.last:  # don't repeat the same "waiting..." line
            self.last = msg
            self.push({"type": "status", "text": msg})

    def alert(self, tag, title, body=""):
        self.push({"type": "alert", "tag": tag, "title": title, "body": body})
        threading.Thread(target=self.popup, args=(tag, title, body), daemon=True).start()

    def device_prompt(self, name, url, code):
        self.signins[name] = (url, code)
        self.push({"type": "signin", "platform": name, "code": code})

    def device_done(self, name):
        self.signins.pop(name, None)
        self.push({"type": "signedin", "platform": name})
        if self.page == "signin:" + name:
            self.go_home()

    def popup(self, tag, title, body):
        """Always-on-top alert in the top-right corner for POPUP_MS; click to close early."""
        api = PopupApi()
        with self.lock:
            slot = min(set(range(len(self.popups) + 1)) - set(self.popups.values()))
            self.popups[api] = slot
        t = THEMES[self.theme]
        page = (f'<body style="margin:0;cursor:pointer;font-family:Segoe UI,sans-serif;background:{t["panel"]};overflow:hidden" '
                f'onclick="pywebview.api.close()"><div style="background:{"#CC0000" if tag == "YT" else "#9146FF"};'
                f'color:#fff;padding:12px 16px;font-weight:600;font-size:17px">{html.escape(title)}</div>'
                + (f'<div style="color:{t["fg"]};padding:10px 16px;font-size:14px">{html.escape(body)}</div>' if body else "")
                + '<script>window.addEventListener("pywebviewready",()=>pywebview.api.fit(document.body.scrollHeight))</script></body>')
        screen = webview.screens[0]
        api._window = webview.create_window(
            "DualChat alert", html=page, js_api=api, width=POPUP_W, height=POPUP_SLOT - 10,
            x=screen.width - POPUP_W - 20, y=20 + slot * POPUP_SLOT, frameless=True, on_top=True, focus=False,
            resizable=False, background_color=t["panel"])
        time.sleep(POPUP_MS / 1000)
        api.close()
        with self.lock:
            self.popups.pop(api, None)


def load_config():
    """-> a note for the chat if config.json couldn't be read (it's set aside, never overwritten)."""
    if not CONFIG.exists():
        return None
    try:
        CFG.update(json.loads(CONFIG.read_text(encoding="utf-8-sig")))  # -sig: some editors add a BOM
    except ValueError as e:
        kept = CONFIG.with_name(f"config.broken-{int(time.time())}.json")
        os.replace(CONFIG, kept)
        return f"config.json couldn't be read ({e}), so it was renamed to {kept.name}. Set up your keys again."
    return None


def main():
    windows_setup()
    App(load_config()).run()


def windows_setup():
    """Sharp text and our own taskbar icon (instead of python.exe's) on Windows."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor; stops Windows bitmap-stretching the window
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("DualChat")
    except (AttributeError, OSError):
        pass  # not Windows, or too old to have shcore


if __name__ == "__main__":
    main()
