"""Natural-language + Gemini chat layer over Gmail actions."""
from __future__ import annotations

import html
import json
import re
from typing import Any, Dict, List, Optional, Tuple

from gemini_client import GeminiClientError, gemini_configured, generate_content
from gmail_client import (
    create_draft,
    get_auth_status,
    get_message,
    list_messages,
    mark_as_read,
    reply_to_message,
    send_email,
)

SYSTEM_PROMPT = """You are a helpful Gmail assistant talking through Telegram.
You can read, search, summarize, mark read, reply, send, and draft email for the connected mailbox.
Be concise. Prefer short Telegram-friendly answers.
When the user wants mail actions, call the available tools.
Never invent email contents — use tools.
For send/reply/draft, confirm clearly what you did.
If Gmail is not connected, tell them to open http://127.0.0.1:8765 and sign in with Google.
"""

GMAIL_TOOLS = [
    {
        "function_declarations": [
            {
                "name": "list_unread",
                "description": "List recent unread inbox emails.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "count": {
                            "type": "integer",
                            "description": "How many unread emails to fetch (1-10).",
                        }
                    },
                },
            },
            {
                "name": "list_inbox",
                "description": "List latest inbox emails (read or unread).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "count": {
                            "type": "integer",
                            "description": "How many emails to fetch (1-10).",
                        }
                    },
                },
            },
            {
                "name": "search_mail",
                "description": "Search Gmail with a Gmail query string.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Gmail search query, e.g. from:google.com newer_than:7d",
                        },
                        "count": {"type": "integer"},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "read_email",
                "description": "Read one email by message id, including body.",
                "parameters": {
                    "type": "object",
                    "properties": {"msg_id": {"type": "string"}},
                    "required": ["msg_id"],
                },
            },
            {
                "name": "mark_read",
                "description": "Mark one email as read by message id.",
                "parameters": {
                    "type": "object",
                    "properties": {"msg_id": {"type": "string"}},
                    "required": ["msg_id"],
                },
            },
            {
                "name": "reply_email",
                "description": "Reply in-thread to an email.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "msg_id": {"type": "string"},
                        "text": {"type": "string"},
                    },
                    "required": ["msg_id", "text"],
                },
            },
            {
                "name": "send_email",
                "description": "Send a new email.",
                "parameters": {
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
                "name": "draft_email",
                "description": "Create a Gmail draft.",
                "parameters": {
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
                "name": "gmail_status",
                "description": "Check whether Gmail OAuth is connected and which address.",
                "parameters": {"type": "object", "properties": {}},
            },
        ]
    }
]


def _format_list(title: str, messages: List[Dict[str, Any]]) -> str:
    if not messages:
        return f"<b>{html.escape(title)}</b>\nNo matching messages."
    lines = [f"<b>{html.escape(title)} ({len(messages)})</b>", ""]
    for idx, m in enumerate(messages, 1):
        mid = html.escape(str(m.get("id") or ""))
        sender = html.escape(str(m.get("from") or "Unknown"))
        subj = html.escape(str(m.get("subject") or "(No Subject)"))
        snip = html.escape(str(m.get("snippet") or "")[:120])
        badge = "• unread · " if m.get("unread") else "• "
        lines.append(
            f"{idx}. {badge}<b>{subj}</b>\n"
            f"   From: {sender}\n"
            f"   id <code>{mid}</code>\n"
            f"   {snip}"
        )
        lines.append("")
    return "\n".join(lines).strip()


def _run_tool(name: str, args: Dict[str, Any]) -> Tuple[str, Any]:
    count = max(1, min(int(args.get("count") or 5), 10))

    if name == "list_unread":
        msgs = list_messages(query="is:unread in:inbox", max_results=count)
        return _format_list("Unread", msgs), {"messages": msgs}

    if name == "list_inbox":
        msgs = list_messages(query="in:inbox", max_results=count)
        return _format_list("Inbox", msgs), {"messages": msgs}

    if name == "search_mail":
        query = str(args.get("query") or "").strip()
        if not query:
            return "Search query is empty.", {"ok": False}
        msgs = list_messages(query=query, max_results=count)
        return _format_list(f"Search: {query}", msgs), {"messages": msgs}

    if name == "read_email":
        mid = str(args.get("msg_id") or "").strip()
        msg = get_message(mid, include_body=True)
        body = html.escape(str(msg.get("body") or msg.get("snippet") or "")[:3500])
        return (
            f"<b>{html.escape(str(msg.get('subject') or '(No Subject)'))}</b>\n"
            f"From: {html.escape(str(msg.get('from') or ''))}\n"
            f"Date: {html.escape(str(msg.get('date') or ''))}\n"
            f"id <code>{html.escape(mid)}</code>\n\n{body}",
            msg,
        )

    if name == "mark_read":
        mid = str(args.get("msg_id") or "").strip()
        mark_as_read(mid)
        return f"Marked <code>{html.escape(mid)}</code> as read.", {"ok": True}

    if name == "reply_email":
        mid = str(args.get("msg_id") or "").strip()
        text = str(args.get("text") or "").strip()
        res = reply_to_message(mid, text)
        return (
            f"Reply sent.\nTo: {html.escape(str(res.get('replied_to') or ''))}\n"
            f"Subject: {html.escape(str(res.get('subject') or ''))}",
            res,
        )

    if name == "send_email":
        to_addr = str(args.get("to") or "").strip()
        subject = str(args.get("subject") or "").strip()
        body = str(args.get("body") or "").strip()
        sent = send_email(to=to_addr, subject=subject, body=body)
        return (
            f"Email sent to {html.escape(to_addr)}\n"
            f"Subject: {html.escape(subject)}\n"
            f"id <code>{html.escape(str(sent.get('id') or ''))}</code>",
            sent,
        )

    if name == "draft_email":
        to_addr = str(args.get("to") or "").strip()
        subject = str(args.get("subject") or "").strip()
        body = str(args.get("body") or "").strip()
        dr = create_draft(to=to_addr, subject=subject, body=body)
        return (
            f"Draft saved for {html.escape(to_addr)}\n"
            f"Subject: {html.escape(subject)}\n"
            f"draft <code>{html.escape(str(dr.get('id') or ''))}</code>",
            dr,
        )

    if name == "gmail_status":
        g = get_auth_status()
        if g.get("authenticated"):
            return (
                f"Gmail connected as <code>{html.escape(str(g.get('email_address') or ''))}</code>",
                g,
            )
        return (
            "Gmail is not connected. Open http://127.0.0.1:8765 and sign in with Google.",
            g,
        )

    return f"Unknown tool: {html.escape(name)}", {"ok": False}


def try_fast_nl(text: str) -> Optional[str]:
    """Map common plain-English phrases to Gmail actions without Gemini."""
    t = text.strip().lower()
    if not t:
        return None

    if re.search(r"\b(status|am i connected|oauth)\b", t):
        html_out, _ = _run_tool("gmail_status", {})
        return html_out

    if re.search(r"\b(unread|new mail|new email|anything new)\b", t):
        html_out, _ = _run_tool("list_unread", {"count": 5})
        return html_out

    if re.fullmatch(r"(inbox|latest|recent( emails?)?)", t) or re.search(
        r"\b(show|list|check)\b.*\binbox\b", t
    ):
        html_out, _ = _run_tool("list_inbox", {"count": 5})
        return html_out

    m = re.search(r"\b(?:search|find|look for)\b\s+(.+)$", t)
    if m:
        query = m.group(1).strip(" .")
        if query:
            html_out, _ = _run_tool("search_mail", {"query": query, "count": 5})
            return html_out

    m = re.search(r"\b(?:read|open)\b\s+([a-zA-Z0-9_-]{6,})\b", t)
    if m:
        html_out, _ = _run_tool("read_email", {"msg_id": m.group(1)})
        return html_out

    m = re.search(r"\b(?:mark\s*read|mark as read)\b\s+([a-zA-Z0-9_-]{6,})\b", t)
    if m:
        html_out, _ = _run_tool("mark_read", {"msg_id": m.group(1)})
        return html_out

    return None


def handle_chat_message(
    text: str,
    history: Optional[List[Dict[str, str]]] = None,
) -> Tuple[str, List[Dict[str, str]]]:
    """
    Handle a free-text Telegram message.
    Returns (html_reply, updated_history).
    """
    history = list(history or [])
    fast = try_fast_nl(text)
    if fast is not None:
        history.append({"role": "user", "text": text})
        history.append({"role": "model", "text": re.sub(r"<[^>]+>", "", fast)[:500]})
        return fast, history[-12:]

    if not gemini_configured():
        return (
            "I can do basic mail commands in plain English already "
            "(try “show unread” or “search from google”).\n"
            "For full chat, add <code>GEMINI_API_KEY</code> in the local .env.",
            history,
        )

    auth = get_auth_status()
    contents: List[Dict[str, Any]] = []
    for turn in history[-10:]:
        role = "user" if turn.get("role") == "user" else "model"
        contents.append({"role": role, "parts": [{"text": turn.get("text") or ""}]})
    contents.append({"role": "user", "parts": [{"text": text}]})

    system = SYSTEM_PROMPT
    if auth.get("authenticated"):
        system += f"\nConnected Gmail: {auth.get('email_address')}"
    else:
        system += "\nGmail OAuth is NOT connected yet."

    try:
        first = generate_content(
            contents,
            system_instruction=system,
            tools=GMAIL_TOOLS,
            temperature=0.2,
            max_output_tokens=900,
        )
    except GeminiClientError as e:
        return f"Gemini error: {html.escape(str(e)[:400])}", history

    tool_notes: List[str] = []
    model_parts: List[Dict[str, Any]] = []
    calls = first.get("function_calls") or []

    if calls:
        # Keep the model's function-call turn for the follow-up request.
        raw = first.get("raw_candidate") or {}
        model_content = raw.get("content") or {
            "role": "model",
            "parts": [
                {"functionCall": {"name": c["name"], "args": c["args"]}} for c in calls
            ],
        }
        contents.append(model_content)

        response_parts = []
        for call in calls[:4]:
            name = call["name"]
            args = call.get("args") or {}
            try:
                human, payload = _run_tool(name, args)
                tool_notes.append(human)
                response_parts.append(
                    {
                        "functionResponse": {
                            "name": name,
                            "response": {
                                "result": re.sub(r"<[^>]+>", "", human)[:2500],
                                "ok": True,
                                "data": payload
                                if isinstance(payload, dict)
                                and "messages" not in payload
                                else {"ok": True},
                            },
                        }
                    }
                )
            except Exception as e:
                err = str(e)
                tool_notes.append(f"Tool {html.escape(name)} failed: {html.escape(err)}")
                response_parts.append(
                    {
                        "functionResponse": {
                            "name": name,
                            "response": {"ok": False, "error": err[:500]},
                        }
                    }
                )

        contents.append({"role": "user", "parts": response_parts})
        try:
            second = generate_content(
                contents,
                system_instruction=system,
                tools=GMAIL_TOOLS,
                temperature=0.3,
                max_output_tokens=700,
            )
            reply = second.get("text") or ""
        except GeminiClientError:
            reply = ""

        if not reply:
            reply = "\n\n".join(tool_notes) if tool_notes else "Done."
        elif tool_notes and len(calls) == 1 and calls[0]["name"] in {
            "list_unread",
            "list_inbox",
            "search_mail",
            "read_email",
        }:
            # Prefer structured mail listing for list/read tools.
            reply = tool_notes[0]
    else:
        reply = first.get("text") or "I’m here — ask about your inbox or say “show unread”."

    # Escape model prose unless it already contains our HTML from tools.
    if "<" not in reply:
        reply = html.escape(reply)

    history.append({"role": "user", "text": text})
    history.append({"role": "model", "text": re.sub(r"<[^>]+>", "", reply)[:800]})
    return reply, history[-12:]
