"""Two-Way Bridge between Gmail (GCP OAuth) and Telegram Bot."""
import html
from typing import Any, Dict, List, Optional

from chat_agent import handle_chat_message
from config import load_state, save_env_updates, save_state
from gmail_client import (
    build_oauth_url,
    create_draft,
    get_auth_status,
    get_message,
    list_messages,
    mark_as_read,
    reply_to_message,
    send_email,
)
from telegram_client import (
    answer_callback_query,
    format_email_alert_html,
    get_bot_status,
    get_default_chat_id,
    get_updates,
    send_email_notification,
    send_message,
)

HELP_TEXT = """<b>Gmail ↔ Telegram</b>

Talk normally, e.g.:
• “show unread”
• “search from google”
• “summarize my latest emails”
• “draft a reply saying thanks”

<b>Commands:</b>
• /auth — Connect Gmail (OAuth)
• /status — Connection status
• /unread · /inbox · /search · /read · /markread
• /reply · /send · /draft
• /help — This menu"""


def _build_oauth_reply_markup() -> Optional[Dict[str, Any]]:
    """Build Telegram inline keyboard with Google OAuth Redirect button if GCP client is configured."""
    buttons = []
    try:
        oauth_url = build_oauth_url()
        buttons.append([{"text": "Authorize Gmail (OAuth)", "url": oauth_url}])
    except Exception:
        pass
    buttons.append([{"text": "Check OAuth Status", "callback_data": "check_oauth"}])
    return {"inline_keyboard": buttons}


def sync_new_unread_emails_to_telegram(max_check: int = 10, force_resend: bool = False) -> Dict[str, Any]:
    """Check Sarabyx Gmail for unread emails and push new ones to Telegram."""
    gmail_status = get_auth_status()
    if not gmail_status.get("authenticated"):
        return {"ok": False, "reason": "Gmail OAuth not authenticated yet.", "notified_count": 0}

    tg_status = get_bot_status()
    if not tg_status.get("connected") or not get_default_chat_id():
        return {"ok": False, "reason": "Telegram Bot Token or Chat ID not configured yet.", "notified_count": 0}

    state = load_state()
    notified_ids: List[str] = list(state.get("notified_ids") or [])

    unread_messages = list_messages(query="is:unread in:inbox", max_results=max_check)
    newly_notified: List[str] = []

    # Process oldest to newest
    for msg in reversed(unread_messages):
        mid = str(msg.get("id") or "")
        if not mid:
            continue
        if not force_resend and mid in notified_ids:
            continue
        send_email_notification(msg)
        notified_ids.append(mid)
        newly_notified.append(mid)

    state["notified_ids"] = notified_ids
    save_state(state)
    return {
        "ok": True,
        "checked_unread": len(unread_messages),
        "notified_count": len(newly_notified),
        "notified_ids": newly_notified,
    }


def _format_message_list_html(title: str, messages: List[Dict[str, Any]]) -> str:
    if not messages:
        return f"📭 <b>{html.escape(title)}</b>\nNo matching messages found."
    lines = [f"📬 <b>{html.escape(title)} ({len(messages)})</b>", ""]
    for idx, m in enumerate(messages, 1):
        mid = html.escape(str(m.get("id") or ""))
        sender = html.escape(str(m.get("from") or "Unknown"))
        subj = html.escape(str(m.get("subject") or "(No Subject)"))
        snip = html.escape(str(m.get("snippet") or "")[:120])
        unread_badge = "🟢 " if m.get("unread") else ""
        lines.append(
            f"{idx}. {unread_badge}<b>{subj}</b>\n"
            f"   👤 {sender}\n"
            f"   🆔 <code>{mid}</code> — <i>{snip}</i>\n"
            f"   👉 Read: <code>/read {mid}</code>"
        )
        lines.append("")
    return "\n".join(lines).strip()


def _handle_command(chat_id: str, text: str) -> None:
    parts = text.strip().split(maxsplit=1)
    cmd = parts[0].lower().split("@")[0]
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd in ("/start", "/help"):
        save_env_updates({"TELEGRAM_CHAT_ID": chat_id})
        bot_name = str(get_bot_status().get("bot_username") or "your_bot")
        send_message(
            f"✅ <b>Connected to @{html.escape(bot_name)}</b> "
            f"(Chat ID: <code>{html.escape(chat_id)}</code>)\n"
            f"🔗 <b>Local setup:</b> <code>http://127.0.0.1:8765</code>\n\n{HELP_TEXT}",
            chat_id=chat_id,
            reply_markup=_build_oauth_reply_markup(),
        )
        return

    if cmd == "/auth":
        g = get_auth_status()
        if g.get("authenticated"):
            send_message(
                f"✅ <b>Sarabyx Gmail is already authorized!</b>\n"
                f"Account: <code>{html.escape(str(g.get('email_address') or ''))}</code>\n\n"
                f"Want to re-authenticate? Click below or open <code>http://127.0.0.1:8765/auth/start</code>",
                chat_id=chat_id,
                reply_markup=_build_oauth_reply_markup(),
            )
        elif g.get("gcp_client_configured"):
            oauth_url = build_oauth_url()
            send_message(
                f"🔐 <b>Fetch Sarabyx Gmail OAuth Token via Redirect</b>\n\n"
                f"Click the button below to sign into your Sarabyx Google account. It will automatically redirect to "
                f"<code>http://127.0.0.1:8765/oauth2callback</code> and save your <code>token.json</code>!",
                chat_id=chat_id,
                reply_markup={
                    "inline_keyboard": [
                        [{"text": "🔐 Authorize Sarabyx Gmail (OAuth Redirect)", "url": oauth_url}],
                        [{"text": "🔄 Check OAuth Status", "callback_data": "check_oauth"}],
                    ]
                },
            )
        else:
            send_message(
                f"⚠️ <b>GCP Client ID &amp; Secret Needed First</b>\n\n"
                f"1. Open <code>http://127.0.0.1:8765</code> in your browser.\n"
                f"2. Paste your GCP <b>Client ID</b> &amp; <b>Client Secret</b> (or upload <code>client_secret.json</code>).\n"
                f"3. Click <b>🔐 Save &amp; Fetch OAuth via Redirect</b>!",
                chat_id=chat_id,
                reply_markup=_build_oauth_reply_markup(),
            )
        return

    if cmd == "/status":
        g = get_auth_status()
        t = get_bot_status()
        g_line = (
            f"✅ Connected ({html.escape(str(g.get('email_address') or ''))})"
            if g.get("authenticated")
            else "⚠️ Not Authorized Yet (Click button below or open http://127.0.0.1:8765)"
        )
        t_line = (
            f"✅ @{html.escape(str(t.get('bot_username') or ''))} (Chat ID: <code>{html.escape(chat_id)}</code>)"
            if t.get("connected")
            else "⚠️ Not Connected"
        )
        send_message(
            f"📊 <b>Sarabyx Agent Status</b>\n\n"
            f"• <b>Gmail (GCP OAuth):</b> {g_line}\n"
            f"• <b>Telegram Bot:</b> {t_line}",
            chat_id=chat_id,
            reply_markup=_build_oauth_reply_markup(),
        )
        return

    if cmd == "/unread":
        msgs = list_messages(query="is:unread", max_results=5)
        send_message(_format_message_list_html("Unread Emails", msgs), chat_id=chat_id)
        return

    if cmd == "/inbox":
        count = 5
        if arg.isdigit():
            count = max(1, min(int(arg), 15))
        msgs = list_messages(query="in:inbox", max_results=count)
        send_message(_format_message_list_html("Latest Inbox Emails", msgs), chat_id=chat_id)
        return

    if cmd == "/search":
        if not arg:
            send_message("⚠️ Usage: <code>/search from:someone subject:enquiry</code>", chat_id=chat_id)
            return
        msgs = list_messages(query=arg, max_results=5)
        send_message(_format_message_list_html(f"Search: {arg}", msgs), chat_id=chat_id)
        return

    if cmd == "/read":
        if not arg:
            send_message("⚠️ Usage: <code>/read &lt;msg_id&gt;</code>", chat_id=chat_id)
            return
        msg = get_message(arg.split()[0], include_body=True)
        send_message(format_email_alert_html(msg, include_body=True), chat_id=chat_id)
        return

    if cmd == "/markread":
        if not arg:
            send_message("⚠️ Usage: <code>/markread &lt;msg_id&gt;</code>", chat_id=chat_id)
            return
        mid = arg.split()[0]
        mark_as_read(mid)
        send_message(f"✅ Marked email <code>{html.escape(mid)}</code> as read.", chat_id=chat_id)
        return

    if cmd == "/reply":
        subparts = arg.split(maxsplit=1)
        if len(subparts) < 2:
            send_message("⚠️ Usage: <code>/reply &lt;msg_id&gt; Your reply message</code>", chat_id=chat_id)
            return
        mid, reply_body = subparts[0], subparts[1]
        res = reply_to_message(mid, reply_body)
        send_message(
            f"📤 <b>Reply Sent!</b>\n"
            f"<b>To:</b> {html.escape(str(res.get('replied_to') or ''))}\n"
            f"<b>Subject:</b> {html.escape(str(res.get('subject') or ''))}",
            chat_id=chat_id,
        )
        return

    if cmd in ("/send", "/draft"):
        segments = [s.strip() for s in arg.split("|", 2)]
        if len(segments) < 3:
            send_message(
                f"⚠️ Usage: <code>{cmd} recipient@domain.com | Subject Line | Message body</code>",
                chat_id=chat_id,
            )
            return
        to_addr, subj, body = segments[0], segments[1], segments[2]
        if cmd == "/send":
            sent = send_email(to=to_addr, subject=subj, body=body)
            send_message(
                f"📤 <b>Email Sent from Sarabyx Gmail!</b>\n"
                f"<b>To:</b> {html.escape(to_addr)}\n"
                f"<b>Subject:</b> {html.escape(subj)}\n"
                f"<b>Message ID:</b> <code>{html.escape(str(sent.get('id') or ''))}</code>",
                chat_id=chat_id,
            )
        else:
            dr = create_draft(to=to_addr, subject=subj, body=body)
            send_message(
                f"📝 <b>Gmail Draft Created!</b>\n"
                f"<b>To:</b> {html.escape(to_addr)}\n"
                f"<b>Subject:</b> {html.escape(subj)}\n"
                f"<b>Draft ID:</b> <code>{html.escape(str(dr.get('id') or ''))}</code>",
                chat_id=chat_id,
            )
        return

    send_message("Unknown command. Send /help or just ask in plain English.", chat_id=chat_id)


def _safe_answer_callback(cq_id: str, text: str = "") -> None:
    if not cq_id:
        return
    try:
        answer_callback_query(cq_id, text)
    except Exception:
        # Telegram expires callback queries quickly; ignore stale ones.
        pass


def process_telegram_updates(timeout: int = 5) -> Dict[str, Any]:
    """Poll Telegram for commands, chat, or inline button clicks."""
    tg_status = get_bot_status()
    if not tg_status.get("connected"):
        return {"ok": False, "processed": 0, "reason": "Telegram Bot not connected."}

    state = load_state()
    offset = int(state.get("telegram_offset") or 0)
    chat_histories: Dict[str, List[Dict[str, str]]] = dict(state.get("chat_histories") or {})
    updates = get_updates(offset=offset, timeout=timeout)
    processed = 0

    for u in updates:
        uid = int(u.get("update_id") or 0)
        if uid >= offset:
            offset = uid + 1

        try:
            if "callback_query" in u:
                cq = u["callback_query"]
                cq_id = str(cq.get("id") or "")
                data = str(cq.get("data") or "")
                msg = cq.get("message") or {}
                chat_id = str((msg.get("chat") or {}).get("id") or get_default_chat_id())

                # Acknowledge immediately so Telegram doesn't expire the query.
                _safe_answer_callback(cq_id, "Working…")

                if data.startswith("read:"):
                    mid = data.split(":", 1)[1]
                    full_msg = get_message(mid, include_body=True)
                    send_message(format_email_alert_html(full_msg, include_body=True), chat_id=chat_id)
                elif data.startswith("markread:"):
                    mid = data.split(":", 1)[1]
                    mark_as_read(mid)
                    send_message(f"Marked <code>{html.escape(mid)}</code> as read.", chat_id=chat_id)
                elif data == "check_oauth":
                    g = get_auth_status()
                    if g.get("authenticated"):
                        send_message(
                            f"Gmail connected: <code>{html.escape(str(g.get('email_address') or ''))}</code>",
                            chat_id=chat_id,
                        )
                    else:
                        _handle_command(chat_id, "/auth")
                processed += 1

            elif "message" in u:
                msg = u["message"]
                chat_id = str((msg.get("chat") or {}).get("id") or "")
                text = (msg.get("text") or "").strip()
                if not chat_id or not text:
                    continue
                save_env_updates({"TELEGRAM_CHAT_ID": chat_id})
                if text.startswith("/"):
                    _handle_command(chat_id, text)
                else:
                    history = chat_histories.get(chat_id) or []
                    reply, history = handle_chat_message(text, history)
                    chat_histories[chat_id] = history
                    send_message(reply, chat_id=chat_id)
                processed += 1
        except Exception as e:
            fallback_chat = get_default_chat_id()
            if fallback_chat:
                try:
                    send_message(f"Error: {html.escape(str(e)[:500])}", chat_id=fallback_chat)
                except Exception:
                    pass

    # Keep histories small
    trimmed = {cid: turns[-12:] for cid, turns in list(chat_histories.items())[-20:]}
    state["telegram_offset"] = offset
    state["chat_histories"] = trimmed
    save_state(state)
    return {"ok": True, "processed": processed, "next_offset": offset}
