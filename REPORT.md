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
| In-app setup with automatic key capture | Yes | Yes |
| Light / dark theme | Toggle in the header, remembered between runs, title bar follows | |

Other details:

- **Setup inside the app:** the setup screen opens each Google Cloud and Twitch developer page in the DualChat window itself, below a native DualChat bar. Keys are captured from the page as soon as they appear. Sign-in codes are filled in on Google's device page, and Twitch's link already includes the code.
- **Seamless header:** the native Windows title bar is recolored to match the app header in both themes. Snapping, resizing and the standard window buttons keep working.
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
            │              numbered event log ◄── ui.html in WebView2  │
            │                         ▲          (polls every 150 ms   │
            │                         │           through a guarded API)│
 Twitch  ◄──┤  Twitch thread ── IRC socket (read + send)                 │
 IRC/API    │  Twitch stats thread ── viewers + follows every 10 s       │
            └────────────────────────────────────────────────────────────┘
```

- **Small and mostly standard library.** The backend lives in `dualchat.py` and uses the Python standard library (`urllib`, `ssl`, `socket`) plus `pywebview`. The window is a single HTML page, `ui.html`, shown in **Microsoft Edge WebView2**, which is part of Windows. The first version used Tkinter. It moved to WebView2 because Tk can't show real web pages, which in-app setup needs. The move also gave native color emoji and a fully styled interface.
- **The page never touches the network.** Worker threads add events to a numbered log in Python. The page asks for anything newer than the last number it saw, every 150 ms. When the window comes back from a setup page, the page reloads and replays the log, so chat history is never lost or duplicated. Sending runs on short-lived worker threads, so a slow API call can't freeze the window.
- **All Twitch socket traffic stays on one thread.** Instead of sharing the TLS socket between the reader and the send button, outgoing messages go into an outbox queue. The reader thread sends them between short (0.5 s) reads, which avoids using one TLS connection from two threads at once.
- **Each platform runs separately.** If a platform has no client ID in `config.json`, it is simply skipped. Each worker has its own reconnect loop with back-off, so one platform failing never affects the other.

### Sign-in (OAuth 2.0 device flow)

Both platforms use the **device authorization grant**. The app shows a short code, opens the sign-in page, and polls until the user approves. One small function handles both Google and Twitch, since the two only differ in a few field names (`verification_url` vs `verification_uri`, `error` vs `message`).

- Tokens are saved to `tokens.json` next to the app and refreshed automatically before they expire.
- Google doesn't send a new refresh token on refresh, so the existing one is kept.
- A rejected refresh falls back to signing in again.
- Sign-ins happen one platform at a time, so the user is never asked for two codes at once.

The device flow was chosen because it needs no client secret for Twitch (it's a public client), no local web server, and no redirect handling.

### In-app setup

The setup screen, `ui.html`'s setup view, lists the steps for each platform. Each **Open** button loads the real Google Cloud or Twitch developer page into the same WebView2 window.

**The setup bar.** A native WinForms strip is docked across the top of the window while a setup page shows, so the page gets the rest of the window and nothing on it is covered.
- **Contents:** the step to follow, **Open In Browser** with an icon, **◀ Back**, and minimize, maximize and close.
- **Title-bar behavior:** drag it to move the window (Windows' own move loop, with snapping), double-click it to maximize, or drag its top edge to resize.
- **Width:** on narrow windows the step text, and at the narrowest the action buttons too, wrap onto their own rows. The bar grows to fit, and the page below shrinks to match.
- **Isolation:** nothing is drawn into Google's or Twitch's pages.

**The watcher.** While a setup page shows, a Python thread checks it about once a second:
- **Key capture:**
  - **Google:** when the page shows exactly one Client ID and one secret (`GOCSPX-…`), both are saved. Two IDs on screen, such as the client list, count as ambiguous and are ignored.
  - **Twitch:** the Client ID is taken from the `/console/apps/<id>` address of the app's Manage page.
- **Sign-in code:** on Google's device page, the code is filled in through the browser's native value setter, so the page's own code registers the change.

Keys are written to `config.json` atomically (temporary file, then rename). Sign-ins made on setup pages are kept in a WebView2 profile under `%LOCALAPPDATA%\DualChat`, separate from the user's own browser.

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
| 6 | Emoji showed in black and white, and skin tones or flags came apart | Tk on Windows draws text without color emoji or emoji joining. | First fixed with inline Twemoji images. The move to WebView2 made color emoji native, and the image code was removed. Windows' emoji font has no country flags, so a bundled flag-only font (Twemoji Country Flags, CC BY 4.0) covers just those characters. |
| 7 | The .exe couldn't find its config | In a PyInstaller one-file build, `__file__` points into a temporary unpack folder. | The app reads and writes `config.json` and `tokens.json` next to `sys.executable`, and loads bundled files (UI, icon, font) from `sys._MEIPASS`. |
| 8 | Setup pages could call the app (found while building in-app setup) | WebView2 exposes the app's JavaScript API to **every** page loaded in the window, Google's and Twitch's included. | Every API method checks that the window is showing DualChat's own page (`http://127.0.0.1:<port>/`) and refuses otherwise. Verified by running a script on a real Twitch page that tried to read events and overwrite the saved Twitch key. Both were refused. |
| 9 | The window could open with a blank view | pywebview's `pywebviewready` event can fire before the page's script registers for it. | The page also starts straight away if the API is already there, and retries if Python isn't ready yet. |

**Twitch viewer lag:** Twitch's official viewer count comes from a cache on Twitch's side, so it can trail the count on Twitch's own player by a minute or two. There's no faster official source. The app only uses documented, official APIs.

---

## 5. Security and privacy

- **No secrets in the repository.** `config.json` (client IDs and the Google client secret) and `tokens.json` (access and refresh tokens) are in `.gitignore`. A placeholder `config.example.json` is shipped instead.
- **Least-privilege scopes.** Google uses the `youtube` scope, needed to post chat. Twitch only requests chat read/write and follower read access.
- **IRC injection blocked.** Typed text is collapsed to a single line before sending, so a newline can't sneak in a raw IRC command. This is covered by a test.
- **Length limit.** Messages are capped at 200 characters (YouTube's limit) and rejected with a message instead of being cut off without warning.
- **Tokens are stored in plain text** next to the app, protected only by Windows file permissions. That's acceptable for a single-user desktop tool. See the improvements list below.
- **Chat can't inject code.** Chat text and names are untrusted, so the page only ever inserts them as plain text (`textContent`), and popups escape them. This is tested with a message containing an `<img onerror=…>` payload.
- **Setup pages are walled off** from the app's API (problem 8 above), and the setup bar is native, outside the page entirely. The saved Google secret is never sent to the page, which only learns whether one exists.
- **Damaged config is never overwritten.** An unreadable `config.json` is renamed to `config.broken-<time>.json` before setup writes a new one, and every write goes through a temporary file first.
- **No network calls for emoji.** The flag font ships inside the app.

---

## 6. Testing

- **Unit tests** (`python -m unittest test_dualchat`), 12 in total:
  - IRC parsing, including tags with escapes and `USERNOTICE` events.
  - IRC injection blocking.
  - Token refresh that keeps the old refresh token.
  - The streaming JSON parser, for both SSE and JSON-array formats, including data split mid-character.
  - Unwrapping list-shaped error bodies.
  - Google key capture, which requires exactly one ID and one secret.
  - Twitch key capture, which only accepts dev.twitch.tv app addresses.
  - Checking pasted keys.
  - Treating placeholders as "not set up".
  - The API refusing every call while a setup page is showing.
  - A damaged config being set aside rather than overwritten.
- **Simulated-service tests:**
  - A local HTTP server that streams chat in delayed batches, checking incremental delivery, backlog suppression, member alerts and the end-of-stream signal.
- **Full-window tests** driving the real WebView2 window with throwaway settings:
  - First launch opens setup, and saving a key from the form works.
  - A Twitch page opens inside the window below the setup bar, and that page is refused when it calls the app.
  - The page fits below the bar at every size, including the narrowest window and maximized, and the bar's buttons, dragging and double-click all work.
  - The header works as a title bar: drag, double-click, the custom buttons, resizing from the sides and top, with real mouse input.
  - The bar's **Back** button returns to chat with history replayed.
  - Chat with HTML in it stays plain text.
  - The title bar recolors in both themes (all Windows calls return success).
  - The alert popup opens and closes on time.
  - The built .exe serves its bundled page, icon and font.
- **Live checks** against real YouTube and Twitch streams: sign-in, reading and sending chat on both platforms, viewer counts, and the problems listed in section 4.

---

## 7. Building

```
python -m unittest test_dualchat
python make_icon.py        # only if the icon design changes (needs Pillow)
python -m PyInstaller --onefile --windowed --name DualChat --noconfirm --clean --distpath dist --workpath build --specpath build --icon "$PWD\dualchat.ico" --add-data "$PWD\dualchat.ico;." --add-data "$PWD\ui.html;." --add-data "$PWD\assets\icon.png;assets" --add-data "$PWD\assets\TwemojiCountryFlags.woff2;assets" dualchat.py
```

The result is `dist\DualChat.exe` (about 14 MB). Unsigned executables built with PyInstaller can trigger a Windows SmartScreen warning the first time they run.

---

## 8. Known limitations

- **Platform emotes:** Twitch emotes and YouTube's custom emoji show as text, since they're platform images rather than Unicode emoji.
- **Setup pages can change:** key capture depends on how Google's and Twitch's pages look today. If either site changes, keys can still be pasted on the setup screen.
- **Google may refuse sign-in inside apps:** Google sometimes blocks signing in from embedded browsers. **Open In Browser** and **Use my browser** are the way around it.
- **Flags in alert popups** show as letters. The popups don't load the flag font.
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
