from __future__ import annotations

from typing import Any
from urllib.parse import quote as _quote

from heckle.config import Connection
from heckle.errors import APIError, GitHubAPIError, GenerationError
from heckle.forges.shared.http import RESTClient


class GitHubClient(RESTClient):
    """GitHub query conventions over the shared read-only transport."""
    def __init__(self, connection: Connection) -> None:
        super().__init__(connection)

    def request(self, method: str, url: str, *, payload: dict[str, Any] | None = None) -> tuple[Any, dict[str, str]]:
        try:
            return super().request(method, url, payload=payload)
        except APIError as exc:
            raise GitHubAPIError(method, exc.url, exc.status, "API request failed") from None

    def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        endpoint = self.root.removesuffix("/v3") + "/graphql" if self.root.endswith("/api/v3") else self.root + "/graphql"
        body, _ = self.request("POST", endpoint, payload={"query": query, "variables": variables})
        if not isinstance(body, dict) or body.get("errors") or not isinstance(body.get("data"), dict):
            raise GenerationError("GitHub GraphQL query failed or returned partial data")
        return body["data"]


def quote(value: str) -> str:
    return _quote(value, safe="")
