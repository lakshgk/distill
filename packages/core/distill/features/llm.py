"""
distill.features.llm
~~~~~~~~~~~~~~~~~~~~
Provider-agnostic LLM client for chat completions.

Sends requests to any OpenAI-compatible ``/chat/completions`` endpoint.
The caller supplies an API key, model name, and optional base URL.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

_logger = logging.getLogger(__name__)


# ── Exceptions ──────────────────────────────────────────────────────────────

class LLMError(Exception):
    """Raised when an LLM call fails after all retries or config is invalid.

    Never contains the API key string.
    """


# ── Config ──────────────────────────────────────────────────────────────────

@dataclass
class LLMConfig:
    api_key:          str
    model:            str
    base_url:         Optional[str] = None
    timeout_seconds:  int           = 30
    max_retries:      int           = 2


# ── Client ──────────────────────────────────────────────────────────────────

class LLMClient:
    """Sync chat-completion client for any OpenAI-compatible API."""

    def __init__(self, config: LLMConfig) -> None:
        self._config = config

    def complete(self, system: str, user: str) -> str:
        """Send a chat completion request and return the assistant message text.

        Raises ``LLMError`` on configuration errors or after all retries are
        exhausted.  The API key is never included in error messages.
        """
        import httpx

        if not self._config.base_url:
            raise LLMError("base_url is required")

        url = f"{self._config.base_url.rstrip('/')}/chat/completions"

        headers = {
            "Authorization": f"Bearer {self._config.api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": self._config.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }

        last_error: Optional[Exception] = None

        for attempt in range(1 + self._config.max_retries):
            try:
                resp = httpx.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=self._config.timeout_seconds,
                )
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]

            except Exception as exc:
                last_error = exc
                if attempt < self._config.max_retries:
                    time.sleep(1)

        # All retries exhausted — build a safe error message
        msg = self._safe_error_message(last_error)
        raise LLMError(msg) from last_error

    def _safe_error_message(self, exc: Optional[Exception]) -> str:
        """Build an error message that never contains the API key."""
        raw = str(exc) if exc else "unknown error"
        # Scrub the API key from the message if it leaked into the exception text
        if self._config.api_key and self._config.api_key in raw:
            raw = raw.replace(self._config.api_key, "***")
        return f"LLM request failed after retries: {raw}"
