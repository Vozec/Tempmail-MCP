import hashlib
import json
import os
import uuid
from typing import Optional

from .base import Attachment, EmailAccount, EmailProvider, Message
from ..utils.flaresolverr import FlareSolverrClient

BASE_URL = "https://www.mailticking.com"

_API_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "fr,fr-FR;q=0.9,en-US;q=0.8,en;q=0.7",
    "Content-Type": "application/json",
    "Origin": BASE_URL,
    "Referer": BASE_URL + "/",
}


def _email_code(email: str) -> str:
    """SHA-256 of the email — used by mailticking as the auth token for /get-emails."""
    return hashlib.sha256(email.encode()).hexdigest()


# Mailbox types offered by /get-mailbox:
#   1 → abc@<temp-domain>      (non-Gmail temp domain)
#   2 → abc+tag@gmail.com      (Gmail plus addressing)  ← recommended
#   3 → a.b.c@gmail.com        (Gmail dot trick)
#   4 → abc@googlemail.com     (googlemail alias)
MAILBOX_TYPE_TEMP = "2"


def _parse_list_item(item: dict, to_addr: str) -> Message:
    return Message(
        id=item["Code"],
        from_addr=f'"{item.get("FromName", "")}" <{item.get("FromEmail", "")}>',
        to_addr=to_addr,
        subject=item.get("Subject", ""),
        body_text=None,
        body_html=None,
        created_at=str(item.get("SendTime", "")),
        attachments=[],
    )


class MailTickingProvider(EmailProvider):
    """
    Temp-mail provider for mailticking.com.

    Uses FlareSolverr to bypass Cloudflare.

    create_email flow:
      POST /get-mailbox {"types":["2"]} → Gmail +tag address
      POST /activate-email {"email":"..."} → activates it

    delete_email: POST /destroy (body empty) — destroys the active mailbox.

    The `token` field of EmailAccount is sha256(email), required as `code`
    in POST /get-emails.

    Required env var:
      FLARESOLVERR_URL  — default: http://localhost:8191
    """

    name = "mailticking"

    def __init__(self, flaresolverr_url: Optional[str] = None) -> None:
        url = flaresolverr_url or os.getenv("FLARESOLVERR_URL", "http://localhost:8191")
        self._fs = FlareSolverrClient(url=url)
        self._session_id: Optional[str] = None

    # ------------------------------------------------------------------ session

    async def _init_session(self) -> None:
        sid = f"mailticking_{uuid.uuid4().hex[:8]}"
        await self._fs.create_session(sid)
        self._session_id = sid

    async def _ensure_session(self) -> None:
        if not self._session_id:
            await self._init_session()

    async def _refresh_session(self) -> None:
        """Destroy current session and open a fresh one (new email on homepage)."""
        if self._session_id:
            await self._fs.destroy_session(self._session_id)
        self._session_id = None
        await self._init_session()

    # ------------------------------------------------------------------ interface

    async def create_email(
        self,
        min_name_length: int = 10,
        max_name_length: int = 10,
        domain: Optional[str] = None,
    ) -> EmailAccount:
        # Fresh session so the new active_mailbox cookie is clean
        await self._refresh_session()

        # 1. Get a new mailbox address (type 2 = Gmail +tag, actual temp addresses)
        solution = await self._fs.post(
            f"{BASE_URL}/get-mailbox",
            body=json.dumps({"types": [MAILBOX_TYPE_TEMP]}),
            headers=_API_HEADERS,
            session_id=self._session_id,
        )
        try:
            data = json.loads(solution.get("response", "{}"))
        except json.JSONDecodeError:
            data = {}
        if not data.get("success"):
            raise RuntimeError(f"mailticking: /get-mailbox failed: {solution.get('response')}")
        email = data["email"]

        # 2. Activate it (sets active_mailbox + temp_mail_history cookies)
        await self._fs.post(
            f"{BASE_URL}/activate-email",
            body=json.dumps({"email": email}),
            headers=_API_HEADERS,
            session_id=self._session_id,
        )

        return EmailAccount(email=email, token=_email_code(email), provider=self.name)

    async def get_messages(self, account: EmailAccount) -> list[Message]:
        await self._ensure_session()
        body = json.dumps({"email": account.email, "code": account.token})
        solution = await self._fs.post(
            f"{BASE_URL}/get-emails?lang=",
            body=body,
            headers=_API_HEADERS,
            session_id=self._session_id,
        )
        try:
            data = json.loads(solution.get("response", "{}"))
        except json.JSONDecodeError:
            return []
        if not data.get("success"):
            return []
        return [_parse_list_item(item, account.email) for item in data.get("emails", [])]

    async def get_message(self, account: EmailAccount, message_id: str) -> Message:
        """
        Merges metadata from /get-emails (from, subject…) with the HTML body
        from /mail/gmail-content/{id} since the content endpoint returns
        empty metadata fields.
        """
        await self._ensure_session()

        # Fetch body
        solution = await self._fs.get(
            f"{BASE_URL}/mail/gmail-content/{message_id}",
            session_id=self._session_id,
        )
        try:
            content_data = json.loads(solution.get("response", "{}"))
        except json.JSONDecodeError:
            content_data = {}
        body_html = content_data.get("result", {}).get("content")

        # Find metadata in the message list (best-effort)
        messages = await self.get_messages(account)
        base = next((m for m in messages if m.id == message_id), None)

        if base:
            return Message(
                id=base.id,
                from_addr=base.from_addr,
                to_addr=base.to_addr,
                subject=base.subject,
                body_text=None,
                body_html=body_html,
                created_at=base.created_at,
                attachments=[],
            )

        # Fallback — content endpoint data only
        result = content_data.get("result", {})
        return Message(
            id=message_id,
            from_addr=f'"{result.get("from_name", "")}" <{result.get("from", "")}>',
            to_addr=result.get("receiver", account.email),
            subject=result.get("subject", ""),
            body_text=None,
            body_html=body_html,
            created_at=str(result.get("send_time", "")),
            attachments=[],
        )

    async def delete_email(self, account: EmailAccount) -> bool:
        """Destroy the active mailbox (POST /destroy — clears active_mailbox cookie)."""
        await self._ensure_session()
        solution = await self._fs.post(
            f"{BASE_URL}/destroy",
            body="",
            headers={
                **_API_HEADERS,
                "X-Requested-With": "XMLHttpRequest",
            },
            session_id=self._session_id,
        )
        try:
            data = json.loads(solution.get("response", "{}"))
            return bool(data.get("success"))
        except json.JSONDecodeError:
            return False

    async def get_domains(self) -> list[str]:
        # mailticking generates dotted aliases of real Gmail addresses
        return ["gmail.com"]

    async def health_check(self) -> bool:
        return await self._fs.health_check()

    async def aclose(self) -> None:
        if self._session_id:
            await self._fs.destroy_session(self._session_id)
        await self._fs.aclose()
