from __future__ import annotations

from typing import Any
from heckle.core.model import Entity


def group(data: dict[str, Any], parent: str | None = None) -> Entity:
    return Entity(
        "namespace",
        str(data["full_path"]),
        str(data["id"]),
        "group",
        data,
        parent,
        str(data["full_path"]),
    )


def project(data: dict[str, Any], parent: Entity) -> Entity:
    return Entity(
        "repository",
        str(data["path_with_namespace"]),
        str(data["id"]),
        "project",
        data,
        parent.uid,
        str(data["path_with_namespace"]),
    )


def child(
    parent: Entity, native_kind: str, kind: str, data: dict[str, Any], identity: str | int
) -> Entity:
    native = {**data, "_parent_id": parent.remote_id, "_parent_path": parent.key}
    return Entity(
        kind,
        f"{parent.key}/{identity}",
        str(data.get("id", identity)),
        native_kind,
        native,
        parent.uid,
        parent.owner or parent.key,
    )
