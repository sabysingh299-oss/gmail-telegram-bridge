"""Telegram Bot API Client & Gmail Notification Formatter for Sarabyx."""
import html
import json
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from config import load_env, save_env_updates


class TelegramClientError(Exception):
    """Raised when Telegram Bot API operations fail."""


def _get_bot_token() -> str:
    env = load_env()
    token = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        raise TelegramClientError(
            "TELEGRAM_BOT_TOKEN is not configured in .env yet."
        )
    return token


def get_default_chat_id() -> str:
    env = load_env()
    return (env.get("TELEGRAM_CHAT_ID") or "").strip()


def _tg_request(method: str, payload: Optional[Dict[str, Any]] = None, timeout: int = 25) -> Dict[str, Any]:
    """Perform a request to the Telegram Bot API."""
    token = _get_bot_token()
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise TelegramClientError(f"Telegram API HTTP {e.code} on {method}: {err_body}") from e

    if not result.get("ok"):
        raise TelegramClientError(f"Telegram API error on {method}: {result}")
    return result.get("result")


def get_bot_status() -> Dict[str, Any]:
    """Check Telegram Bot connection status and return bot profile."""
    env = load_env()
    token = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (env.get("TELEGRAM_CHAT_ID") or "").strip()
    status: Dict[str, Any] = {
        "bot_token_configured": bool(token),
        "chat_id_configured": bool(chat_id),
        "chat_id": chat_id or None,
        "connected": False,
    }
    if not token:
        return status

    try:
        me = _tg_request("getMe", {})
        status["connected"] = True
        status["bot_id"] = me.get("id")
        status["bot_username"] = me.get("username")
        status["bot_name"] = me.get("first_name")
    except Exception as e:
        status["error"] = str(e)
    return status


def send_message(
    text: str,
    chat_id: Optional[str] = None,
    parse_mode: Optional[str] = "HTML",
    reply_markup: Optional[Dict[str, Any]] = None,
    disable_web_page_preview: bool = True,
) -> Dict[str, Any]:
    """Send a message via the Sarabyx Telegram Bot."""
    target_chat = (chat_id or get_default_chat_id()).strip()
    if not target_chat:
        raise TelegramClientError(
            "TELEGRAM_CHAT_ID is not set. Send /start to your Telegram bot or set TELEGRAM_CHAT_ID in .env."
        )
    # Telegram max message length is 4096 chars
    if len(text) > 4000:
        text = text[:3990] + "\n...(truncated)"

    payload: Dict[str, Any] = {
        "chat_id": target_chat,
        "text": text,
        "disable_web_page_preview": disable_web_page_preview,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup

    return _tg_request("sendMessage", payload)


def answer_callback_query(callback_query_id: str, text: str = "") -> Any:
    """Acknowledge an inline button click in Telegram."""
    return _tg_request(
        "answerCallbackQuery",
        {"callback_query_id": callback_query_id, "text": text[:190]},
    )


def register_bot_commands() -> Any:
    """Register the bot command menu in Telegram."""
    commands = [
        {"command": "start", "description": "Connect chat & show OAuth + command menu"},
        {"command": "auth", "description": "Get OAuth Redirect button to connect Sarabyx Gmail"},
        {"command": "status", "description": "Check Gmail OAuth & Telegram Bot status"},
        {"command": "unread", "description": "List unread emails in Sarabyx Gmail"},
        {"command": "inbox", "description": "List latest 5 emails in Inbox"},
        {"command": "search", "description": "Search Gmail (e.g. /search subject:enquiry)"},
        {"command": "read", "description": "Read full email by ID (/read <msg_id>)"},
        {"command": "reply", "description": "Reply to an email (/reply <msg_id> <text>)"},
        {"command": "send", "description": "Send email (/send to | subject | body)"},
        {"command": "draft", "description": "Create draft (/draft to | subject | body)"},
        {"command": "help", "description": "Show commands + chat tips"},
    ]
    return _tg_request("setMyCommands", {"commands": commands})


def get_updates(offset: Optional[int] = None, timeout: int = 10) -> List[Dict[str, Any]]:
    """Fetch new incoming messages & button clicks sent to the Telegram Bot."""
    payload: Dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
    if offset is not None and offset > 0:
        payload["offset"] = offset
    res = _tg_request("getUpdates", payload, timeout=timeout + 10)
    updates = res if isinstance(res, list) else []

    # Auto-save TELEGRAM_CHAT_ID if not configured yet and a message arrives
    if updates and not get_default_chat_id():
        for u in updates:
            msg = u.get("message") or (u.get("callback_query") or {}).get("message") or {}
            cid = str((msg.get("chat") or {}).get("id") or "")
            if cid:
                save_env_updates({"TELEGRAM_CHAT_ID": cid})
                break

    return updates


def format_email_alert_html(msg: Dict[str, Any], include_body: bool = False) -> str:
    """Format a Gmail message into clean Telegram HTML."""
    msg_id = html.escape(str(msg.get("id") or ""))
    sender = html.escape(str(msg.get("from") or "Unknown Sender"))
    subject = html.escape(str(msg.get("subject") or "(No Subject)"))
    date_str = html.escape(str(msg.get("date") or ""))
    content = str(msg.get("body") if include_body else (msg.get("snippet") or ""))
    if len(content) > 1800:
        content = content[:1800] + "..."
    escaped_content = html.escape(content)

    header = "📧 <b>Sarabyx Gmail — Full Message</b>" if include_body else "📬 <b>New Unread Email — Sarabyx Gmail</b>"
    lines = [
        header,
        f"<b>From:</b> {sender}",
        f"<b>Subject:</b> {subject}",
    ]
    if date_str:
        lines.append(f"<b>Date:</b> {date_str}")
    lines.append(f"<b>ID:</b> <code>{msg_id}</code>")
    lines.append("")
    lines.append(escaped_content or "<i>(Empty message body)</i>")
    lines.append("")
    lines.append(f"💡 <i>Reply from Telegram:</i> <code>/reply {msg_id} Your message here</code>")
    return "\n".join(lines)


def send_email_notification(msg: Dict[str, Any], chat_id: Optional[str] = None) -> Dict[str, Any]:
    """Send a rich Telegram notification for a Gmail message with inline action buttons."""
    msg_id = str(msg.get("id") or "")
    text = format_email_alert_html(msg, include_body=False)
    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "📖 Read Full", "callback_data": f"read:{msg_id}"},
                {"text": "✅ Mark Read", "callback_data": f"markread:{msg_id}"},
            ]
        ]
    }
    return send_message(text=text, chat_id=chat_id, parse_mode="HTML", reply_markup=reply_markup)
