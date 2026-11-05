#!/usr/bin/env python3
"""Sarabyx Gmail (GCP OAuth) + @Sarabyx_bot Agent Daemon — Instant Direct Google OAuth Redirect."""
import html
import json
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict

from bridge_worker import process_telegram_updates, sync_new_unread_emails_to_telegram
from config import (
    DEFAULT_PORT,
    DEFAULT_REDIRECT_URI,
    load_env,
    load_token,
    oauth_configured,
    save_env_updates,
)
from gmail_client import (
    build_oauth_url,
    exchange_code_for_token,
    get_auth_status,
)
from telegram_client import (
    get_bot_status,
    get_default_chat_id,
    register_bot_commands,
    send_message,
)


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def render_dashboard_html(flash_msg: str = "", flash_type: str = "info") -> str:
    env = load_env()
    token = load_token()
    has_oauth_client = oauth_configured()
    is_authed = bool(has_oauth_client and token and token.get("access_token"))
    email_addr = str(
        (token or {}).get("emailAddress")
        or env.get("SARABYX_GMAIL_ADDRESS")
        or ""
    )
    bot_status = get_bot_status()
    bot_user = (
        (env.get("TELEGRAM_BOT_USERNAME") or "").strip().lstrip("@")
        or str(bot_status.get("bot_username") or "").strip()
        or "your_bot"
    )
    chat_id = get_default_chat_id()
    telegram_ready = bool(chat_id)
    client_id_value = html.escape(env.get("GOOGLE_CLIENT_ID") or "")
    has_secret = bool(env.get("GOOGLE_CLIENT_SECRET"))

    flash_block = ""
    if flash_msg:
        cls = (
            "flash-ok"
            if flash_type == "ok"
            else "flash-err"
            if flash_type == "err"
            else "flash-info"
        )
        flash_block = (
            f'<div class="flash {cls}" role="status">{html.escape(flash_msg)}</div>'
        )

    oauth_state = "done" if has_oauth_client else "todo"
    gmail_state = "done" if is_authed else "todo"
    tg_state = "done" if telegram_ready else "todo"
    gmail_detail = (
        html.escape(email_addr)
        if is_authed and email_addr
        else "OAuth token saved"
        if is_authed
        else (
            "Authorize the mailbox this bridge should use"
            if has_oauth_client
            else "Save your Google Cloud OAuth client first"
        )
    )
    tg_detail = (
        f"Chat linked · send /unread or /inbox in @{html.escape(bot_user)}"
        if telegram_ready
        else f"Open @{html.escape(bot_user)} and send /start"
    )
    google_label = (
        "Switch Google account" if is_authed else "Sign in with Google"
    )
    google_cta = (
        f"""<a href="/auth/start" class="btn btn-google">
            <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true"><path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/><path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/><path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/><path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/></svg>
            {google_label}
          </a>"""
        if has_oauth_client
        else '<p class="hint">Google sign-in unlocks after the OAuth client above is saved.</p>'
    )
    secret_placeholder = "•••••••• saved — paste to replace" if has_secret else "Client secret"
    ready_note = ""
    if is_authed and telegram_ready:
        ready_note = (
            '<p class="ready">Both sides are connected. Use Telegram to read, '
            "search, and reply from this Gmail.</p>"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Gmail ↔ Telegram setup</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Manrope:wght@600;700;800&display=swap" rel="stylesheet" />
  <style>
    :root {{
      --paper: #f5f2e9;
      --paper-deep: #ebe6d8;
      --ink: #17211f;
      --muted: #4c5550;
      --line: rgba(23, 33, 31, .16);
      --blue: #294be0;
      --lime: #d9ef73;
      --ok: #1f6b45;
      --ok-bg: #e5f3ea;
      --err: #8a2f2f;
      --err-bg: #f7e8e6;
      --info: #294be0;
      --info-bg: #e8ecfb;
      --sans: "DM Sans", system-ui, sans-serif;
      --display: "Manrope", system-ui, sans-serif;
    }}
    * {{ box-sizing: border-box; }}
    ::selection {{ background: var(--lime); color: var(--ink); }}
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      padding: 32px 20px;
      font-family: var(--sans);
      color: var(--ink);
      background:
        radial-gradient(circle at 12% 18%, rgba(41, 75, 224, .08), transparent 42%),
        radial-gradient(circle at 88% 82%, rgba(217, 239, 115, .35), transparent 36%),
        var(--paper);
      -webkit-font-smoothing: antialiased;
    }}
    .shell {{
      width: min(100%, 440px);
    }}
    .brand {{
      display: flex;
      align-items: center;
      gap: 10px;
      margin-bottom: 22px;
      text-decoration: none;
      color: inherit;
    }}
    .brand-mark {{
      width: 28px;
      height: 28px;
      border-radius: 8px;
      background: var(--ink);
      color: var(--lime);
      display: grid;
      place-items: center;
      font: 800 13px/1 var(--display);
      letter-spacing: -0.04em;
    }}
    .wordmark {{
      font: 800 22px/1 var(--display);
      letter-spacing: -0.04em;
    }}
    .wordmark span {{ font-weight: 500; }}
    .wordmark .dot {{ color: var(--blue); }}
    .panel {{
      background: #fffefa;
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 28px 26px 24px;
    }}
    h1 {{
      margin: 0 0 8px;
      font: 700 1.55rem/1.2 var(--display);
      letter-spacing: -0.03em;
    }}
    .lede {{
      margin: 0 0 22px;
      color: var(--muted);
      font-size: 0.95rem;
      line-height: 1.55;
    }}
    .flash {{
      padding: 12px 14px;
      border-radius: 10px;
      margin-bottom: 18px;
      font-size: 0.9rem;
      line-height: 1.45;
    }}
    .flash-ok {{ background: var(--ok-bg); color: var(--ok); }}
    .flash-err {{ background: var(--err-bg); color: var(--err); }}
    .flash-info {{ background: var(--info-bg); color: var(--info); }}
    .steps {{
      display: grid;
      gap: 14px;
      margin: 0 0 8px;
    }}
    .step {{
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 16px;
      background: var(--paper);
    }}
    .step-top {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 8px;
    }}
    .step-label {{
      font: 700 0.78rem/1 var(--display);
      letter-spacing: 0.04em;
      text-transform: uppercase;
      color: var(--muted);
    }}
    .badge {{
      font-size: 0.75rem;
      font-weight: 700;
      padding: 5px 9px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: #fff;
      color: var(--muted);
    }}
    .badge.done {{
      background: var(--ok-bg);
      border-color: transparent;
      color: var(--ok);
    }}
    .step h2 {{
      margin: 0 0 6px;
      font: 700 1.05rem/1.3 var(--display);
      letter-spacing: -0.02em;
    }}
    .step p {{
      margin: 0 0 14px;
      color: var(--muted);
      font-size: 0.9rem;
      line-height: 1.45;
    }}
    .btn {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 10px;
      width: 100%;
      min-height: 48px;
      padding: 12px 16px;
      border-radius: 10px;
      font: 600 0.98rem/1 var(--sans);
      text-decoration: none;
      transition: background .15s ease, color .15s ease, border-color .15s ease;
    }}
    .btn:focus-visible {{
      outline: 2px solid var(--blue);
      outline-offset: 3px;
    }}
    .btn-google {{
      background: #fff;
      color: var(--ink);
      border: 1px solid var(--line);
    }}
    .btn-google:hover {{ border-color: var(--ink); }}
    .btn-tg {{
      background: var(--ink);
      color: var(--paper);
      border: 1px solid var(--ink);
    }}
    .btn-tg:hover {{ background: var(--blue); border-color: var(--blue); }}
    .btn-tg .arrow {{ color: var(--lime); }}
    .btn-save {{
      background: var(--blue);
      color: #fff;
      border: 1px solid var(--blue);
      cursor: pointer;
      width: 100%;
    }}
    .btn-save:hover {{ filter: brightness(1.05); }}
    .fields {{ display: grid; gap: 10px; margin-bottom: 12px; }}
    label {{
      display: grid;
      gap: 6px;
      font-size: 0.8rem;
      font-weight: 600;
      color: var(--muted);
    }}
    input {{
      width: 100%;
      min-height: 44px;
      padding: 10px 12px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: #fff;
      color: var(--ink);
      font: 500 0.92rem/1.3 var(--sans);
    }}
    input:focus {{
      outline: 2px solid var(--blue);
      outline-offset: 1px;
    }}
    .hint {{
      margin: 0;
      color: var(--muted);
      font-size: 0.86rem;
      line-height: 1.45;
    }}
    .howto {{
      margin: 0 0 12px;
      padding: 10px 12px;
      border-radius: 10px;
      background: #fff;
      border: 1px solid var(--line);
      color: var(--muted);
      font-size: 0.82rem;
      line-height: 1.5;
    }}
    .howto code {{
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 0.78rem;
      color: var(--ink);
    }}
    .ready {{
      margin: 18px 0 0;
      padding-top: 16px;
      border-top: 1px solid var(--line);
      color: var(--ok);
      font-size: 0.9rem;
      line-height: 1.45;
    }}
    .foot {{
      margin: 16px 2px 0;
      color: var(--muted);
      font-size: 0.78rem;
      line-height: 1.45;
    }}
  </style>
</head>
<body>
  <main class="shell">
    <div class="brand" aria-label="Sarabyx">
      <span class="brand-mark" aria-hidden="true">sx</span>
      <span class="wordmark">sara<span>by</span>x<span class="dot">.</span></span>
    </div>

    <section class="panel" aria-labelledby="setup-title">
      <h1 id="setup-title">Connect Gmail to Telegram</h1>
      <p class="lede">
        Get an OAuth token for one Gmail account, then talk to that inbox from
        Telegram — unread mail, search, read, reply.
      </p>

      {flash_block}

      <div class="steps">
        <article class="step" data-state="{oauth_state}">
          <div class="step-top">
            <span class="step-label">Step 1 · Google Cloud</span>
            <span class="badge {oauth_state}">{"Saved" if has_oauth_client else "Needed"}</span>
          </div>
          <h2>Paste your OAuth client</h2>
          <p class="howto">
            In Google Cloud → APIs &amp; Services → Credentials, create an
            <b>OAuth client ID</b> (Desktop or Web). Add redirect URI
            <code>{html.escape(DEFAULT_REDIRECT_URI)}</code>, then paste the
            client ID and secret here. Do not use a public sample client.
          </p>
          <form class="fields" method="post" action="/configure" autocomplete="off">
            <label>
              Client ID
              <input name="google_client_id" type="text" required value="{client_id_value}" placeholder="….apps.googleusercontent.com" spellcheck="false" />
            </label>
            <label>
              Client secret
              <input name="google_client_secret" type="password" {"required" if not has_secret else ""} placeholder="{secret_placeholder}" spellcheck="false" />
            </label>
            <button class="btn btn-save" type="submit">Save OAuth client</button>
          </form>
        </article>

        <article class="step" data-state="{gmail_state}">
          <div class="step-top">
            <span class="step-label">Step 2 · Gmail</span>
            <span class="badge {gmail_state}">{"Connected" if is_authed else "Needed"}</span>
          </div>
          <h2>Authorize Gmail</h2>
          <p>{gmail_detail}</p>
          {google_cta}
        </article>

        <article class="step" data-state="{tg_state}">
          <div class="step-top">
            <span class="step-label">Step 3 · Telegram</span>
            <span class="badge {tg_state}">{"Ready" if telegram_ready else "Needed"}</span>
          </div>
          <h2>Link the bot</h2>
          <p>{tg_detail}</p>
          <a href="https://t.me/{html.escape(bot_user)}" target="_blank" rel="noopener noreferrer" class="btn btn-tg">
            Open @{html.escape(bot_user)}
            <span class="arrow" aria-hidden="true">↗</span>
          </a>
        </article>
      </div>

      {ready_note}
    </section>

    <p class="foot">Local only · http://127.0.0.1 · token stays on this machine</p>
  </main>
</body>
</html>"""


class AgentHTTPHandler(BaseHTTPRequestHandler):
    """HTTP Handler for direct Google OAuth2 redirect & callback."""

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def _send_html(self, content: str, status: int = 200) -> None:
        encoded = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_json(self, data: Dict[str, Any], status: int = 200) -> None:
        encoded = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path == "/":
            msg = qs.get("msg", [""])[0]
            mtype = qs.get("type", ["info"])[0]
            self._send_html(render_dashboard_html(msg, mtype))
            return

        if path == "/api/status":
            self._send_json(
                {
                    "gmail": get_auth_status(),
                    "telegram": get_bot_status(),
                }
            )
            return

        if path == "/auth/start":
            if not oauth_configured():
                self.send_response(302)
                self.send_header(
                    "Location",
                    "/?type=err&msg="
                    + urllib.parse.quote(
                        "Add your Google Cloud OAuth client ID and secret first."
                    ),
                )
                self.end_headers()
                return
            auth_url = build_oauth_url(redirect_uri=DEFAULT_REDIRECT_URI)
            self.send_response(302)
            self.send_header("Location", auth_url)
            self.end_headers()
            return

        if path == "/oauth2callback":
            if "error" in qs:
                err = qs["error"][0]
                self._send_html(render_dashboard_html(f"Google OAuth Error: {err}", "err"), status=400)
                return
            code = qs.get("code", [""])[0]
            if not code:
                self._send_html(render_dashboard_html("Missing ?code= in OAuth callback.", "err"), status=400)
                return
            try:
                token_data = exchange_code_for_token(code, redirect_uri=DEFAULT_REDIRECT_URI)
                email_addr = token_data.get("emailAddress") or "Gmail"
                try:
                    if get_default_chat_id():
                        send_message(
                            f"✅ <b>Gmail connected</b>\n"
                            f"Account: <code>{html.escape(str(email_addr))}</code>\n"
                            f"Use /unread or /inbox."
                        )
                except Exception:
                    pass
                self._send_html(
                    render_dashboard_html(
                        f"Gmail connected as {email_addr}. OAuth token saved on this machine.",
                        "ok",
                    )
                )
            except Exception as e:
                self._send_html(render_dashboard_html(f"OAuth exchange failed: {e}", "err"), status=500)
            return

        self._send_json({"error": "Not found"}, status=404)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/configure":
            self._send_json({"error": "Not found"}, status=404)
            return

        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        form = urllib.parse.parse_qs(raw)
        client_id = (form.get("google_client_id") or [""])[0].strip()
        client_secret = (form.get("google_client_secret") or [""])[0].strip()

        updates: Dict[str, str] = {"GOOGLE_REDIRECT_URI": DEFAULT_REDIRECT_URI}
        if client_id:
            updates["GOOGLE_CLIENT_ID"] = client_id
        if client_secret:
            updates["GOOGLE_CLIENT_SECRET"] = client_secret

        save_env_updates(updates)

        if not oauth_configured():
            self.send_response(302)
            self.send_header(
                "Location",
                "/?type=err&msg="
                + urllib.parse.quote(
                    "That client ID is invalid or missing. Paste your own Google Cloud OAuth client."
                ),
            )
            self.end_headers()
            return

        self.send_response(302)
        self.send_header(
            "Location",
            "/?type=ok&msg="
            + urllib.parse.quote(
                "OAuth client saved. Continue with Sign in with Google."
            ),
        )
        self.end_headers()


def background_bridge_loop() -> None:
    """Continuously poll Telegram commands and periodically sync unread Sarabyx Gmail messages."""
    try:
        register_bot_commands()
    except Exception:
        pass

    last_gmail_sync = 0.0
    while True:
        try:
            process_telegram_updates(timeout=4)
        except Exception:
            pass

        now = time.time()
        env = load_env()
        try:
            poll_interval = max(10, int(env.get("POLL_INTERVAL_SECONDS") or 30))
        except ValueError:
            poll_interval = 30

        if now - last_gmail_sync >= poll_interval:
            last_gmail_sync = now
            try:
                sync_new_unread_emails_to_telegram(max_check=10)
            except Exception:
                pass

        time.sleep(2)


def main() -> None:
    # Scrub banned sample client + pin redirect URI — never invent secrets.
    save_env_updates({"GOOGLE_REDIRECT_URI": DEFAULT_REDIRECT_URI})
    env = load_env()
    port = int(env.get("AGENT_PORT") or DEFAULT_PORT)

    worker = threading.Thread(target=background_bridge_loop, daemon=True)
    worker.start()

    server = ReusableThreadingHTTPServer(("127.0.0.1", port), AgentHTTPHandler)
    print(f"Gmail ↔ Telegram bridge at http://127.0.0.1:{port}")
    if oauth_configured():
        print(f"OAuth start: http://127.0.0.1:{port}/auth/start")
    else:
        print("OAuth client not configured — open the page and paste your GCP client ID/secret.")
    server.serve_forever()


if __name__ == "__main__":
    main()
