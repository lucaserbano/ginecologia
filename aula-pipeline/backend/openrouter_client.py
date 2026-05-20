from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

import google.auth
from google.auth.transport.requests import Request as GoogleAuthRequest

from settings import (
    AI_BACKEND,
    AI_TIMEOUT_SECONDS,
    OPENROUTER_API_KEY,
    OPENROUTER_APP_TITLE,
    OPENROUTER_HTTP_REFERER,
    OPENROUTER_MODEL,
    OPENROUTER_URL,
    VERTEX_LOCATION,
    VERTEX_MODEL,
    VERTEX_PROJECT_ID,
)


class OpenRouterError(RuntimeError):
    pass


def is_openrouter_ready() -> bool:
    return bool(OPENROUTER_API_KEY and OPENROUTER_MODEL and OPENROUTER_URL)


def generate_text(system_prompt: str, user_prompt: str, temperature: float = 0.2, max_tokens: int = 2400) -> str:
    if AI_BACKEND == "vertex":
        return _generate_text_vertex(system_prompt, user_prompt, temperature=temperature, max_tokens=max_tokens)
    if AI_BACKEND == "openrouter":
        return _generate_text_openrouter(system_prompt, user_prompt, temperature=temperature, max_tokens=max_tokens)
    raise OpenRouterError(f"AI_BACKEND inválido: {AI_BACKEND}. Use 'vertex' ou 'openrouter'.")


def _generate_text_vertex(system_prompt: str, user_prompt: str, temperature: float = 0.2, max_tokens: int = 2400) -> str:
    if not VERTEX_PROJECT_ID:
        raise OpenRouterError("VERTEX_PROJECT_ID/GOOGLE_CLOUD_PROJECT não configurado.")

    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    if not creds.valid or not creds.token:
        creds.refresh(GoogleAuthRequest())

    endpoint_model = _vertex_model_endpoint(VERTEX_MODEL)
    url = (
        f"https://{VERTEX_LOCATION}-aiplatform.googleapis.com/v1/"
        f"projects/{VERTEX_PROJECT_ID}/locations/{VERTEX_LOCATION}/{endpoint_model}:generateContent"
    )

    payload = {
        "systemInstruction": {
            "role": "system",
            "parts": [{"text": system_prompt}],
        },
        "contents": [
            {
                "role": "user",
                "parts": [{"text": user_prompt}],
            }
        ],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        },
    }

    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {creds.token}",
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=AI_TIMEOUT_SECONDS) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise OpenRouterError(f"Vertex AI HTTP {exc.code}: {detail}") from exc
    except Exception as exc:
        raise OpenRouterError(f"Falha na chamada Vertex AI: {exc}") from exc

    try:
        data = json.loads(raw)
    except Exception as exc:
        raise OpenRouterError(f"Resposta Vertex AI inválida: {exc}") from exc

    candidates = data.get("candidates") or []
    if not candidates:
        raise OpenRouterError("Vertex AI sem candidates na resposta.")
    content = candidates[0].get("content") or {}
    parts = content.get("parts") or []
    texts: list[str] = []
    for part in parts:
        if isinstance(part, dict):
            text = part.get("text")
            if isinstance(text, str):
                texts.append(text)
    joined = "\n".join(texts).strip()
    if not joined:
        raise OpenRouterError("Vertex AI retornou conteúdo vazio.")
    return joined


def _generate_text_openrouter(system_prompt: str, user_prompt: str, temperature: float = 0.2, max_tokens: int = 2400) -> str:
    if not is_openrouter_ready():
        raise OpenRouterError("OPENROUTER_API_KEY/OPENROUTER_MODEL não configurados.")

    payload = {
        "model": OPENROUTER_MODEL,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    body = json.dumps(payload).encode("utf-8")

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    if OPENROUTER_HTTP_REFERER:
        headers["HTTP-Referer"] = OPENROUTER_HTTP_REFERER
    if OPENROUTER_APP_TITLE:
        headers["X-Title"] = OPENROUTER_APP_TITLE

    req = urllib.request.Request(OPENROUTER_URL, data=body, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=AI_TIMEOUT_SECONDS) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise OpenRouterError(f"OpenRouter HTTP {exc.code}: {detail}") from exc
    except Exception as exc:
        raise OpenRouterError(f"Falha na chamada OpenRouter: {exc}") from exc

    try:
        data = json.loads(raw)
    except Exception as exc:
        raise OpenRouterError(f"Resposta OpenRouter inválida: {exc}") from exc

    choices = data.get("choices") or []
    if not choices:
        raise OpenRouterError("OpenRouter sem choices na resposta.")

    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        joined = "\n".join(parts).strip()
        if joined:
            return joined

    raise OpenRouterError("OpenRouter retornou conteúdo vazio.")


def _vertex_model_endpoint(model: str) -> str:
    clean = (model or "").strip().strip("/")
    if clean.startswith("projects/"):
        return clean
    if clean.startswith("publishers/"):
        return clean
    encoded = urllib.parse.quote(clean, safe=".-_")
    return f"publishers/google/models/{encoded}"
