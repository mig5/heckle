"""Forge-neutral discovery data. No provider addresses or HCL expressions live here."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterator

from heckle.errors import GenerationError

SCHEMA_VERSION = 2
FORGES = ("github", "gitlab", "gitea", "forgejo")


@dataclass(frozen=True)
class Source:
    forge: str
    url: str
    scope: str
    namespace_type: str | None = None

    def __post_init__(self) -> None:
        # Version-1 inventories were exclusively organization/group snapshots.
        kind = self.namespace_type or ("group" if self.forge == "gitlab" else "organization")
        object.__setattr__(self, "namespace_type", kind)
        allowed = {"user", "group" if self.forge == "gitlab" else "organization"}
        if (
            self.forge not in FORGES
            or not isinstance(self.scope, str)
            or not self.scope
            or kind not in allowed
        ):
            raise GenerationError("Invalid forge, namespace type or empty discovery scope")
        if kind == "user" and ("/" in self.scope or any(c.isspace() for c in self.scope)):
            raise GenerationError("--user requires an account username, not a group path or URL")
        if self.scope == "@me" and kind != "user":
            raise GenerationError("--me selects a personal namespace")

    @property
    def resolved(self) -> bool:
        return self.scope != "@me"

    def matches(self, other: Source) -> bool:
        return (
            self.resolved
            and other.resolved
            and self.forge == other.forge
            and self.url == other.url
            and self.namespace_type == other.namespace_type
            and self.scope.casefold() == other.scope.casefold()
        )


@dataclass
class Entity:
    """A portable identity plus namespaced native settings, not a lowest common denominator.

    ``kind`` describes the portable concept. ``native_kind`` identifies the native
    subtype (e.g. project_approval_rule). ``parent`` refers to an Entity.uid.
    Values in ``native`` are JSON data, never executable expressions.
    """

    kind: str
    key: str
    remote_id: str
    native_kind: str
    native: dict[str, Any] = field(default_factory=dict)
    parent: str | None = None
    owner: str | None = None

    @property
    def uid(self) -> str:
        return f"{self.native_kind}:{self.key}"


@dataclass
class Observation:
    scope: str
    status: str  # collected, unavailable, permission_denied, failed, skipped
    count: int = 0
    detail: str = ""
    http_status: int | None = None


@dataclass
class ForgeModel:
    source: Source
    entities: dict[str, Entity] = field(default_factory=dict)
    observations: list[Observation] = field(default_factory=list)
    extensions: dict[str, Any] = field(default_factory=dict)

    def add(self, entity: Entity) -> Entity:
        previous = self.entities.get(entity.uid)
        if previous is not None and previous != entity:
            raise GenerationError(f"Conflicting discovered identity: {entity.uid}")
        self.entities[entity.uid] = entity
        return entity

    def of_kind(self, native_kind: str) -> Iterator[Entity]:
        return (e for e in self.ordered() if e.native_kind == native_kind)

    def ordered(self) -> list[Entity]:
        return [self.entities[key] for key in sorted(self.entities)]

    def to_dict(self) -> dict[str, Any]:
        if not self.source.resolved:
            raise GenerationError("Resolve --me before saving an inventory")
        return {
            "schema_version": SCHEMA_VERSION,
            "source": asdict(self.source),
            "entities": [asdict(entity) for entity in self.ordered()],
            "observations": [
                asdict(o) for o in sorted(self.observations, key=lambda o: (o.scope, o.status))
            ],
            "extensions": self.extensions,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ForgeModel:
        if value.get("schema_version") not in {1, SCHEMA_VERSION}:
            raise GenerationError("Unsupported Heckle inventory schema version")
        try:
            source = dict(value["source"])
            if value["schema_version"] == 1 and source.get("namespace_type") == "user":
                raise ValueError("Version-1 inventories cannot describe personal namespaces")
            if value["schema_version"] == SCHEMA_VERSION and source.get("namespace_type") not in {
                "user",
                "organization",
                "group",
            }:
                raise ValueError("Inventory is missing namespace_type")
            model = cls(Source(**source))
            if not model.source.resolved:
                raise ValueError("An inventory must contain the resolved username, not @me")
            for entity in value.get("entities", []):
                if not isinstance(entity.get("native", {}), dict):
                    raise ValueError("native settings must be an object")
                model.add(Entity(**entity))
            model.observations = [Observation(**item) for item in value.get("observations", [])]
            model.extensions = value.get("extensions", {})
            return model
        except (KeyError, TypeError, ValueError) as exc:
            raise GenerationError(f"Malformed Heckle inventory: {exc}") from exc
