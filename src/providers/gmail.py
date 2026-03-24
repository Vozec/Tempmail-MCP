import asyncio
import email as email_lib
import imaplib
import os
import random
import string
from email.header import decode_header
from typing import Optional

from .base import Attachment, EmailAccount, EmailProvider, Message

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993


def _decode(value: Optional[str]) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    result = []
    for chunk, charset in parts:
        if isinstance(chunk, bytes):
            result.append(chunk.decode(charset or "utf-8", errors="replace"))
        else:
            result.append(chunk)
    return "".join(result)


def _parse_imap_msg(uid: str, raw: bytes, to_addr: str) -> Message:
    msg = email_lib.message_from_bytes(raw)
    body_text: Optional[str] = None
    body_html: Optional[str] = None
    attachments: list[Attachment] = []

    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            disposition = str(part.get("Content-Disposition", ""))
            if "attachment" in disposition:
                filename = _decode(part.get_filename() or "")
                payload = part.get_payload(decode=True) or b""
                attachments.append(
                    Attachment(filename=filename, content_type=ct, size=len(payload))
                )
            elif ct == "text/plain" and body_text is None:
                body_text = (part.get_payload(decode=True) or b"").decode(
                    part.get_content_charset() or "utf-8", errors="replace"
                )
            elif ct == "text/html" and body_html is None:
                body_html = (part.get_payload(decode=True) or b"").decode(
                    part.get_content_charset() or "utf-8", errors="replace"
                )
    else:
        ct = msg.get_content_type()
        payload = (msg.get_payload(decode=True) or b"").decode(
            msg.get_content_charset() or "utf-8", errors="replace"
        )
        if ct == "text/html":
            body_html = payload
        else:
            body_text = payload

    return Message(
        id=uid,
        from_addr=_decode(msg.get("From", "")),
        to_addr=to_addr,
        subject=_decode(msg.get("Subject", "")),
        body_text=body_text,
        body_html=body_html,
        created_at=msg.get("Date", ""),
        cc=msg.get("Cc"),
        attachments=attachments,
    )


class GmailProvider(EmailProvider):
    """
    Wraps a real Gmail account via IMAP.

    Required env vars:
      GMAIL_EMAIL        — full Gmail address  (e.g. user@gmail.com)
      GMAIL_APP_PASSWORD — Google App Password (not the account password)

    create_email() generates a "+tag" alias (user+<random>@gmail.com).
    Messages sent to that alias are received in the normal inbox and
    filtered by the To: header on get_messages().
    delete_email() moves all messages for that alias to Trash.
    """

    name = "gmail"

    def __init__(self) -> None:
        self._address = os.environ["GMAIL_EMAIL"]
        self._password = os.environ["GMAIL_APP_PASSWORD"]
        self._user, self._domain = self._address.split("@", 1)

    # ------------------------------------------------------------------ helpers

    def _connect(self) -> imaplib.IMAP4_SSL:
        mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        mail.login(self._address, self._password)
        return mail

    def _search_uids(self, mail: imaplib.IMAP4_SSL, to_addr: str) -> list[bytes]:
        mail.select("INBOX")
        _, data = mail.search(None, f'(TO "{to_addr}")')
        return data[0].split() if data[0] else []

    # ------------------------------------------------------------------ interface

    async def create_email(
        self,
        min_name_length: int = 10,
        max_name_length: int = 10,
        domain: Optional[str] = None,
    ) -> EmailAccount:
        length = random.randint(min_name_length, max_name_length)
        tag = "".join(random.choices(string.ascii_lowercase + string.digits, k=length))
        alias = f"{self._user}+{tag}@{self._domain}"
        return EmailAccount(email=alias, token=tag, provider=self.name)

    def _sync_get_messages(self, to_addr: str) -> list[Message]:
        mail = self._connect()
        try:
            uids = self._search_uids(mail, to_addr)
            messages = []
            for uid in uids:
                _, msg_data = mail.fetch(uid, "(RFC822)")
                raw = msg_data[0][1]
                messages.append(_parse_imap_msg(uid.decode(), raw, to_addr))
            return messages
        finally:
            mail.logout()

    async def get_messages(self, account: EmailAccount) -> list[Message]:
        return await asyncio.to_thread(self._sync_get_messages, account.email)

    def _sync_get_message(self, uid: str, to_addr: str) -> Message:
        mail = self._connect()
        try:
            mail.select("INBOX")
            _, msg_data = mail.fetch(uid.encode(), "(RFC822)")
            return _parse_imap_msg(uid, msg_data[0][1], to_addr)
        finally:
            mail.logout()

    async def get_message(self, account: EmailAccount, message_id: str) -> Message:
        return await asyncio.to_thread(self._sync_get_message, message_id, account.email)

    def _sync_delete(self, to_addr: str) -> bool:
        mail = self._connect()
        try:
            uids = self._search_uids(mail, to_addr)
            for uid in uids:
                mail.store(uid, "+FLAGS", "\\Deleted")
            mail.expunge()
            return True
        finally:
            mail.logout()

    async def delete_email(self, account: EmailAccount) -> bool:
        return await asyncio.to_thread(self._sync_delete, account.email)

    async def get_domains(self) -> list[str]:
        return [self._domain]

    async def health_check(self) -> bool:
        def _check() -> bool:
            mail = self._connect()
            mail.logout()
            return True
        try:
            return await asyncio.to_thread(_check)
        except Exception:
            return False
