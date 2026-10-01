from __future__ import annotations
import dataclasses
from heckle.errors import GenerationError
from heckle.hcl.types import Body


def _normalize_block(raw: str | None) -> str | None:
    if raw is None:
        return None
    return "\n".join(line.rstrip() for line in raw.strip().splitlines())


@dataclasses.dataclass
class ImportRecord:
    old_address: str
    import_id: str
    new_address: str | None = None


@dataclasses.dataclass
class Family:
    resource_type: str
    samples: list[Body] = dataclasses.field(default_factory=list)
    lifecycle_raw: str | None = None

    def add(self, body: Body, lifecycle_raw: str | None) -> None:
        normalized = _normalize_block(lifecycle_raw)
        current = _normalize_block(self.lifecycle_raw)
        if self.samples and normalized != current:
            raise GenerationError(f"{self.resource_type} instances have differing lifecycle blocks")
        self.samples.append(body)
        self.lifecycle_raw = lifecycle_raw


# Provider-native compilation is deliberately separate from the forge-neutral model.
from typing import Any
from heckle.core.model import Entity, ForgeModel
from heckle.core.capabilities import Coverage


@dataclasses.dataclass(frozen=True)
class Reference:
    target_uid: str
    attribute: str = "id"


@dataclasses.dataclass
class Candidate:
    entity: Entity
    resource_type: str
    import_id: str
    module: str
    references: dict[str, Reference] = dataclasses.field(default_factory=dict)
    drop_attributes: set[str] = dataclasses.field(default_factory=set)
    ignore_attributes: set[str] = dataclasses.field(default_factory=set)

    flat_override: str | None = None

    @property
    def key(self) -> str:
        return self.entity.key

    @property
    def flat_address(self) -> str:
        if self.flat_override:
            return self.flat_override
        from heckle.hcl.render import hcl_label

        return f"{self.resource_type}.{hcl_label(self.resource_type, self.entity.uid)}"

    @property
    def address(self) -> str:
        from heckle.hcl.render import hcl_string

        return f"module.{self.module}.{self.resource_type}.this[{hcl_string(self.key)}]"


@dataclasses.dataclass
class ImportPlan:
    model: ForgeModel
    candidates: list[Candidate] = dataclasses.field(default_factory=list)
    coverage: Coverage = dataclasses.field(default_factory=Coverage)
    # Native providers can carry a typed internal plan, never serialized as input code.
    native: Any = None

    def validate(self) -> None:
        from heckle.errors import GenerationError

        for field in ("address", "flat_address"):
            values = [getattr(c, field) for c in self.candidates]
            if len(values) != len(set(values)):
                raise GenerationError(f"Duplicate provider {field}")
        identities = [(c.resource_type, c.import_id) for c in self.candidates]
        if len(identities) != len(set(identities)):
            raise GenerationError("A remote resource would be imported more than once")


@dataclasses.dataclass
class CompiledResource:
    candidate: Candidate
    body: Body
    ignored: set[str] = dataclasses.field(default_factory=set)
    resource_name: str = "this"

    @property
    def address(self) -> str:
        from heckle.hcl.render import hcl_string

        return (
            f"module.{self.candidate.module}.{self.candidate.resource_type}."
            f"{self.resource_name}[{hcl_string(self.candidate.key)}]"
        )


@dataclasses.dataclass
class CompiledProject:
    plan: ImportPlan
    resources: list[CompiledResource] = dataclasses.field(default_factory=list)
    variables: dict[str, str] = dataclasses.field(default_factory=dict)
    validation_inputs: dict[str, str] = dataclasses.field(default_factory=dict)
    skip_imports: set[str] = dataclasses.field(default_factory=set)
    output_attributes: dict[str, set[str]] = dataclasses.field(default_factory=dict)
    moved: list[tuple[str, str]] = dataclasses.field(default_factory=list)
    native: Any = None

    def assign_presence_profiles(self) -> None:
        """Give native resources stable blocks based on configured argument presence."""
        if not self.resources:
            return
        import hashlib
        from collections import defaultdict
        from heckle.hcl.render import body_presence_signature, body_presence_weight

        by_module: dict[str, list[CompiledResource]] = defaultdict(list)
        for item in self.resources:
            by_module[item.candidate.module].append(item)
        for resources in by_module.values():
            groups: dict[tuple[object, ...], list[CompiledResource]] = defaultdict(list)
            for item in resources:
                groups[body_presence_signature(item.body)].append(item)
            if len(groups) == 1:
                for item in resources:
                    item.resource_name = "this"
                continue
            ranked = sorted(
                groups.items(),
                key=lambda pair: (
                    -max(body_presence_weight(item.body) for item in pair[1]),
                    -len(pair[1]),
                    repr(pair[0]),
                ),
            )
            primary = ranked[0][0]
            for signature, items in groups.items():
                name = (
                    "this"
                    if signature == primary
                    else ("profile_" + hashlib.sha256(repr(signature).encode()).hexdigest()[:10])
                )
                for item in items:
                    item.resource_name = name
