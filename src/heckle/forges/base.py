from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import replace
from urllib.parse import quote
from pathlib import Path
from typing import Any

from heckle.config import Connection
from heckle.core.model import ForgeModel, Observation
from heckle.core.security import scrub
from heckle.errors import APIError, GenerationError
from heckle.forges.shared.http import RESTClient


class ForgeAdapter(ABC):
    """A forge handler discovers API data; it does not know Terraform resources."""

    def __init__(self, connection: Connection, client: RESTClient | None = None) -> None:
        self.connection = connection
        self.client = client or RESTClient(connection)
        self.model = ForgeModel(connection.source)
        self.user: dict[str, Any] | None = None
        self.is_self = False

    @staticmethod
    def username(data: dict[str, Any]) -> str:
        # Never use display names as namespace identifiers.
        value = data.get("login") or data.get("username")
        if not isinstance(value, str) or not value:
            raise GenerationError("User response is missing an account username")
        return value

    def lookup_user(self, username: str) -> dict[str, Any]:
        return self.detail("personal account", f"/users/{quote(username, safe='')}")

    def resolve_source(self) -> None:
        """Resolve --me once, while keeping saved identities independent of tokens."""
        source = self.connection.source
        if source.namespace_type != "user" or self.user is not None:
            return
        current = self.detail("authenticated user", "/user")
        current_name = self.username(current)
        selected = (
            current
            if not source.resolved or current_name.casefold() == source.scope.casefold()
            else self.lookup_user(source.scope)
        )
        name = self.username(selected)
        if source.resolved and name.casefold() != source.scope.casefold():
            raise GenerationError("User returned by the API does not match --user")
        if str(selected.get("type") or "").casefold() == "organization" or selected.get(
            "is_organization"
        ):
            raise GenerationError("--user selected an organization; use --org instead")
        if selected.get("id") is None or current.get("id") is None:
            raise GenerationError("User response is missing a stable account ID")
        self.user = selected
        self.is_self = str(selected["id"]) == str(current["id"])
        self.connection = replace(self.connection, source=replace(source, scope=name))
        self.model.source = self.connection.source

    def owned_repositories(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Filter visibility/access lists by actual owner before fetching children."""
        owned: dict[str, dict[str, Any]] = {}
        owner = self.connection.source.scope
        for row in rows:
            identity = row.get("owner") or {}
            name = self.username(identity)
            if name.casefold() != owner.casefold():
                self.model.observations.append(
                    Observation(
                        row.get("full_name", name),
                        "skipped",
                        1,
                        "Repository belongs to another namespace",
                    )
                )
                continue
            owned[str(row["id"])] = row
        return sorted(owned.values(), key=lambda r: r["name"].casefold())

    @abstractmethod
    def discover(self, workspace: Path) -> ForgeModel:
        raise NotImplementedError

    def collection(
        self,
        scope: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        key: str | None = None,
        optional: bool = True,
        variable: bool = False,
    ) -> list[dict[str, Any]] | None:
        try:
            value = self.client.get_paginated(path, params=params, key=key)
        except APIError as exc:
            if not optional:
                raise
            self.model.observations.append(
                Observation(
                    scope,
                    (
                        "permission_denied"
                        if exc.status in {401, 403}
                        else "unavailable" if exc.status == 404 else "failed"
                    ),
                    detail=str(exc),
                    http_status=exc.status,
                )
            )
            return None
        self.model.observations.append(Observation(scope, "collected", count=len(value)))
        return scrub(value, variable=variable)

    def detail(self, scope: str, path: str, *, optional: bool = False) -> dict[str, Any] | None:
        try:
            value, _ = self.client.get(path)
        except APIError as exc:
            if not optional:
                raise
            self.model.observations.append(
                Observation(
                    scope,
                    (
                        "permission_denied"
                        if exc.status in {401, 403}
                        else "unavailable" if exc.status == 404 else "failed"
                    ),
                    detail=str(exc),
                    http_status=exc.status,
                )
            )
            return None
        if value is None and optional:
            self.model.observations.append(
                Observation(scope, "collected", 0, "No object configured")
            )
            return None
        if not isinstance(value, dict):
            raise APIError(0, path, "Expected an API object")
        return scrub(value)
