from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

from heckle.core.model import Source
from heckle.errors import GenerationError

TOKEN_ENV = {
    "github": ("GITHUB_TOKEN", "GH_TOKEN"),
    "gitlab": ("GITLAB_TOKEN",),
    "gitea": ("GITEA_TOKEN",),
    "forgejo": ("FORGEJO_API_TOKEN", "FORGEJO_TOKEN"),
}
URL_ENV = {"github": "GITHUB_BASE_URL", "gitlab": "GITLAB_BASE_URL", "gitea": "GITEA_BASE_URL", "forgejo": "FORGEJO_HOST"}
DEFAULT_URL = {"github": "https://api.github.com", "gitlab": "https://gitlab.com"}


def normalize_url(forge: str, value: str, *, allow_http: bool = False) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
        raise GenerationError("--url must be an HTTP(S) URL without embedded credentials")
    if parsed.query or parsed.fragment:
        raise GenerationError("--url cannot contain a query or fragment")
    if parsed.scheme == "http" and not allow_http:
        raise GenerationError("Plain HTTP exposes API credentials; explicitly pass --allow-http for trusted local test instances")
    suffix = "/api/v4" if forge == "gitlab" else "/api/v1" if forge in {"gitea", "forgejo"} else ""
    path = parsed.path.rstrip("/")
    if suffix and path.endswith(suffix):
        path = path[:-len(suffix)]
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", "")).rstrip("/")


@dataclass(frozen=True)
class Connection:
    source: Source
    token: str = field(repr=False)
    workers: int = 4
    timeout: float = 60
    retries: int = 4
    ca_file: str | None = None
    allow_http: bool = False

    def __post_init__(self) -> None:
        if not 1 <= self.workers <= 16:
            raise GenerationError("--workers must be between 1 and 16")
        if self.timeout <= 0 or self.retries < 0:
            raise GenerationError("Invalid HTTP timeout or retry count")
        if not self.token:
            raise GenerationError("Missing forge API token")

    @classmethod
    def from_environment(cls, forge: str, scope: str, url: str | None = None, *, namespace_type: str | None = None, **kwargs: object) -> Connection:
        token = next((os.environ.get(name) for name in TOKEN_ENV[forge] if os.environ.get(name)), "")
        if not token:
            raise GenerationError("Set " + " or ".join(TOKEN_ENV[forge]))
        value = url or os.environ.get(URL_ENV[forge]) or DEFAULT_URL.get(forge)
        if not value:
            raise GenerationError(f"Specify --url or {URL_ENV[forge]} for {forge}")
        value = normalize_url(forge, value, allow_http=bool(kwargs.get("allow_http", False)))
        return cls(Source(forge, value, scope, namespace_type), token, **kwargs)

    @property
    def api_root(self) -> str:
        suffix = {"github": "", "gitlab": "/api/v4", "gitea": "/api/v1", "forgejo": "/api/v1"}
        return self.source.url + suffix[self.source.forge]

    def provider_environment(self) -> dict[str, str]:
        env = {TOKEN_ENV[self.source.forge][0]: self.token}
        if self.ca_file:
            env["SSL_CERT_FILE"] = self.ca_file
        return env
