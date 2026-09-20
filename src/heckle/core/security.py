"""Redaction at persistence boundaries and conservative workspace publication."""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from heckle.errors import GenerationError

REDACTED = "[REDACTED]"
SECRET_KEYS = frozenset({
    "token", "access_token", "api_token", "auth_token", "password", "secret",
    "private_token", "private_key", "authorization", "authorization_header",
    "webhook_token", "signing_token", "custom_headers", "url_variables", "runners_token", "runner_token", "registration_token",
    "import_url_password", "migration_service_auth_token", "migration_service_auth_password",
})


def scrub(value: Any, *, variable: bool = False) -> Any:
    """Do not persist credential fields, including readable GitLab CI values.

    Webhook URLs are handled separately by the provider compiler; normal inventory
    can contain confidential configuration and must still be treated as private.
    """
    if isinstance(value, list):
        return [scrub(item, variable=variable) for item in value]
    if isinstance(value, dict):
        return {
            str(k): (REDACTED if str(k).lower() in SECRET_KEYS or (variable and k in {"value", "data"})
                     else scrub(v, variable=variable))
            for k, v in value.items()
        }
    return value


def safe_url(url: str) -> str:
    """Strip URL credentials and query strings from diagnostics."""
    try:
        parsed = urlsplit(url)
        host = parsed.netloc.rsplit("@", 1)[-1]
        return urlunsplit((parsed.scheme, host, parsed.path, "", ""))
    except ValueError:
        return "[invalid URL]"


def write_private_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
    path.chmod(0o600)


def check_destination(destination: Path, *, force: bool) -> Path:
    if destination.is_symlink():
        raise GenerationError("Output must not be a symbolic link")
    path = destination.expanduser().resolve()
    if path in {Path(path.anchor), Path.home().resolve(), Path.cwd().resolve()} or path in Path.cwd().resolve().parents:
        raise GenerationError("Refusing to replace a root, home, or current working directory")
    if path.exists():
        if not path.is_dir():
            raise GenerationError(f"Output is not a directory: {path}")
        if not force:
            raise GenerationError(f"Output already exists: {path}; choose a new --out or use --force")
        # --force replaces disposable generations only, never an adopted IaC root.
        if not (path / ".heckle-generated").is_file():
            raise GenerationError("--force only replaces a Heckle-marked output directory")
        if any(item.is_symlink() for item in path.rglob("*")):
            raise GenerationError("Refusing to replace an output containing symbolic links")
        if (path / ".terraform").exists() or any(path.rglob("*.tfstate*")) or any(path.rglob("*.tfvars*")):
            raise GenerationError("Refusing to replace an initialized project, state, or user tfvars")
        if any(re.search(r'\bbackend\s+"', p.read_text(encoding="utf-8")) for p in path.glob("*.tf")):
            raise GenerationError("Refusing to replace an output with backend configuration")
    return path


def publish(staged: Path, destination: Path, *, force: bool) -> None:
    """Rollback-safe same-filesystem publication after all validation succeeds."""
    destination = check_destination(destination, force=force)
    backup: Path | None = None
    if destination.exists():
        backup = Path(tempfile.mkdtemp(prefix=f".{destination.name}-previous-", dir=destination.parent))
        backup.rmdir()
        destination.rename(backup)
    try:
        staged.rename(destination)
    except BaseException:
        if backup is not None:
            backup.rename(destination)
        raise
    if backup is not None:
        shutil.rmtree(backup)
