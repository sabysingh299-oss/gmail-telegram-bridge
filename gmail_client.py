"""Google Cloud OAuth 2.0 & Gmail REST API Client for Sarabyx."""
import base64
import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional

from config import (
    DEFAULT_REDIRECT_URI,
    GMAIL_SCOPES,
    get_gcp_oauth_client_config,
    load_env,
    load_token,
    save_env_updates,
    save_token,
)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"


class GmailClientError(Exception):
    """Raised when Gmail OAuth or API operations fail."""


def build_oauth_url(redirect_uri: Optional[str] = None, login_hint: Optional[str] = None) -> str:
    """Build the Google OAuth 2.0 account-chooser URL for the configured client."""
    client = get_gcp_oauth_client_config()
    if not client.get("client_id") or not client.get("client_secret"):
        raise GmailClientError(
            "Missing Google Cloud OAuth client. Paste your client ID and secret on the setup page."
        )
    uri = redirect_uri or client.get("redirect_uri") or DEFAULT_REDIRECT_URI

    params = {
        "client_id": client["client_id"],
        "redirect_uri": uri,
        "response_type": "code",
        "scope": " ".join(GMAIL_SCOPES),
        "access_type": "offline",
        "prompt": "select_account consent",
        "include_granted_scopes": "true",
    }
    if login_hint:
        params["login_hint"] = login_hint

    return f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_code_for_token(code: str, redirect_uri: Optional[str] = None) -> Dict[str, Any]:
    """Exchange OAuth 2.0 authorization code for access_token & refresh_token."""
    client = get_gcp_oauth_client_config()
    if not client:
        raise GmailClientError("Missing GCP OAuth client configuration.")

    uri = redirect_uri or client.get("redirect_uri") or DEFAULT_REDIRECT_URI
    payload = urllib.parse.urlencode(
        {
            "code": code.strip(),
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
            "redirect_uri": uri,
            "grant_type": "authorization_code",
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        GOOGLE_TOKEN_URL,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            token_data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise GmailClientError(f"OAuth token exchange failed ({e.code}): {err_body}") from e

    existing = load_token() or {}
    if "refresh_token" not in token_data and "refresh_token" in existing:
        token_data["refresh_token"] = existing["refresh_token"]

    token_data["obtained_at"] = int(time.time())
    token_data["expires_at"] = int(time.time()) + int(token_data.get("expires_in", 3599)) - 60
    save_token(token_data)

    # Fetch profile to record connected Gmail address
    try:
        profile = get_profile()
        email_addr = profile.get("emailAddress", "")
        if email_addr:
            token_data["emailAddress"] = email_addr
            save_token(token_data)
            save_env_updates({"SARABYX_GMAIL_ADDRESS": email_addr})
    except Exception:
        pass

    return token_data


def get_valid_access_token() -> str:
    """Return a valid OAuth access_token, refreshing via refresh_token if expired."""
    token_data = load_token()
    if not token_data:
        raise GmailClientError(
            "Gmail OAuth token (token.json) not found. Complete the OAuth flow first."
        )

    now = int(time.time())
    expires_at = int(token_data.get("expires_at", 0))
    if token_data.get("access_token") and now < expires_at:
        return token_data["access_token"]

    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        raise GmailClientError(
            "OAuth access_token expired and no refresh_token is available. Please re-authorize."
        )

    client = get_gcp_oauth_client_config()
    if not client:
        raise GmailClientError("Missing GCP OAuth client configuration to refresh token.")

    payload = urllib.parse.urlencode(
        {
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        GOOGLE_TOKEN_URL,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            refreshed = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise GmailClientError(f"OAuth token refresh failed ({e.code}): {err_body}") from e

    token_data["access_token"] = refreshed["access_token"]
    token_data["expires_in"] = refreshed.get("expires_in", 3599)
    token_data["expires_at"] = int(time.time()) + int(token_data["expires_in"]) - 60
    if "refresh_token" in refreshed:
        token_data["refresh_token"] = refreshed["refresh_token"]
    save_token(token_data)
    return token_data["access_token"]


def _gmail_request(
    path: str,
    method: str = "GET",
    query: Optional[Dict[str, Any]] = None,
    body: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Perform an authenticated request to Gmail REST API v1."""
    access_token = get_valid_access_token()
    url = f"{GMAIL_API_BASE}{path}"
    if query:
        clean_q = {k: v for k, v in query.items() if v is not None}
        if clean_q:
            url = f"{url}?{urllib.parse.urlencode(clean_q, doseq=True)}"

    data = json.dumps(body).encode("utf-8") if body is not None else None
    client = get_gcp_oauth_client_config()
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    if client.get("quota_project"):
        headers["X-Goog-User-Project"] = client["quota_project"]
    if data is not None:
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise GmailClientError(f"Gmail API error ({e.code} on {path}): {err_body}") from e


def get_auth_status() -> Dict[str, Any]:
    """Return current GCP OAuth configuration & Gmail connection status."""
    client = get_gcp_oauth_client_config()
    token = load_token()
    status: Dict[str, Any] = {
        "gcp_client_configured": bool(client),
        "client_id_preview": (client["client_id"][:18] + "...") if client else None,
        "redirect_uri": client["redirect_uri"] if client else DEFAULT_REDIRECT_URI,
        "token_present": bool(token),
        "has_refresh_token": bool(token and token.get("refresh_token")),
        "authenticated": False,
        "email_address": (token or {}).get("emailAddress") or load_env().get("SARABYX_GMAIL_ADDRESS"),
    }
    if client and token:
        try:
            profile = get_profile()
            status["authenticated"] = True
            status["email_address"] = profile.get("emailAddress")
            status["messages_total"] = profile.get("messagesTotal")
            status["threads_total"] = profile.get("threadsTotal")
        except Exception as e:
            status["auth_error"] = str(e)
    return status


def get_profile() -> Dict[str, Any]:
    """Get authenticated user's Gmail profile."""
    return _gmail_request("/profile")


def _decode_base64url(data: str) -> str:
    """Decode base64url encoded string into UTF-8 text."""
    if not data:
        return ""
    padded = data + "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _strip_html(raw_html: str) -> str:
    """Convert HTML email body into readable plain text."""
    text = re.sub(r"(?is)<(script|style).*?>.*?(</\1>)", "", raw_html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _extract_body_from_payload(payload: Dict[str, Any]) -> str:
    """Recursively extract plain text (or stripped HTML fallback) from Gmail message payload."""
    mime_type = payload.get("mimeType", "")
    body_data = (payload.get("body") or {}).get("data", "")

    if mime_type == "text/plain" and body_data:
        return _decode_base64url(body_data).strip()

    parts = payload.get("parts") or []
    # Prefer text/plain first
    for part in parts:
        if part.get("mimeType") == "text/plain":
            d = (part.get("body") or {}).get("data", "")
            if d:
                return _decode_base64url(d).strip()

    # Check nested multipart
    for part in parts:
        if (part.get("mimeType") or "").startswith("multipart/"):
            nested = _extract_body_from_payload(part)
            if nested:
                return nested

    # Fallback to text/html
    if mime_type == "text/html" and body_data:
        return _strip_html(_decode_base64url(body_data))

    for part in parts:
        if part.get("mimeType") == "text/html":
            d = (part.get("body") or {}).get("data", "")
            if d:
                return _strip_html(_decode_base64url(d))

    return ""


def _parse_headers(headers: List[Dict[str, str]]) -> Dict[str, str]:
    """Extract standard headers into a dictionary."""
    out: Dict[str, str] = {}
    for h in headers or []:
        name = (h.get("name") or "").lower()
        val = h.get("value") or ""
        if name in ("from", "to", "cc", "subject", "date", "message-id", "references", "in-reply-to"):
            out[name] = val
    return out


def get_message(msg_id: str, include_body: bool = True) -> Dict[str, Any]:
    """Fetch full details of a single Gmail message by ID."""
    fmt = "full" if include_body else "metadata"
    raw = _gmail_request(f"/messages/{msg_id}", query={"format": fmt})
    payload = raw.get("payload") or {}
    headers = _parse_headers(payload.get("headers") or [])
    labels = raw.get("labelIds") or []

    result = {
        "id": raw.get("id"),
        "threadId": raw.get("threadId"),
        "labelIds": labels,
        "unread": "UNREAD" in labels,
        "snippet": html.unescape(raw.get("snippet") or ""),
        "from": headers.get("from", ""),
        "to": headers.get("to", ""),
        "cc": headers.get("cc", ""),
        "subject": headers.get("subject", "(No Subject)"),
        "date": headers.get("date", ""),
        "message_id_header": headers.get("message-id", ""),
        "references": headers.get("references", ""),
    }
    if include_body:
        body_text = _extract_body_from_payload(payload)
        result["body"] = body_text or result["snippet"]
    return result


def list_messages(query: str = "is:unread", max_results: int = 10) -> List[Dict[str, Any]]:
    """List Gmail messages matching a search query (default: 'is:unread')."""
    resp = _gmail_request(
        "/messages",
        query={"q": query, "maxResults": max(1, min(int(max_results), 50))},
    )
    stubs = resp.get("messages") or []
    detailed: List[Dict[str, Any]] = []
    for stub in stubs:
        mid = stub.get("id")
        if not mid:
            continue
        try:
            detailed.append(get_message(mid, include_body=False))
        except Exception:
            continue
    return detailed


def _build_raw_mime(
    to: str,
    subject: str,
    body: str,
    html_body: Optional[str] = None,
    in_reply_to: Optional[str] = None,
    references: Optional[str] = None,
) -> str:
    """Create base64url encoded RFC 2822 message."""
    if html_body:
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))
    else:
        msg = MIMEText(body, "plain", "utf-8")

    msg["To"] = to
    msg["Subject"] = subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references

    raw_bytes = msg.as_bytes()
    return base64.urlsafe_b64encode(raw_bytes).decode("ascii").rstrip("=")


def send_email(
    to: str,
    subject: str,
    body: str,
    html_body: Optional[str] = None,
    thread_id: Optional[str] = None,
    in_reply_to: Optional[str] = None,
    references: Optional[str] = None,
) -> Dict[str, Any]:
    """Send an email from the authenticated Sarabyx Gmail account."""
    raw_str = _build_raw_mime(
        to=to,
        subject=subject,
        body=body,
        html_body=html_body,
        in_reply_to=in_reply_to,
        references=references,
    )
    payload: Dict[str, Any] = {"raw": raw_str}
    if thread_id:
        payload["threadId"] = thread_id

    return _gmail_request("/messages/send", method="POST", body=payload)


def reply_to_message(msg_id: str, body: str) -> Dict[str, Any]:
    """Reply directly to an existing Gmail message within its thread."""
    orig = get_message(msg_id, include_body=False)
    reply_to_addr = orig.get("from") or ""
    subj = orig.get("subject") or ""
    if not subj.lower().startswith("re:"):
        subj = f"Re: {subj}"

    msg_id_hdr = orig.get("message_id_header") or None
    refs = orig.get("references") or ""
    if msg_id_hdr:
        refs = f"{refs} {msg_id_hdr}".strip()

    sent = send_email(
        to=reply_to_addr,
        subject=subj,
        body=body,
        thread_id=orig.get("threadId"),
        in_reply_to=msg_id_hdr,
        references=refs or None,
    )
    # Also mark original message as read
    try:
        mark_as_read(msg_id)
    except Exception:
        pass
    return {"sent": sent, "replied_to": reply_to_addr, "subject": subj}


def create_draft(to: str, subject: str, body: str) -> Dict[str, Any]:
    """Create a draft email in Sarabyx Gmail."""
    raw_str = _build_raw_mime(to=to, subject=subject, body=body)
    return _gmail_request(
        "/drafts",
        method="POST",
        body={"message": {"raw": raw_str}},
    )


def mark_as_read(msg_id: str) -> Dict[str, Any]:
    """Remove the UNREAD label from a Gmail message."""
    return _gmail_request(
        f"/messages/{msg_id}/modify",
        method="POST",
        body={"removeLabelIds": ["UNREAD"]},
    )
