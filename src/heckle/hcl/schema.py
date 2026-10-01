"""Inspect the installed provider, not a guessed resource schema."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from heckle.errors import GenerationError
from heckle.hcl.types import Body, NestedBlock


def contains_sensitive(attribute: dict[str, Any]) -> bool:
    if attribute.get("sensitive") or attribute.get("write_only"):
        return True
    return any(
        contains_sensitive(value)
        for value in attribute.get("nested_type", {}).get("attributes", {}).values()
    )


@dataclass(frozen=True)
class ProviderSchema:
    source: str
    resources: dict[str, Any]

    @classmethod
    def from_json(cls, payload: dict[str, Any], source: str) -> ProviderSchema:
        if str(payload.get("format_version", "")).split(".")[0] != "1":
            raise GenerationError("Unsupported Terraform/OpenTofu provider schema format")
        candidates = [
            schema
            for name, schema in payload.get("provider_schemas", {}).items()
            if name == source or name.endswith("/" + source)
        ]
        if len(candidates) != 1:
            raise GenerationError(
                f"Cannot uniquely identify installed provider schema for {source}"
            )
        return cls(source, candidates[0].get("resource_schemas", {}))

    def supports(self, resource_type: str) -> bool:
        return resource_type in self.resources

    def block(self, resource_type: str) -> dict[str, Any]:
        if not self.supports(resource_type):
            raise GenerationError(f"Installed provider has no resource {resource_type}")
        return self.resources[resource_type]["block"]

    def configurable(self, resource_type: str) -> set[str]:
        return {
            name
            for name, attr in self.block(resource_type).get("attributes", {}).items()
            if attr.get("optional") or attr.get("required")
        }

    def clean(self, resource_type: str, body: Body) -> Body:
        return clean_body(body, self.block(resource_type))


def clean_body(body: Body, schema: dict[str, Any]) -> Body:
    result = body.copy()
    attributes = schema.get("attributes", {})
    for name in list(result.attributes):
        attribute = attributes.get(name)
        if attribute is None:
            raise GenerationError(
                f"Provider-generated configuration contains an unknown attribute: {name}"
            )
        if not (attribute.get("optional") or attribute.get("required")):
            del result.attributes[name]
    blocks = schema.get("block_types", {})
    result.blocks = []
    for block in body.blocks:
        if block.name not in blocks:
            raise GenerationError(
                f"Provider-generated configuration contains an unknown block: {block.name}"
            )
        if block.labels:
            raise GenerationError("Labeled nested provider blocks are not supported")
        result.blocks.append(
            NestedBlock(block.name, (), clean_body(block.body, blocks[block.name]["block"]), "")
        )
    return result
