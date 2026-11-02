# Connecting Your Accounts to DualChat

`DualChat.exe` ships with **no accounts or keys built in**. Each streamer connects their own free Google Cloud project and Twitch developer app. **All of it happens inside DualChat**: the setup screen opens each Google and Twitch page right in the DualChat window, below a DualChat bar that tells you what to do, and saves your keys automatically as soon as they appear on screen.

It takes about 10 minutes, and you only do it once.

```
DualChat\                  ← any folder you like
├── DualChat.exe           ← from the Releases page
├── config.json            ← DualChat creates this with your keys
└── tokens.json            ← DualChat creates this after you sign in
```

> You can set up just one platform. The other one is simply skipped.

---

## How the setup screen works

Run `DualChat.exe`. The first time, **Connect your accounts** opens. You can reopen it any time with **⚙ Setup** in the header.

- Each step has an **Open** button. It loads that Google or Twitch page **inside the DualChat window**.
- While a setup page is open, a **DualChat bar** runs across the top, and the page fits below it at any window size. The bar has:
  - what to do on this page
  - **Open In Browser**: use your normal browser instead, if a site won't cooperate inside the app
  - **◀ Back**: return to the setup screen
  - minimize, maximize and close. Drag the bar to move the window, or double-click it to maximize.
- When your keys appear on screen, DualChat **saves them by itself**, and the bar shows **✓ keys saved**.

---

## YouTube, about 6 minutes

Sign in with the **Google account that owns your YouTube channel**. Google Cloud is free for this, and there's no billing setup.

1. **Create a project.** Sign in, then create a project with any name. Make sure it's selected at the top of the page.
2. **Turn on YouTube Data API v3.** Click **Enable**.
3. **Set up Google Auth Platform.** Click **Get started** and enter:
   - **App name:** anything, for example `DualChat`. **User support email:** your email.
   - **Audience:** **External**.
   - **Contact information:** your email.
   - Agree to the Google API Services User Data Policy, then click **Create**.
4. **Add a test user.** Under **Test users**, click **Add users**, enter your channel's Google account, and click **Save**.
   *If you skip this, signing in later fails with "Access blocked".*
5. **Create the client.** Choose type **TVs and Limited Input devices**, give it any name, and click **Create**.
   The next screen shows the Client ID and secret, and **DualChat saves both automatically**. Google only shows the secret once, so stay on that screen until the bar says **✓ YouTube keys saved**.

## Twitch, about 3 minutes

1. **Two-factor authentication.** Twitch requires it before you can register apps. Turn it on if it's off.
2. **Register an app.** Log in if asked, then fill in:
   - **Name:** anything unique, for example `DualChat yourname`. Twitch says so if a name is taken.
   - **OAuth Redirect URLs:** `http://localhost`
   - **Category:** **Chat Bot**
   - **Client Type:** **Public**

   Then click **Create**.
3. **Open your app.** Click **Manage** next to your app. DualChat reads the Client ID from that page and saves it.

To use a channel other than your own, for example one you moderate, type its name in the **Channel** box and click **Save Twitch keys**.

---

## Go to chat and sign in

Click **Go to chat**. DualChat asks you to sign in to each platform once:

1. A card appears at the top of the chat with a **code**.
2. Click **Sign in here**. The sign-in page opens inside DualChat, and the code is filled in for you on Google's page. Twitch's page already includes it.
   - Google shows **"Google hasn't verified this app"**. That's expected for your own private app. Click **Continue**.
   - If Google won't let you sign in inside the app, click **Use my browser** on the card and enter the code there.
3. Approve access. DualChat returns to the chat by itself, and the card disappears.

Your sign-ins are saved in `tokens.json`, so next time DualChat just opens. Start your stream before or after opening DualChat. It looks for a live YouTube broadcast every 60 seconds.

---

## Already have keys? Paste them in

On the setup screen, each platform has boxes for pasting keys:

- **YouTube:** **Client ID** (ends in `.apps.googleusercontent.com`) and **Client secret** (usually starts with `GOCSPX-`).
- **Twitch:** **Client ID** (30 lowercase letters and numbers) and an optional **Channel**.

Click **Save**. DualChat checks the format before saving.

You can also write `config.json` yourself, using `config.example.json` from this repository as the template. Keep it next to `DualChat.exe`.

---

## Keep your keys private

- **Never share, upload or screenshot** `config.json` or `tokens.json`. Together they can read and post in your chat. The setup screen never shows your saved Google secret.
- **Moving DualChat:** move the whole folder. The files must stay together.
- **Sign out:**
  - Delete `tokens.json` to sign DualChat out of YouTube and Twitch.
  - Delete `%LOCALAPPDATA%\DualChat\webview` to sign the setup pages out of Google and Twitch.
- **Revoke access completely:**
  - **Google:** <https://myaccount.google.com/permissions>, then remove the app.
  - **Twitch:** Settings → **Connections**, then disconnect it.
- **If a key leaks:** delete the client in Google Cloud or the app on Twitch, create a new one, and run **⚙ Setup** again.

---

## Troubleshooting

| What you see | Fix |
|---|---|
| "Windows protected your PC" | The app isn't code-signed. Click **More info → Run anyway**. |
| The setup page doesn't capture keys | Stay on the screen that shows the keys for a few seconds. If the bar still doesn't show ✓, copy them into the paste boxes on the setup screen. |
| Google says "Access blocked" | Add your Google account under **Test users** (YouTube step 4). |
| Google won't sign in inside the app | Use **Open In Browser** on the bar, or **Use my browser** on the sign-in card. |
| YouTube: "no live stream right now" | You're not live yet, or you signed in with a Google account that doesn't own the channel. Delete `tokens.json` and sign in again. |
| YouTube asks you to sign in about every 7 days | Normal while your Google app is in **Testing** mode. Publishing the app in Google Auth Platform removes the limit. |
| YouTube: "quota exceeded" | Google's free limit is 10,000 units a day, and DualChat uses about 250–350 an hour. It resets at midnight Pacific time. |
| Twitch: "name is already taken" | Change the app name to anything unique and click **Create** again. |
| Twitch page shows an error when registering | Turn on two-factor authentication (Twitch step 1). |
| "config.json couldn't be read" in chat | The file was damaged, so DualChat set it aside as `config.broken-….json` (nothing is deleted). Run **⚙ Setup** again. |
