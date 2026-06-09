"""Generic Docker secret reader for ai-local services.

Reads secrets following the Docker secrets convention:
  1. Check for {NAME}_FILE env var → read file content
  2. Fallback to {NAME} env var (for local development only)
  3. Raise RuntimeError if neither is available

Usage:
    from sharedai.security.secrets import read_secret

    api_key = read_secret("INTERNAL_API_KEY")
    # Reads from INTERNAL_API_KEY_FILE=/run/secrets/internal_api_key
    # Falls back to INTERNAL_API_KEY env var if _FILE is not set
"""

import os
from pathlib import Path


def read_secret(name: str, *, required: bool = True) -> str:
    """Read a secret value from file or environment.

    Args:
        name: The secret name (e.g. "INTERNAL_API_KEY").
              Checks {name}_FILE env var first, then {name}.
        required: If True, raises RuntimeError when secret is missing.
                  If False, returns empty string.

    Returns:
        The secret value (stripped of whitespace).

    Raises:
        RuntimeError: If required=True and secret cannot be found.
    """
    # Priority 1: Read from file (Docker secrets pattern)
    file_var = f"{name}_FILE"
    file_path = os.environ.get(file_var)
    if file_path:
        path = Path(file_path)
        if path.is_file():
            return path.read_text().strip()

    # Priority 2: Direct env var (local dev fallback)
    value = os.environ.get(name, "")
    if value:
        return value.strip()

    if required:
        raise RuntimeError(
            f"Secret '{name}' not configured. "
            f"Set {file_var} (Docker secret path) or {name} (dev fallback)."
        )
    return ""
