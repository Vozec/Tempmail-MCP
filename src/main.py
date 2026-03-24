"""
Entry point — all configuration via .env (or environment variables).

    python -m src.main         # REST API (+ frontend if ENABLE_FRONTEND=true)
    python -m src.mcp_server   # MCP server via stdio
"""
import os

import uvicorn
from dotenv import load_dotenv

load_dotenv()

if __name__ == "__main__":
    uvicorn.run(
        "src.api:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        reload=os.getenv("RELOAD", "false").lower() in ("1", "true", "yes"),
    )
