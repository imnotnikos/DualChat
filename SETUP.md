# Connecting Your Accounts to DualChat

`DualChat.exe` ships with **no accounts or keys built in**. You give it your own keys in a small text file called `config.json` that sits **next to the .exe**. The app reads that file when it starts, and the first time you run it, it asks you to sign in to YouTube and Twitch.

This guide takes about 10–15 minutes, and you only do it once.

```
DualChat\                  ← any folder you like
├── DualChat.exe           ← from the Releases page
├── config.json            ← you create this (step 4)
└── tokens.json            ← the app creates this after you sign in
```

> You can set up just one platform. Anything left on its `YOUR_...` placeholder is skipped.

---

## Step 1: Download

1. Open this repository's **Releases** page and download the latest `DualChat.exe` and `config.example.json`.
2. Put both files in the same folder, for example `Documents\DualChat`.

---

## Step 2: YouTube (Google Cloud), about 8 minutes

YouTube needs a free Google Cloud "project". It's how Google lets an app read and post in *your* chat. There's no cost and no billing setup.

1. Go to <https://console.cloud.google.com> and sign in with the **Google account that owns your YouTube channel**.
2. **Create a project:** click the project picker at the top, then **New project**. Name it anything (for example `DualChat`) and click **Create**. Make sure the new project is selected.
3. **Turn on the YouTube API:** search the top bar for **YouTube Data API v3**, open it, and click **Enable**.
4. **Set up sign-in:** search for **Google Auth Platform** (older consoles call it **OAuth consent screen**) and click **Get started**.
   - **App name:** anything, for example `DualChat`. **User support email:** your email.
   - **Audience:** **External**.
   - **Contact information:** your email.
   - Agree to the Google API Services User Data Policy, then click **Create**.
5. **Add yourself as a test user:** go to **Audience → Test users → Add users**, enter the Google account that owns your channel, and click **Save**.
   *If you skip this, signing in later fails with "Access blocked".*
6. **Create the client:** go to **Clients → Create client**.
   - **Application type:** **TVs and Limited Input devices**. This type is required, because the app signs you in with a code.
   - **Name:** anything, then click **Create**.
7. **Copy the Client ID and Client secret right away.** The secret is only shown once. Paste both somewhere safe for step 4, or download the JSON.

---

## Step 3: Twitch, about 3 minutes

1. Twitch requires **two-factor authentication** to register apps. If yours is off, turn it on in Twitch → **Settings → Security and Privacy**.
2. Go to <https://dev.twitch.tv/console/apps> and click **Register Your Application**. Log in with your Twitch account if asked.
3. Fill in:
   - **Name:** anything unique, for example `DualChat yourname`.
   - **OAuth Redirect URLs:** `http://localhost`
   - **Category:** **Chat Bot**
   - **Client Type:** **Public**. This matters: a public client needs no secret.
4. Click **Create**, then **Manage**, and copy the **Client ID**.

---

## Step 4: Create `config.json`

1. In your DualChat folder, **rename** `config.example.json` to `config.json`.
   *Windows tip: if you can't see file extensions, turn on File Explorer → **View → Show → File name extensions**, so you don't end up with `config.json.json`.*
2. Open `config.json` in Notepad. Replace **only the text inside the quotes** with your values:

```json
{
  "youtube": {
    "client_id": "PASTE-GOOGLE-CLIENT-ID.apps.googleusercontent.com",
    "client_secret": "PASTE-GOOGLE-CLIENT-SECRET"
  },
  "twitch": {
    "client_id": "PASTE-TWITCH-CLIENT-ID",
    "channel": ""
  }
}
```

| Field | What to put |
|---|---|
| `youtube.client_id` | The Google **Client ID** from step 2.7 (it ends in `.apps.googleusercontent.com`) |
| `youtube.client_secret` | The Google **Client secret** from step 2.7 |
| `twitch.client_id` | The Twitch **Client ID** from step 3.4 |
| `twitch.channel` | Leave `""` to use your own channel, or type another channel name you moderate |
| `theme` *(optional)* | `"dark"` or `"light"`. The app adds and updates this itself when you click the theme button |

3. Save the file. Keep the quotes and commas exactly as they are. If the app says the file "isn't valid JSON", a quote or comma is usually missing.

---

## Step 5: First launch and sign-in

1. Double-click `DualChat.exe`.
   - If Windows shows **"Windows protected your PC"**, click **More info → Run anyway**. The app isn't code-signed, which is common for free tools.
2. **YouTube:** a Google "Connect a device" page opens in your browser, and the DualChat window shows a gray line with a **code**. Type the code into the page, pick the account that owns your channel, and allow access.
   - Google shows **"Google hasn't verified this app"**. That's expected for your own private app. Click **Continue**.
3. **Twitch:** once YouTube is done, a Twitch page opens with the code already filled in. Click **Activate**, then **Authorize**.
4. Done. Your sign-ins are saved in `tokens.json`, so next time the app just opens.

Start your stream before or after opening DualChat. It looks for a live YouTube broadcast every 60 seconds.

---

## Keep your keys private

- **Never share, upload or screenshot** `config.json` or `tokens.json`. Together they can read and post in your chat.
- **Moving DualChat:** move the whole folder. The three files must stay together.
- **Sign out or reset:** delete `tokens.json`, and the app asks you to sign in again.
- **Revoke access completely:**
  - **Google:** <https://myaccount.google.com/permissions>, then remove the app.
  - **Twitch:** Settings → **Connections**, then disconnect it.
- **If a key leaks:** in Google Cloud, delete the client and create a new one. On Twitch, delete the app and register a new one. Then update `config.json`.

---

## Troubleshooting

| What you see | Fix |
|---|---|
| "No config.json found" | `config.json` must be in the **same folder** as `DualChat.exe`, and named exactly that (check for a hidden `.txt` ending). |
| "config.json isn't valid JSON" | A quote or comma is missing. Copy the example above again and paste your values back in. |
| Google says "Access blocked" | Add your Google account under **Audience → Test users** (step 2.5). |
| YouTube: "no live stream right now" | You're not live yet, or you signed in with a Google account that doesn't own the channel. Delete `tokens.json` and sign in with the right one. |
| YouTube asks you to sign in about every 7 days | Normal while your Google app is in **Testing** mode. Publishing the app in Google Auth Platform removes the limit. |
| YouTube: "quota exceeded" | Google's free limit is 10,000 units a day, and DualChat uses about 250–350 an hour. It resets at midnight Pacific time. |
| Twitch never connects | Check the app is a **Public** client and the Client ID has no extra spaces. |
| Twitch sign-in page shows an error | Your Twitch account needs two-factor authentication turned on (step 3.1). |
