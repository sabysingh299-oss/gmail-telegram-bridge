# Gmail ↔ Telegram Bridge

Talk to your Gmail from Telegram — locally.

This small Python app runs on your machine. It:

1. Completes **Google OAuth** for one Gmail account (token stays on disk)
2. Connects a **Telegram bot** you create with BotFather
3. Lets you use **slash commands** or **plain English** (optional Gemini key) to read, search, reply, send, and draft mail

No secrets are shipped in this repository. You bring your own OAuth client, bot token, and optional Gemini API key.

---

## What you need

| Piece | Where to get it |
| --- | --- |
| Python 3.10+ | system / brew / pyenv |
| Google Cloud OAuth client (Desktop or Web) | [Google Cloud Console → Credentials](https://console.cloud.google.com/apis/credentials) |
| Telegram bot token | [@BotFather](https://t.me/BotFather) |
| Optional Gemini API key | [Google AI Studio](https://aistudio.google.com/apikey) |

Enable the **Gmail API** on your Google Cloud project.

---

## Quick start

```bash
git clone https://github.com/<you>/gmail-telegram-bridge.git
cd gmail-telegram-bridge
cp .env.example .env
```

Edit `.env` and fill at least:

```env
GOOGLE_CLIENT_ID=your-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret
GOOGLE_REDIRECT_URI=http://localhost:8765/oauth2callback
TELEGRAM_BOT_TOKEN=your-botfather-token
# optional
GEMINI_API_KEY=
GEMINI_MODEL=gemini-3.1-flash-lite
GCP_QUOTA_PROJECT=
```

In Google Cloud, add this **Authorized redirect URI** to your OAuth client:

```text
http://localhost:8765/oauth2callback
```

Run the local daemon:

```bash
python3 agent_daemon.py
```

Open **http://127.0.0.1:8765/** → sign in with Google → open your bot in Telegram → send `/start`.

---

## Telegram usage

### Commands

| Command | Action |
| --- | --- |
| `/start` `/help` | Connect chat + show help |
| `/auth` | OAuth link / status |
| `/status` | Gmail + bot status |
| `/unread` | Unread inbox |
| `/inbox` | Latest inbox |
| `/search <query>` | Gmail search |
| `/read <msg_id>` | Full message |
| `/markread <msg_id>` | Mark read |
| `/reply <msg_id> <text>` | Reply in thread |
| `/send to@x.com \| Subject \| Body` | Send |
| `/draft to@x.com \| Subject \| Body` | Draft |

### Plain English (with Gemini key)

Examples:

- `show unread`
- `search from google`
- `summarize my latest emails`
- `draft a polite reply saying thanks`

Without `GEMINI_API_KEY`, common phrases like “show unread” still work via a small local mapper.

Inline buttons on new-mail alerts: **Read Full** / **Mark Read**.

---

## Optional MCP server

For IDE / agent tooling:

```bash
python3 mcp_server.py
```

Point your MCP client at that command. Tools cover status, OAuth URL, list/read/send/reply/draft, and Telegram notify.

---

## Files (local only — gitignored)

| File | Purpose |
| --- | --- |
| `.env` / `credentials.json` | Your secrets |
| `token.json` | Gmail OAuth tokens |
| `state.json` | Poll offset + notified ids |

Never commit these. `.gitignore` already excludes them.

---

## Security notes

- Runs on **localhost** only by default (`127.0.0.1:8765`)
- OAuth tokens and API keys stay on your machine
- Put the app in Google Cloud **Testing** mode and add your Google account as a test user while developing
- Rotate any key that was ever pasted into chat or committed by mistake

---

## License

MIT — see [LICENSE](LICENSE).
