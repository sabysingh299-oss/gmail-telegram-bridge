#!/usr/bin/env python3
"""Model Context Protocol (MCP) Server for Sarabyx Gmail (GCP OAuth) + Telegram Bot."""
import json
import sys
from typing import Any, Dict, List

from bridge_worker import process_telegram_updates, sync_new_unread_emails_to_telegram
from config import save_env_updates
from gmail_client import (
    build_oauth_url,
    create_draft,
    exchange_code_for_token,
    get_auth_status,
    get_message,
    list_messages,
    mark_as_read,
    reply_to_message,
    send_email,
)
from telegram_client import (
    get_bot_status,
    send_email_notification,
    send_message,
)

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "sarabyx_status",
        "description": "Check the connection and OAuth status of both Sarabyx Gmail (GCP OAuth) and the Sarabyx Telegram Bot.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "sarabyx_configure_credentials",
        "description": "Save GCP OAuth credentials (GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, SARABYX_GMAIL_ADDRESS) and/or Telegram Bot credentials (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID) into .env.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "google_client_id": {"type": "string"},
                "google_client_secret": {"type": "string"},
                "sarabyx_gmail_address": {"type": "string"},
                "telegram_bot_token": {"type": "string"},
                "telegram_chat_id": {"type": "string"},
            },
        },
    },
    {
        "name": "gmail_get_oauth_url",
        "description": "Generate the Google Cloud OAuth 2.0 authorization URL to connect the Sarabyx Gmail account.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "login_hint": {"type": "string", "description": "Optional Gmail address hint"}
            },
        },
    },
    {
        "name": "gmail_exchange_oauth_code",
        "description": "Exchange the Google OAuth 2.0 authorization code for an access_token and refresh_token (saved to token.json).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Authorization code from OAuth callback"}
            },
            "required": ["code"],
        },
    },
    {
        "name": "gmail_list_messages",
        "description": "List or search emails in Sarabyx Gmail using Gmail search syntax (e.g. 'is:unread', 'in:inbox', 'from:notify.sarabyx.in').",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "default": "is:unread"},
                "max_results": {"type": "integer", "default": 10},
            },
        },
    },
    {
        "name": "gmail_read_message",
        "description": "Read the full body, headers, and metadata of a specific Sarabyx Gmail message by ID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string", "description": "Gmail message ID"}
            },
            "required": ["message_id"],
        },
    },
    {
        "name": "gmail_send_email",
        "description": "Send a new email from the Sarabyx Gmail account.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "gmail_reply_email",
        "description": "Reply directly to an existing Sarabyx Gmail message in its thread and mark it as read.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["message_id", "body"],
        },
    },
    {
        "name": "gmail_create_draft",
        "description": "Create a draft email in Sarabyx Gmail.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "gmail_mark_as_read",
        "description": "Mark a Sarabyx Gmail message as read (removes UNREAD label).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string"}
            },
            "required": ["message_id"],
        },
    },
    {
        "name": "telegram_send_message",
        "description": "Send a formatted message via the Sarabyx Telegram Bot.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "chat_id": {"type": "string", "description": "Optional override chat_id"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "telegram_notify_email",
        "description": "Forward a specific Sarabyx Gmail message to the Telegram Bot with interactive action buttons.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string"},
                "chat_id": {"type": "string"},
            },
            "required": ["message_id"],
        },
    },
    {
        "name": "sync_unread_gmail_to_telegram",
        "description": "Check Sarabyx Gmail for unread emails and push new alerts to the Telegram Bot, plus process any pending Telegram commands.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "max_check": {"type": "integer", "default": 10},
                "force_resend": {"type": "boolean", "default": False},
            },
        },
    },
]


def handle_tool_call(name: str, args: Dict[str, Any]) -> Any:
    if name == "sarabyx_status":
        return {"gmail": get_auth_status(), "telegram": get_bot_status()}

    if name == "sarabyx_configure_credentials":
        updates: Dict[str, str] = {}
        mapping = {
            "google_client_id": "GOOGLE_CLIENT_ID",
            "google_client_secret": "GOOGLE_CLIENT_SECRET",
            "sarabyx_gmail_address": "SARABYX_GMAIL_ADDRESS",
            "telegram_bot_token": "TELEGRAM_BOT_TOKEN",
            "telegram_chat_id": "TELEGRAM_CHAT_ID",
        }
        for k, env_key in mapping.items():
            if args.get(k) is not None:
                updates[env_key] = str(args[k])
        save_env_updates(updates)
        return {"updated_keys": list(updates.keys()), "status": {"gmail": get_auth_status(), "telegram": get_bot_status()}}

    if name == "gmail_get_oauth_url":
        return {"oauth_url": build_oauth_url(login_hint=args.get("login_hint"))}

    if name == "gmail_exchange_oauth_code":
        return exchange_code_for_token(args["code"])

    if name == "gmail_list_messages":
        return list_messages(
            query=args.get("query", "is:unread"),
            max_results=int(args.get("max_results", 10)),
        )

    if name == "gmail_read_message":
        return get_message(args["message_id"], include_body=True)

    if name == "gmail_send_email":
        return send_email(to=args["to"], subject=args["subject"], body=args["body"])

    if name == "gmail_reply_email":
        return reply_to_message(msg_id=args["message_id"], body=args["body"])

    if name == "gmail_create_draft":
        return create_draft(to=args["to"], subject=args["subject"], body=args["body"])

    if name == "gmail_mark_as_read":
        return mark_as_read(args["message_id"])

    if name == "telegram_send_message":
        return send_message(text=args["text"], chat_id=args.get("chat_id"))

    if name == "telegram_notify_email":
        msg = get_message(args["message_id"], include_body=False)
        return send_email_notification(msg, chat_id=args.get("chat_id"))

    if name == "sync_unread_gmail_to_telegram":
        tg_res = process_telegram_updates(timeout=1)
        sync_res = sync_new_unread_emails_to_telegram(
            max_check=int(args.get("max_check", 10)),
            force_resend=bool(args.get("force_resend", False)),
        )
        return {"telegram_commands_processed": tg_res, "gmail_sync": sync_res}

    raise ValueError(f"Unknown tool: {name}")


def send_rpc_response(req_id: Any, result: Any = None, error: Any = None) -> None:
    msg: Dict[str, Any] = {"jsonrpc": "2.0", "id": req_id}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def main() -> None:
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue

        method = req.get("method")
        req_id = req.get("id")
        params = req.get("params") or {}

        if method == "initialize":
            send_rpc_response(
                req_id,
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "sarabyx-gmail-telegram", "version": "1.0.0"},
                },
            )
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            send_rpc_response(req_id, {"tools": TOOLS})
        elif method == "tools/call":
            tool_name = params.get("name", "")
            tool_args = params.get("arguments") or {}
            try:
                output = handle_tool_call(tool_name, tool_args)
                send_rpc_response(
                    req_id,
                    {
                        "content": [
                            {"type": "text", "text": json.dumps(output, indent=2, ensure_ascii=False)}
                        ],
                        "isError": False,
                    },
                )
            except Exception as e:
                send_rpc_response(
                    req_id,
                    {
                        "content": [{"type": "text", "text": f"Error: {e}"}],
                        "isError": True,
                    },
                )
        elif req_id is not None:
            send_rpc_response(req_id, error={"code": -32601, "message": f"Method not found: {method}"})


if __name__ == "__main__":
    main()
