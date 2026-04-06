"""
distill_app.webhooks
~~~~~~~~~~~~~~~~~~~~
Webhook callback delivery for async job results.

When a caller supplies a ``callback_url`` at submission time, the worker
POSTs the full job result to that URL after conversion completes or fails.

Delivery is best-effort with retry. HTTPS only, no private IP ranges (SSRF
protection). URLs are redacted in all log output.
"""

from __future__ import annotations

import ipaddress
import logging
import time
import urllib.parse

_logger = logging.getLogger(__name__)


# ── URL validation ──────────────────────────────────────────────────────────

_PRIVATE_HOSTNAMES = {"localhost", "localhost.localdomain"}


def _redact_url(url: str) -> str:
    """Redact a URL for safe logging — keep scheme + host, mask the path."""
    try:
        parsed = urllib.parse.urlparse(url)
        return f"{parsed.scheme}://{parsed.hostname}/***"
    except Exception:
        return "***"


def validate_callback_url(url: str) -> None:
    """Validate that *url* is a safe HTTPS webhook target.

    Raises ``ValueError`` with a descriptive message if any check fails.
    """
    parsed = urllib.parse.urlparse(url)

    # Scheme check
    if parsed.scheme != "https":
        raise ValueError("callback_url must use https")

    hostname = (parsed.hostname or "").lower()

    # Hostname check
    if not hostname:
        raise ValueError("callback_url must have a valid hostname")

    if hostname in _PRIVATE_HOSTNAMES:
        raise ValueError("callback_url must not target a private or reserved address")

    # IP address check — reject private/reserved ranges
    try:
        addr = ipaddress.ip_address(hostname)
        if addr.is_private or addr.is_loopback or addr.is_reserved or addr.is_link_local:
            raise ValueError("callback_url must not target a private or reserved address")
    except ValueError as e:
        if "private" in str(e) or "reserved" in str(e):
            raise
        # Not an IP address — that's fine, it's a hostname


# ── Delivery ────────────────────────────────────────────────────────────────

class WebhookDelivery:
    """Deliver webhook callbacks with retry."""

    def __init__(self, timeout_seconds: int = 10) -> None:
        self._timeout = timeout_seconds

    def deliver(self, url: str, payload: dict) -> bool:
        """POST *payload* as JSON to *url*. Returns True on 2xx. Never raises."""
        try:
            import httpx

            resp = httpx.post(
                url,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "Distill-Webhook/1.0",
                },
                timeout=self._timeout,
            )
            if 200 <= resp.status_code < 300:
                return True

            _logger.warning(
                "Webhook delivery to %s returned %d",
                _redact_url(url), resp.status_code,
            )
            return False

        except Exception as exc:
            _logger.warning(
                "Webhook delivery to %s failed: %s",
                _redact_url(url), type(exc).__name__,
            )
            return False

    def deliver_with_retry(
        self, url: str, payload: dict, max_retries: int = 3,
    ) -> bool:
        """Deliver with exponential backoff. Returns True on first success."""
        for attempt in range(max_retries):
            if self.deliver(url, payload):
                return True
            backoff = 2 ** attempt  # 1s, 2s, 4s
            _logger.warning(
                "Webhook delivery attempt %d/%d to %s failed, retrying in %ds",
                attempt + 1, max_retries, _redact_url(url), backoff,
            )
            time.sleep(backoff)

        _logger.warning(
            "Webhook delivery to %s failed after %d attempts",
            _redact_url(url), max_retries,
        )
        return False
