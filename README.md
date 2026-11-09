<p align="center"><img src="assets/social-preview.png" alt="DualChat banner" width="100%"></p>

# DualChat
DualChat is a lightweight **Windows** app for streamers who go live on **YouTube** and **Twitch** at the same time. It shows both live chats in one window, and anything you type goes to both. It also shows live viewer counts and pops up an alert for every new sub, member and follower.

<img src="assets/icon.png" width="250" height="250" alt="DualChat icon" />

***
### App made by imNikos.
 
[![Discord](https://img.shields.io/badge/Discord-Nikos'%20Wasteland-5865F2?style=flat-square&logo=discord&logoColor=white)](https://discord.gg/9xJgzNqEaJ)
[![Instagram](https://img.shields.io/badge/Instagram-imNikos_-E4405F?style=flat-square&logo=instagram&logoColor=white)](https://instagram.com/imnikos_)
[![YouTube](https://img.shields.io/badge/YouTube-imNikoss-FF0000?style=flat-square&logo=youtube&logoColor=white)](https://youtube.com/@imnikoss)
[![TikTok](https://img.shields.io/badge/TikTok-imNikos_-000000?style=flat-square&logo=tiktok&logoColor=white)](https://tiktok.com/@imnikos_)
[![GitHub](https://img.shields.io/badge/GitHub-imnotnikos-181717?style=flat-square&logo=github&logoColor=white)](https://github.com/imnotnikos)
[![Modrinth](https://img.shields.io/badge/Modrinth-imNikos-1bd96a?style=flat-square&logo=modrinth&logoColor=white)](https://modrinth.com/user/imNikos)
[![CurseForge](https://img.shields.io/badge/CurseForge-imnotnikos-F16436?style=flat-square&logo=curseforge&logoColor=white)](https://www.curseforge.com/members/imnotnikos/projects)

***

## Features
**Set Up Inside the App**: No files to edit. The setup screen opens each Google and Twitch page right inside the DualChat window, below a DualChat bar with the step to follow. DualChat grabs your keys automatically as they appear, and sign-in codes are filled in for you.

**One Chat, Both Platforms**: YouTube and Twitch messages appear together, each line tagged and colored by platform.

**One Box Replies to Both**: Type once and press Enter. The message goes to YouTube and Twitch at the same time.

**Live Viewer Counts**: The top bar shows viewers on each platform and the combined total.

**Alerts**: Twitch subs, resubs, gift subs and follows, and YouTube subscribers and members, each pop up for 12 seconds in the top-right corner, always on top. Click a popup to close it early. A gift bomb shows as one popup instead of one per gift.

**Color Emoji**: Emoji, including skin tones, flags and combined emoji, show in full color.

**Custom Title Bar**: The header is the title bar. It has live viewer chips, connection dots, a red-to-purple accent line and custom minimize, maximize and close buttons. Drag it to move the window, with Windows snapping, and double-click it to maximize. The edges still resize. On the Google and Twitch setup pages, a bar across the top takes over with the same window buttons, **Open In Browser** and **◀ Back**. The page fits below it at any window size.

**Light & Dark Mode**: Switch with the **☀ Light / ☾ Dark** button in the header. Scrollbars and everything else follow the theme, and the app remembers your choice.

**Sharp on Any Display**: Text stays crisp at 125%, 150% and higher Windows scaling.

**Low API Usage**: YouTube chat comes in over one connection that stays open instead of constant checking, so a typical stream uses only about 250–350 of YouTube's 10,000 free daily API units per hour.

## Requirements
Windows 10 or 11 with Microsoft Edge WebView2. It's built into Windows 11 and installed on almost every Windows 10 PC.

A free Google Cloud project and a free Twitch developer app. DualChat walks you through both on first launch.

# Installation
## For Streamers
Navigate to the Releases page.

Download the latest `DualChat.exe` and put it in its own folder, for example `Documents\DualChat`.

Run it. The first time, the **Connect your accounts** screen opens. Click each step's **Open** button, and that page loads right inside DualChat. When a key appears on screen, DualChat saves it. Then click **Go to chat** and approve the two sign-ins.

## Setup
The .exe contains **no accounts or keys**. Each streamer connects their own, for free:

- **YouTube:** a Google Cloud project with **YouTube Data API v3** turned on and an OAuth client of type **TVs and Limited Input devices**.
- **Twitch:** a developer app with client type **Public**, category **Chat Bot** and redirect URL `http://localhost`.

Your keys are saved to `config.json` and your sign-ins to `tokens.json`, both next to `DualChat.exe`. You can reopen setup any time with **⚙ Setup**. **[SETUP.md](SETUP.md)** walks through every step, how to paste keys by hand, keeping them private, and troubleshooting.

## Good to Know
- Messages are limited to 200 characters, which is YouTube's limit.
- YouTube only reports new subscribers whose subscriptions are **public**, and it can take a few minutes. Member alerts arrive as fast as chat.
- Anyone who already followed or subscribed before you opened the app is ignored. Only new ones get a popup.
- While your Google app is in "Testing" mode, Google signs you out after 7 days, so you'll sign in to YouTube again about once a week.
- Google and Twitch sign-ins made inside DualChat's setup pages are kept in `%LOCALAPPDATA%\DualChat\webview`, separate from your normal browser. Delete that folder to sign them out.
- `config.json` and `tokens.json` hold your keys and logins. Never share or commit them. Both are in `.gitignore`.
- Country flag emoji use the [Twemoji Country Flags](https://github.com/talkjs/country-flag-emoji-polyfill) font, because Windows' emoji font has no flags. The artwork is from [Twemoji](https://github.com/jdecked/twemoji), licensed [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), used unmodified. See `assets/TwemojiCountryFlags-LICENSE.md`.

## Building
Requires Python 3 with `pywebview` and `pyinstaller` (plus `pillow` only to redraw the icon).

    pip install pywebview pyinstaller
    python -m unittest test_dualchat
    python -m PyInstaller --onefile --windowed --name DualChat --noconfirm --clean --distpath dist --workpath build --specpath build --icon "$PWD\dualchat.ico" --add-data "$PWD\dualchat.ico;." --add-data "$PWD\ui.html;." --add-data "$PWD\assets\icon.png;assets" --add-data "$PWD\assets\TwemojiCountryFlags.woff2;assets" dualchat.py

Output: `dist\DualChat.exe`

Run it from source with `python dualchat.py`. The window is `ui.html`, the icon comes from `make_icon.py`, and the banner is `assets/social-preview.html` rendered at 1280×640.

For how it all works and what was fixed along the way, see [REPORT.md](REPORT.md).
