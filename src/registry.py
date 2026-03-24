"""
Shared provider registry — imported by both api.py and mcp_server.py.
"""
import logging
import os
from typing import Optional

from .providers import (
    EmailProvider,
    GmailProvider,
    MailTickingProvider,
    MailTmProvider,
    TempAilProvider,
    TempMailIO,
    TempMailoProvider,
)

log = logging.getLogger(__name__)

_providers: dict[str, EmailProvider] = {}

PRIORITY = [
    "mail.tm",      # clean REST API, real temp domains
    "gmail",        # IMAP +tag aliases (only if creds set)
    "mailticking",  # Gmail +tag via FlareSolverr
    "tempmail.io",  # direct API
    "tempmailo",    # FlareSolverr
    "tempail",      # FlareSolverr
]


def register(provider: EmailProvider) -> None:
    _providers[provider.name] = provider


def get(name: Optional[str] = None) -> EmailProvider:
    if not _providers:
        raise RuntimeError("No email provider loaded")
    if name is None:
        for pname in PRIORITY:
            if pname in _providers:
                return _providers[pname]
        return next(iter(_providers.values()))
    provider = _providers.get(name)
    if provider is None:
        raise KeyError(f"Provider '{name}' not found. Available: {list(_providers)}")
    return provider


def all_providers() -> dict[str, EmailProvider]:
    return _providers


def list_names() -> list[str]:
    ordered = [p for p in PRIORITY if p in _providers]
    others = [p for p in _providers if p not in PRIORITY]
    return ordered + others


async def startup() -> None:
    register(TempMailIO())
    register(TempMailoProvider())
    register(MailTickingProvider())
    register(MailTmProvider())
    register(TempAilProvider())

    if os.getenv("GMAIL_EMAIL") and os.getenv("GMAIL_APP_PASSWORD"):
        register(GmailProvider())
        log.info("Gmail provider registered")
    else:
        log.info("Gmail provider skipped (GMAIL_EMAIL / GMAIL_APP_PASSWORD not set)")


async def shutdown() -> None:
    for p in list(_providers.values()):
        if hasattr(p, "aclose"):
            await p.aclose()
    _providers.clear()
