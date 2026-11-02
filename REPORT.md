# DualChat: Project Report

DualChat is a Windows desktop app for streamers who go live on YouTube and Twitch at the same time. It merges both live chats into one window and sends whatever you type to both platforms at once. It also shows live viewer counts and pops up alerts for new subscribers, members and followers.

This report covers what the app does, how it's built, the decisions behind it, the problems found while testing it on a real stream, and what it still can't do.

---

## 1. Features

| Feature | YouTube | Twitch |
|---|---|---|
| Read live chat | Yes, one long-lived streaming connection | Yes, IRC over TLS |
| Send chat (one box for both) | Yes | Yes |
| Live viewer count | Every 30 s | Every 10 s |
| Subscriber alerts | New public subscribers | Subs, resubs, gift subs, Prime |
| Member alerts | New members, gifted memberships, milestones | n/a |
| Follow alerts | n/a | Every 10 s |
| Color emoji in chat | Yes | Yes |
| Light / dark theme | Toggle in the top bar, remembered between runs | |

Other details:

- **Alert popups** sit on top of other windows in the top-right corner for 12 seconds, stack when several arrive together, and close on click. Each alert also appears as a highlighted line in chat.
- **Gift bombs** show as one summary popup instead of one popup per gifted sub.
- **Sharp text** on high-DPI displays (125%, 150%, and so on), since Windows doesn't blur the window.
- **Distribution:** a single `DualChat.exe` with its own icon. Nothing else needs to be installed.

---

## 2. Architecture

```
            ┌──────────────── DualChat.exe (one process) ────────────────┐
            │                                                            │
 YouTube ◄──┤  YouTube thread ── chat stream (streamList)                │
 Data API   │  YouTube stats thread ── viewers + subscribers every 30 s  │
            │                         │                                  │
            │                         ▼                                  │
            │                 thread-safe queues ──► Tk window (main     │
            │                         ▲               thread, pumps      │
            │                         │               every 100 ms)      │
 Twitch  ◄──┤  Twitch thread ── IRC socket (read + send)                 │
 IRC/API    │  Twitch stats thread ── viewers + follows every 10 s       │
            └────────────────────────────────────────────────────────────┘
```

- **One file, standard library first.** All logic lives in `dualchat.py`, which uses only the Python standard library plus Pillow (for emoji images). Network calls use `urllib`, and the UI uses Tkinter.
- **The UI thread never touches the network.** Worker threads put messages on queues, and the Tk main loop drains them every 100 ms. Sending also runs on short-lived worker threads, so a slow API call can't freeze the window.
- **All Twitch socket traffic stays on one thread.** Instead of sharing the TLS socket between the reader and the send button, outgoing messages go into an outbox queue. The reader thread sends them between short (0.5 s) reads, which avoids using one TLS connection from two threads at once.
- **Each platform runs separately.** If a platform has no client ID in `config.json`, it is simply skipped. Each worker has its own reconnect loop with back-off, so one platform failing never affects the other.

### Sign-in (OAuth 2.0 device flow)

Both platforms use the **device authorization grant**. The app shows a short code, opens the sign-in page, and polls until the user approves. One small function handles both Google and Twitch, since the two only differ in a few field names (`verification_url` vs `verification_uri`, `error` vs `message`).

- Tokens are saved to `tokens.json` next to the app and refreshed automatically before they expire.
- Google doesn't send a new refresh token on refresh, so the existing one is kept.
- A rejected refresh falls back to signing in again.
- Sign-ins happen one platform at a time, so the user is never asked for two codes at once.

The device flow was chosen because it needs no client secret for Twitch (it's a public client), no local web server, and no redirect handling.

---

## 3. Platform details and API budget

### YouTube

- **Finding the stream:** `liveBroadcasts.list` with `broadcastStatus=active`, checked every 60 s while offline.
- **Reading chat:** `liveChatMessages.streamList` over HTTP (`GET /youtube/v3/liveChat/messages/stream`). It's a single long-lived connection that pushes new messages as they arrive. It costs **5 quota units per connection**, however long the connection stays open, instead of 5 units every few seconds when polling. The app resumes after a reconnect using the last `nextPageToken`. It never reconnects in a tight loop: if a connection lasts under 5 seconds, it waits before trying again.
- **Viewers:** `videos.list` (`liveStreamingDetails.concurrentViewers`), 1 unit every 30 s.
- **Subscribers:** `subscriptions.list` with `myRecentSubscribers=true`, 1 unit every 30 s.
- **Sending:** `POST /youtube/v3/liveChat/messages`, 20 units per message.

**Typical use:** roughly 250–350 units per streaming hour, against a default daily quota of 10,000 units. That's about 25+ hours of streaming a day before hitting the limit. The earlier polling version used about 2,000 units an hour.

### Twitch

- **Chat:** IRC at `irc.chat.twitch.tv:6697` (TLS), with the `tags` and `commands` capabilities turned on. These give display names and `USERNOTICE` events (subs, resubs, gifts).
- **Viewers:** Helix `GET /streams`.
- **Follows:** Helix `GET /channels/followers`, which needs the `moderator:read:followers` scope.
- **Scopes requested:** `chat:read chat:edit moderator:read:followers`.

### Alert logic

- **No alerts for old events at startup:** the first fetch of followers and subscribers only records who is already there, and later fetches alert on anyone new.
- **YouTube chat history:** the stream replays recent chat when it connects. To avoid alerting on old member events, each message's `publishedAt` time is compared with when the app connected.
- **Twitch gift bombs:** individual gifts that are part of a mystery gift (they carry `msg-param-community-gift-id`) are skipped, since the summary event already announces them.

---

## 4. Problems found during live testing and how they were fixed

Each of these appeared only against the real services, after the local tests had passed.

| # | Symptom | Root cause | Fix |
|---|---|---|---|
| 1 | YouTube chat never appeared, and no error was shown | The streaming endpoint rejects `alt=sse` (HTTP 400). Its error body is a **JSON array**, not an object, so the error handler itself crashed and silently ended the YouTube thread. | Dropped `alt=sse`; the endpoint streams a chunked JSON array, which the parser already handles. The error parser now unwraps arrays, so future errors show up in the window. |
| 2 | Sending to YouTube failed with HTTP 404 | The send address had moved from `/liveChatMessages` to `/liveChat/messages`. | Updated the path. The fix was checked by sending an empty request (expecting 400, not 404), so nothing was posted to chat. |
| 3 | YouTube viewers showed "offline" during a live stream | YouTube **leaves `concurrentViewers` out entirely** when nobody is watching. | A stream that has started and not ended, with no count, now shows 0. |
| 4 | The .exe said `config.json` was invalid when it wasn't | Some editors save UTF-8 with a byte-order mark (BOM), which Python's default reader rejects. | Read the config as `utf-8-sig`. |
| 5 | Blurry text on a 125% display | Without DPI awareness, Windows draws the window small and stretches the image. | The app now declares per-monitor DPI awareness, and every pixel size is scaled by the real DPI. Fonts already scale on their own. |
| 6 | Emoji showed in black and white, and skin tones or flags came apart | Tk on Windows draws text without color emoji or emoji joining. Pillow's Windows build can't join sequences either. | Emoji are matched as whole sequences (flags, keycaps, skin tones, ZWJ joins, subdivision flags) and shown as inline Twemoji images. Each image is downloaded once on the worker thread, and the app falls back to plain text when offline. |
| 7 | The .exe couldn't find its config | In a PyInstaller one-file build, `__file__` points into a temporary unpack folder. | The app reads and writes `config.json` and `tokens.json` next to `sys.executable`, and loads bundled files (the icon) from `sys._MEIPASS`. |

**Twitch viewer lag:** Twitch's official viewer count comes from a cache on Twitch's side, so it can trail the count on Twitch's own player by a minute or two. There's no faster official source. The app only uses documented, official APIs.

---

## 5. Security and privacy

- **No secrets in the repository.** `config.json` (client IDs and the Google client secret) and `tokens.json` (access and refresh tokens) are in `.gitignore`. A placeholder `config.example.json` is shipped instead.
- **Least-privilege scopes.** Google uses the `youtube` scope, needed to post chat. Twitch only requests chat read/write and follower read access.
- **IRC injection blocked.** Typed text is collapsed to a single line before sending, so a newline can't sneak in a raw IRC command. This is covered by a test.
- **Length limit.** Messages are capped at 200 characters (YouTube's limit) and rejected with a message instead of being cut off without warning.
- **Tokens are stored in plain text** next to the app, protected only by Windows file permissions. That's acceptable for a single-user desktop tool. See the improvements list below.
- **Emoji downloads** go to the jsDelivr CDN (Twemoji). They reveal which emoji appear in chat, and nothing else.

---

## 6. Testing

- **Unit tests** (`python -m unittest test_dualchat`), 9 in total:
  - IRC parsing, including tags with escapes and `USERNOTICE` events.
  - IRC injection blocking.
  - Token refresh that keeps the old refresh token.
  - The streaming JSON parser, for both SSE and JSON-array formats, including data split mid-character.
  - Unwrapping list-shaped error bodies.
  - Viewer-bar formatting.
  - Emoji sequence matching.
  - Twemoji file naming.
- **Simulated-service tests:**
  - A local HTTP server that streams chat in delayed batches, checking incremental delivery, backlog suppression, member alerts and the end-of-stream signal.
  - A fake-service UI test, checking queued messages, stacked popups that close on their own, theme switching and saving, and inline emoji images.
- **Live checks** against real YouTube and Twitch streams: sign-in, reading and sending chat on both platforms, viewer counts, and the problems listed in section 4.

---

## 7. Building

```
python -m unittest test_dualchat
python make_icon.py        # only if the icon design changes (needs Pillow)
python -m PyInstaller --onefile --windowed --name DualChat --noconfirm --clean --distpath dist --workpath build --specpath build --icon "$PWD\dualchat.ico" --add-data "$PWD\dualchat.ico;." dualchat.py
```

The result is `dist\DualChat.exe` (about 19 MB). Unsigned executables built with PyInstaller can trigger a Windows SmartScreen warning the first time they run.

---

## 8. Known limitations

- **Platform emotes:** Twitch emotes and YouTube's custom emoji show as text, since they're platform images rather than Unicode emoji.
- **Black-and-white emoji in some places:** emoji in alert popups and in the typing box can't be shown in color, because Tk labels and entry fields can't hold images.
- **YouTube subscribers:** alerts only appear for subscribers whose subscriptions are public, and YouTube's data can lag by a few minutes.
- **Twitch viewer count lag:** the count can trail Twitch's own player by a minute or two, as described in section 4.
- **Weekly YouTube sign-in:** while the Google Cloud app is in "Testing" mode, Google expires refresh tokens after 7 days. Publishing the consent screen removes this.
- **One broadcast:** the app connects to the first active YouTube broadcast if several are live at once.
- **Platforms:** the app is built and tested on Windows only. The code runs on other systems, but the DPI and taskbar setup is Windows-specific.

## 9. Possible improvements

- Store tokens in Windows Credential Manager instead of a plain-text file.
- Show Twitch emotes and YouTube custom emoji as images, using the `emotes` IRC tag and YouTube's emoji data.
- Add per-platform send toggles, and highlight moderators, members and subscribers.
- Add Super Chat, Super Sticker, bits and raid alerts.
- Add optional alert sounds and a setting for how long popups stay up.
- Code-sign the executable.
