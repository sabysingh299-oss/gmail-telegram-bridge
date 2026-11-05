"""Gemini API helper for natural-language Gmail chat over Telegram."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from config import load_env

DEFAULT_MODEL = "gemini-3.1-flash-lite"
FALLBACK_MODELS = ("gemini-3.1-flash-lite", "gemini-2.5-flash", "gemini-3.5-flash-lite")


class GeminiClientError(Exception):
    pass


def gemini_configured() -> bool:
    return bool((load_env().get("GEMINI_API_KEY") or "").strip())


def _api_key() -> str:
    key = (load_env().get("GEMINI_API_KEY") or "").strip()
    if not key:
        raise GeminiClientError("GEMINI_API_KEY is not configured.")
    return key


def _preferred_models() -> List[str]:
    env = load_env()
    preferred = (env.get("GEMINI_MODEL") or DEFAULT_MODEL).strip()
    models = [preferred]
    for m in FALLBACK_MODELS:
        if m not in models:
            models.append(m)
    return models


def _extract_text(candidate: Dict[str, Any]) -> str:
    parts = ((candidate.get("content") or {}).get("parts") or [])
    chunks = [str(p.get("text") or "") for p in parts if p.get("text")]
    return "\n".join(chunks).strip()


def _extract_function_calls(candidate: Dict[str, Any]) -> List[Dict[str, Any]]:
    parts = ((candidate.get("content") or {}).get("parts") or [])
    calls = []
    for part in parts:
        fc = part.get("functionCall") or part.get("function_call")
        if not fc:
            continue
        name = fc.get("name") or ""
        args = fc.get("args") or fc.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {}
        if name:
            calls.append({"name": name, "args": args if isinstance(args, dict) else {}})
    return calls


def generate_content(
    contents: List[Dict[str, Any]],
    *,
    system_instruction: Optional[str] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    temperature: float = 0.3,
    max_output_tokens: int = 1024,
) -> Dict[str, Any]:
    """Call Gemini generateContent; tries preferred model then fallbacks."""
    key = _api_key()
    payload: Dict[str, Any] = {
        "contents": contents,
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_output_tokens,
        },
    }
    if system_instruction:
        payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}
    if tools:
        payload["tools"] = tools

    body = json.dumps(payload).encode("utf-8")
    last_error = ""
    for model in _preferred_models():
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{urllib.parse.quote(model, safe='')}:generateContent?key={urllib.parse.quote(key)}"
        )
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            candidates = data.get("candidates") or []
            if not candidates:
                last_error = f"{model}: empty candidates ({data.get('promptFeedback')})"
                continue
            candidate = candidates[0]
            return {
                "model": model,
                "text": _extract_text(candidate),
                "function_calls": _extract_function_calls(candidate),
                "raw_candidate": candidate,
                "finish_reason": candidate.get("finishReason"),
            }
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            last_error = f"{model}: HTTP {e.code} {err_body[:240]}"
            # Retry on capacity / unavailable model
            if e.code in (404, 429, 503):
                continue
            raise GeminiClientError(last_error) from e
        except Exception as e:
            last_error = f"{model}: {e}"
            continue

    raise GeminiClientError(last_error or "All Gemini models failed.")
