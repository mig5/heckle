from __future__ import annotations

from typing import Any
from urllib.parse import quote as _quote

from heckle.config import Connection
from heckle.errors import APIError, GitHubAPIError, GenerationError
from heckle.forges.shared.http import Quota, RESTClient


class GitHubClient(RESTClient):
    """GitHub query conventions over the shared read-only transport."""

    def __init__(self, connection: Connection) -> None:
        super().__init__(connection)

    def request(
        self,
        method: str,
        url: str,
        *,
        payload: dict[str, Any] | None = None,
        cache: bool = True,
        paced: bool = True,
    ) -> tuple[Any, dict[str, str]]:
        try:
            return super().request(method, url, payload=payload, cache=cache, paced=paced)
        except APIError as exc:
            raise GitHubAPIError(method, exc.url, exc.status, "API request failed") from None

    def quota(self) -> Quota | None:
        """The token's current REST quota, or None where it cannot be read.

        GitHub does not count /rate_limit against the limit, so this is safe to poll,
        and it bypasses both the cache and any rate-limit pause. GitHub Enterprise
        Server answers 404 when rate limiting is disabled.
        """
        try:
            body, _ = self.request("GET", self.root + "/rate_limit", cache=False, paced=False)
        except (APIError, GitHubAPIError, GenerationError):
            return None
        core = (body or {}).get("resources", {}).get("core") if isinstance(body, dict) else None
        if not isinstance(core, dict):
            return None
        try:
            return Quota(int(core["remaining"]), int(core["limit"]), float(core["reset"]))
        except (KeyError, TypeError, ValueError):
            return None

    def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        endpoint = (
            self.root.removesuffix("/v3") + "/graphql"
            if self.root.endswith("/api/v3")
            else self.root + "/graphql"
        )
        body, _ = self.request("POST", endpoint, payload={"query": query, "variables": variables})
        if (
            not isinstance(body, dict)
            or body.get("errors")
            or not isinstance(body.get("data"), dict)
        ):
            raise GenerationError("GitHub GraphQL query failed or returned partial data")
        return body["data"]


def quote(value: str) -> str:
    return _quote(value, safe="")
