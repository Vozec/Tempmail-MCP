import asyncio
import base64
import json
import random
import string
import time
from typing import Optional

import httpx

from .base import Attachment, EmailAccount, EmailProvider, Message

BASE_URL = "https://api.mail.tm"

# Public rate limit: 8 QPS per IP
_MAX_QPS = 8
_MIN_INTERVAL = 1.0 / _MAX_QPS  # 125 ms between requests

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:148.0) Gecko/20100101 Firefox/148.0",
    "Accept": "application/json",
    "Content-Type": "application/json",
}


class _RateLimiter:
    """Simple async token-bucket limiter shared across all requests."""

    def __init__(self, min_interval: float) -> None:
        self._min_interval = min_interval
        self._lock = asyncio.Lock()
        self._last_call: float = 0.0

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._min_interval - (now - self._last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()


def _decode_jwt(token: str) -> dict:
    """Decode JWT payload (no signature verification — we only need the `id` claim)."""
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.b64decode(part))
    except Exception:
        return {}


def _random_str(length: int, alphabet: str = string.ascii_lowercase + string.digits) -> str:
    return "".join(random.choices(alphabet, k=length))


def _parse_message(data: dict) -> Message:
    from_data = data.get("from", {})
    to_list = data.get("to", [])
    cc_list = data.get("cc", [])
    html_list = data.get("html", [])

    return Message(
        id=data["id"],
        from_addr=f'"{from_data.get("name", "")}" <{from_data.get("address", "")}>',
        to_addr=to_list[0]["address"] if to_list else "",
        subject=data.get("subject", ""),
        body_text=data.get("text"),
        body_html=html_list[0] if html_list else None,
        created_at=data.get("createdAt", ""),
        cc=", ".join(c.get("address", "") for c in cc_list) or None,
        attachments=[],
    )


class MailTmProvider(EmailProvider):
    """
    Temp-mail provider for mail.tm (api.mail.tm).

    Clean JWT-authenticated REST API — no Cloudflare bypass needed.
    Rate limit: 8 QPS per IP (enforced via a shared async token-bucket limiter).

    The `token` field of EmailAccount is the JWT returned by POST /token.
    The account ID (needed for deletion) is extracted from the JWT payload.
    """

    name = "mail.tm"

    def __init__(self, timeout: float = 10.0) -> None:
        self._client = httpx.AsyncClient(headers=_HEADERS, timeout=timeout)
        self._rl = _RateLimiter(_MIN_INTERVAL)

    async def _get(self, url: str, **kwargs) -> httpx.Response:
        await self._rl.acquire()
        return await self._client.get(url, **kwargs)

    async def _post(self, url: str, **kwargs) -> httpx.Response:
        await self._rl.acquire()
        return await self._client.post(url, **kwargs)

    async def _delete(self, url: str, **kwargs) -> httpx.Response:
        await self._rl.acquire()
        return await self._client.delete(url, **kwargs)

    def _auth(self, token: str) -> dict:
        return {"Authorization": f"Bearer {token}"}

    async def create_email(
        self,
        min_name_length: int = 10,
        max_name_length: int = 10,
        domain: Optional[str] = None,
    ) -> EmailAccount:
        if not domain:
            resp = await self._get(f"{BASE_URL}/domains")
            resp.raise_for_status()
            domains = resp.json()
            if not domains:
                raise RuntimeError("mail.tm: no domains available")
            domain = domains[0]["domain"]

        length = random.randint(min_name_length, max_name_length)
        address = f"{_random_str(length)}@{domain}"
        password = _random_str(12)

        resp = await self._post(
            f"{BASE_URL}/accounts",
            json={"address": address, "password": password},
        )
        resp.raise_for_status()

        resp = await self._post(
            f"{BASE_URL}/token",
            json={"address": address, "password": password},
        )
        resp.raise_for_status()
        jwt = resp.json()["token"]

        return EmailAccount(email=address, token=jwt, provider=self.name)

    async def get_messages(self, account: EmailAccount) -> list[Message]:
        resp = await self._get(
            f"{BASE_URL}/messages",
            headers=self._auth(account.token),
        )
        resp.raise_for_status()
        return [_parse_message(m) for m in resp.json()]

    async def get_message(self, account: EmailAccount, message_id: str) -> Message:
        resp = await self._get(
            f"{BASE_URL}/messages/{message_id}",
            headers=self._auth(account.token),
        )
        resp.raise_for_status()
        return _parse_message(resp.json())

    async def delete_email(self, account: EmailAccount) -> bool:
        account_id = _decode_jwt(account.token).get("id")
        if not account_id:
            return False
        resp = await self._delete(
            f"{BASE_URL}/accounts/{account_id}",
            headers=self._auth(account.token),
        )
        return resp.status_code in (200, 204)

    async def get_domains(self) -> list[str]:
        resp = await self._get(f"{BASE_URL}/domains")
        resp.raise_for_status()
        return [d["domain"] for d in resp.json()]

    async def health_check(self) -> bool:
        try:
            resp = await self._get(f"{BASE_URL}/domains")
            return resp.status_code == 200 and bool(resp.json())
        except Exception:
            return False

    async def aclose(self) -> None:
        await self._client.aclose()
