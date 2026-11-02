# DualChat

One window that shows your YouTube and Twitch live chat together. Type in the box, press Enter, and the message goes to both.

The .exe needs nothing installed. Running from source needs Python 3 and `pip install pillow`, which is used for color emoji.

## One-time setup

Copy `config.example.json` to `config.json`, then fill it in. If you leave a platform's `YOUR_...` placeholder alone, that platform is skipped.

### YouTube
1. Go to https://console.cloud.google.com, create a project, and enable **YouTube Data API v3**.
2. Open **OAuth consent screen**: choose External and add your own Google account as a test user.
3. Open **Credentials → Create credentials → OAuth client ID**, pick type **TVs and Limited Input devices**, and copy the client ID and secret into `config.json`.

### Twitch
1. Go to https://dev.twitch.tv/console/apps and click **Register Your Application**.
2. Set the redirect URL to `http://localhost`, category **Chat Bot**, client type **Public**.
3. Copy the Client ID into `config.json`. Leave `channel` empty to use your own channel.

## Run

Double-click `dist\DualChat.exe`. Keep `config.json` in the same folder as the .exe; that's also where it saves `tokens.json`. You can move the whole folder anywhere.

Or run it from source: `python dualchat.py`

To rebuild the .exe after changing the code (needs `pip install pyinstaller`), run this in PowerShell from this folder:

```
python -m PyInstaller --onefile --windowed --name DualChat --noconfirm --clean --distpath dist --workpath build --specpath build --icon "$PWD\dualchat.ico" --add-data "$PWD\dualchat.ico;." dualchat.py
```

The icon comes from `make_icon.py` (needs `pip install pillow`). Rerun it if you change the design.

The **☀ Light / ☾ Dark** button in the top bar switches themes, and the app remembers your choice in `config.json`.

On first run the app opens a sign-in page for each platform and shows a code in the chat window. After that, the logins are saved in `tokens.json`.

Start your YouTube stream before (or after) launching. The app checks for a live broadcast every 60 seconds.

## Notes
- YouTube limits messages to 200 characters, so the box does too.
- Emoji in chat show in color using [Twemoji](https://github.com/jdecked/twemoji) images. The app downloads each emoji the first time it appears and keeps it for the session. If it's offline, emoji fall back to plain text.
- YouTube's free API quota is 10,000 units a day. YouTube chat comes in over one connection that stays open (`streamList`), so messages show up instantly. That costs 5 units each time it connects, not on every check. Viewer and subscriber checks cost 2 units every 30 seconds, and each message you send costs 20. A typical stream uses roughly 250–350 units an hour.
- The bar at the top shows live viewers for each platform plus a total. YouTube refreshes every 30 seconds and Twitch every 10 seconds.
- **Alerts:** subs, resubs, gift subs and follows on Twitch, plus new subscribers and members on YouTube. Each one pops up as an always-on-top box in the top-right corner for 12 seconds (click to close it early) and also shows as a gold line in chat.
  - Twitch subs show up instantly. Follows are checked every 10 seconds.
  - YouTube only reports subscribers who keep their subscriptions **public**, and it can lag by a few minutes. Member alerts come through chat, so they arrive as fast as chat does.
  - Anyone who already followed or subscribed before you opened the app is ignored. Only new ones get a popup.
- While the Google app is in "Testing" mode, Google expires the saved login after 7 days, so you'll sign in again weekly.
- `config.json` and `tokens.json` hold your secrets. They're in `.gitignore`, so never commit them.

Tests: `python -m unittest test_dualchat`
