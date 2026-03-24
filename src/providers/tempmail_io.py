import httpx
from typing import Optional

from .base import Attachment, EmailAccount, EmailProvider, Message

BASE_URL = "https://api.internal.temp-mail.io/api/v3"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:148.0) Gecko/20100101 Firefox/148.0",
    "Accept": "*/*",
    "Accept-Language": "fr,fr-FR;q=0.9,en-US;q=0.8,en;q=0.7",
    "Content-Type": "application/json",
    "Application-Name": "web",
    "Application-Version": "4.0.0",
    "X-Cors-Header": "iaWg3pchvFx48fY",
    "Origin": "https://temp-mail.io",
    "Referer": "https://temp-mail.io/",
}


def _parse_message(data: dict) -> Message:
    attachments = [
        Attachment(
            filename=a.get("filename", ""),
            content_type=a.get("content_type", ""),
            size=a.get("size", 0),
            url=a.get("url"),
        )
        for a in data.get("attachments", [])
    ]
    return Message(
        id=data["id"],
        from_addr=data["from"],
        to_addr=data["to"],
        subject=data.get("subject", ""),
        body_text=data.get("body_text"),
        body_html=data.get("body_html"),
        created_at=data["created_at"],
        cc=data.get("cc"),
        attachments=attachments,
    )


class TempMailIO(EmailProvider):
    name = "tempmail.io"

    def __init__(self, timeout: float = 10.0):
        self._client = httpx.AsyncClient(headers=HEADERS, timeout=timeout)

    async def create_email(
        self,
        min_name_length: int = 10,
        max_name_length: int = 10,
        domain: Optional[str] = None,
    ) -> EmailAccount:
        payload: dict = {
            "min_name_length": min_name_length,
            "max_name_length": max_name_length,
        }
        if domain:
            payload["domain"] = domain

        resp = await self._client.post(f"{BASE_URL}/email/new", json=payload)
        resp.raise_for_status()
        data = resp.json()
        return EmailAccount(
            email=data["email"],
            token=data["token"],
            provider=self.name,
        )

    async def get_messages(self, account: EmailAccount) -> list[Message]:
        resp = await self._client.get(f"{BASE_URL}/email/{account.email}/messages")
        resp.raise_for_status()
        data = resp.json()
        return [_parse_message(m) for m in data]

    async def get_message(self, account: EmailAccount, message_id: str) -> Message:
        resp = await self._client.get(f"{BASE_URL}/message/{message_id}")
        resp.raise_for_status()
        return _parse_message(resp.json())

    async def delete_email(self, account: EmailAccount) -> bool:
        resp = await self._client.delete(
            f"{BASE_URL}/email/{account.email}",
            headers={"Authorization": f"Bearer {account.token}"},
        )
        return resp.status_code in (200, 204)

    async def get_domains(self) -> list[str]:
        resp = await self._client.get(f"{BASE_URL}/domains")
        resp.raise_for_status()
        data = resp.json()
        # Response is either a list of strings or list of objects with a "name" key
        if data and isinstance(data[0], str):
            return data
        return [d["name"] for d in data]

    async def health_check(self) -> bool:
        try:
            resp = await self._client.get(f"{BASE_URL}/domains")
            return resp.status_code == 200
        except Exception:
            return False

    async def aclose(self):
        await self._client.aclose()
