"""API key authentication middleware for ai-local services.

Usage in FastAPI:
    from sharedai.security.auth import require_api_key
    app = FastAPI()
    app.add_middleware(require_api_key)

    # Or as a dependency:
    from sharedai.security.auth import verify_api_key
    @app.get("/endpoint", dependencies=[Depends(verify_api_key)])
"""

import os
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


@lru_cache(maxsize=1)
def _load_api_key() -> str:
    """Load API key from file (Docker secret) or env var."""
    key_file = os.environ.get("API_KEY_FILE")
    if key_file:
        path = Path(key_file)
        if path.is_file():
            return path.read_text().strip()

    # Fallback to direct env var (for local dev)
    key = os.environ.get("API_KEY", "")
    if not key:
        raise RuntimeError(
            "No API key configured. Set API_KEY_FILE or API_KEY env var."
        )
    return key


async def verify_api_key(
    api_key: str = Security(_api_key_header),
) -> str:
    """FastAPI dependency that validates X-API-Key header."""
    expected = _load_api_key()
    if not api_key or api_key != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )
    return api_key
