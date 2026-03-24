"""
TempMail MCP server — exposes temp email tools to AI assistants.

Run:
    python -m src.mcp_server

Configure in Claude Desktop (~/.claude/claude_desktop_config.json):
    {
      "mcpServers": {
        "tempmail": {
          "command": "python",
          "args": ["-m", "src.mcp_server"],
          "cwd": "/path/to/tempmail"
        }
      }
    }
"""
from contextlib import asynccontextmanager
from typing import Optional

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

from . import registry
from .providers import EmailAccount


@asynccontextmanager
async def _lifespan(server: FastMCP):
    await registry.startup()
    yield
    await registry.shutdown()


mcp = FastMCP(
    "TempMail",
    lifespan=_lifespan,
    instructions=(
        "Use these tools to create and manage disposable email addresses. "
        "Always store the returned token — it is required for subsequent calls. "
        "Prefer create_email without arguments (auto-selects best provider). "
        "Poll get_messages every few seconds to wait for incoming mail."
    ),
)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def list_providers() -> list[str]:
    """List all available email providers in priority order."""
    return registry.list_names()


@mcp.tool()
async def get_domains(provider: Optional[str] = None) -> list[str]:
    """
    List available email domains for a provider.
    Leave provider empty to use the default (highest priority) provider.
    """
    p = registry.get(provider)
    return await p.get_domains()


@mcp.tool()
async def create_email(provider: Optional[str] = None) -> dict:
    """
    Create a new disposable email address.

    Args:
        provider: Provider name (e.g. "mail.tm", "tempmail.io"). Leave empty for auto.

    Returns:
        email: The generated email address.
        token: Auth token — store it, required for get_messages / delete_email.
        provider: Which provider was used.
    """
    p = registry.get(provider)
    account = await p.create_email()
    return {"email": account.email, "token": account.token, "provider": account.provider}


@mcp.tool()
async def get_messages(email: str, token: str, provider: str) -> list[dict]:
    """
    List messages in the inbox. Poll this every few seconds to wait for new mail.

    Args:
        email: The email address (from create_email).
        token: The auth token (from create_email).
        provider: The provider name (from create_email).

    Returns:
        List of messages with id, from_addr, subject, created_at.
        Use read_message to get the full body.
    """
    p = registry.get(provider)
    account = EmailAccount(email=email, token=token, provider=provider)
    messages = await p.get_messages(account)
    return [
        {
            "id": m.id,
            "from": m.from_addr,
            "subject": m.subject,
            "date": m.created_at,
        }
        for m in messages
    ]


@mcp.tool()
async def read_message(email: str, message_id: str, token: str, provider: str) -> dict:
    """
    Read the full content of a specific message.

    Args:
        email: The email address.
        message_id: The message ID (from get_messages).
        token: The auth token.
        provider: The provider name.

    Returns:
        Full message with from, subject, body_text, body_html, attachments.
    """
    p = registry.get(provider)
    account = EmailAccount(email=email, token=token, provider=provider)
    m = await p.get_message(account, message_id)
    return {
        "id": m.id,
        "from": m.from_addr,
        "to": m.to_addr,
        "subject": m.subject,
        "date": m.created_at,
        "body_text": m.body_text,
        "body_html": m.body_html,
        "attachments": [
            {"filename": a.filename, "content_type": a.content_type, "size": a.size}
            for a in (m.attachments or [])
        ],
    }


@mcp.tool()
async def delete_email(email: str, token: str, provider: str) -> dict:
    """
    Delete / destroy the temporary email address.

    Args:
        email: The email address.
        token: The auth token.
        provider: The provider name.
    """
    p = registry.get(provider)
    account = EmailAccount(email=email, token=token, provider=provider)
    ok = await p.delete_email(account)
    return {"deleted": ok, "email": email}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
