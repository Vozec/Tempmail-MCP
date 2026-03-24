import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import registry
from .providers import EmailAccount, EmailProvider

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    await registry.startup()
    yield
    await registry.shutdown()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="TempMail API",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class CreateEmailRequest(BaseModel):
    min_name_length: int = 10
    max_name_length: int = 10
    domain: Optional[str] = None


class AccountBody(BaseModel):
    email: str
    token: str
    provider: str


# ---------------------------------------------------------------------------
# Dependency
# ---------------------------------------------------------------------------

def get_provider(name: Optional[str] = Query(default=None)) -> EmailProvider:
    try:
        return registry.get(name)
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    except KeyError as e:
        raise HTTPException(404, str(e))


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api")


@router.get("/providers", summary="List providers", tags=["Providers"])
async def list_providers():
    """List all loaded providers in priority order."""
    return registry.list_names()


@router.post("/email", response_model=AccountBody, summary="Create email", tags=["Email"])
async def create_email(
    body: CreateEmailRequest = CreateEmailRequest(),
    name: Optional[str] = Query(default=None),
):
    """
    Create a new temporary email address.

    If `name` is given, use that provider directly.
    Otherwise, try providers in priority order (mail.tm → gmail → mailticking → …)
    and return the first success.
    """
    if name:
        try:
            provider = registry.get(name)
        except KeyError as e:
            raise HTTPException(404, str(e))
        return await provider.create_email(
            min_name_length=body.min_name_length,
            max_name_length=body.max_name_length,
            domain=body.domain,
        )

    errors: dict[str, str] = {}
    for pname in registry.PRIORITY:
        provider = registry.all_providers().get(pname)
        if provider is None:
            continue
        try:
            account = await provider.create_email(
                min_name_length=body.min_name_length,
                max_name_length=body.max_name_length,
                domain=body.domain,
            )
            log.info("create_email: used provider %s", pname)
            return account
        except Exception as exc:
            log.warning("create_email: provider %s failed: %s", pname, exc)
            errors[pname] = str(exc)

    raise HTTPException(503, {"message": "All providers failed", "errors": errors})


@router.get("/email/{email}/messages", summary="List messages", tags=["Email"])
async def get_messages(
    email: str,
    token: str = Query(default=""),
    provider: EmailProvider = Depends(get_provider),
):
    """Poll messages for the given email address."""
    account = EmailAccount(email=email, token=token, provider=provider.name)
    try:
        return await provider.get_messages(account)
    except Exception as e:
        raise HTTPException(502, f"Provider error: {e}")


@router.get("/email/{email}/message/{message_id}", summary="Get message", tags=["Email"])
async def get_message(
    email: str,
    message_id: str,
    token: str = Query(default=""),
    provider: EmailProvider = Depends(get_provider),
):
    """Get a specific message by ID."""
    account = EmailAccount(email=email, token=token, provider=provider.name)
    try:
        return await provider.get_message(account, message_id)
    except Exception as e:
        raise HTTPException(502, f"Provider error: {e}")


@router.delete("/email/{email}", summary="Delete email", tags=["Email"])
async def delete_email(
    email: str,
    token: str = Query(default=""),
    provider: EmailProvider = Depends(get_provider),
):
    """Delete the temporary email account."""
    account = EmailAccount(email=email, token=token, provider=provider.name)
    success = await provider.delete_email(account)
    if not success:
        raise HTTPException(502, "Provider failed to delete the email")
    return {"deleted": True}


@router.get("/domains", summary="List domains", tags=["Providers"])
async def get_domains(provider: EmailProvider = Depends(get_provider)):
    """List available domains for the selected provider."""
    try:
        return await provider.get_domains()
    except Exception as e:
        raise HTTPException(502, f"Provider error: {e}")


@router.get("/health", summary="Health check", tags=["System"])
async def health():
    """
    Check all providers are reachable.
    Returns 200 if all healthy, 207 (Multi-Status) if some are degraded.
    """
    async def _check(name: str, provider: EmailProvider) -> tuple[str, dict]:
        try:
            ok = await asyncio.wait_for(provider.health_check(), timeout=10.0)
            return name, {"status": "ok" if ok else "degraded"}
        except asyncio.TimeoutError:
            return name, {"status": "timeout"}
        except Exception as exc:
            return name, {"status": "error", "detail": str(exc)}

    results = dict(
        await asyncio.gather(*[_check(n, p) for n, p in registry.all_providers().items()])
    )

    all_ok = all(v["status"] == "ok" for v in results.values())
    return JSONResponse(
        status_code=200 if all_ok else 207,
        content={"healthy": all_ok, "providers": results},
    )


# ---------------------------------------------------------------------------
# Mount router + optional static files
# ---------------------------------------------------------------------------

app.include_router(router)

_enable_frontend = os.getenv("ENABLE_FRONTEND", "true").lower() not in ("0", "false", "no")
_static_dir = os.path.join(os.path.dirname(__file__), "static")

if _enable_frontend and os.path.isdir(_static_dir):
    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="static")
