from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class EmailAccount:
    email: str
    token: str
    provider: str


@dataclass
class Attachment:
    filename: str
    content_type: str
    size: int
    url: Optional[str] = None


@dataclass
class Message:
    id: str
    from_addr: str
    to_addr: str
    subject: str
    body_text: Optional[str]
    body_html: Optional[str]
    created_at: str
    cc: Optional[str] = None
    attachments: list[Attachment] = field(default_factory=list)


class EmailProvider(ABC):
    """Base interface for all temp-mail providers."""

    name: str = "base"

    @abstractmethod
    async def create_email(
        self,
        min_name_length: int = 10,
        max_name_length: int = 10,
        domain: Optional[str] = None,
    ) -> EmailAccount:
        """Create a new temporary email address."""
        ...

    @abstractmethod
    async def get_messages(self, account: EmailAccount) -> list[Message]:
        """Poll and return all messages for the given email account."""
        ...

    @abstractmethod
    async def get_message(self, account: EmailAccount, message_id: str) -> Message:
        """Retrieve a specific message by ID."""
        ...

    @abstractmethod
    async def delete_email(self, account: EmailAccount) -> bool:
        """Delete the email account and all its messages."""
        ...

    @abstractmethod
    async def get_domains(self) -> list[str]:
        """Return the list of available domains for this provider."""
        ...

    async def health_check(self) -> bool:
        """Return True if the provider is reachable and operational."""
        return True
