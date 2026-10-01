"""Shared provider compiler. Resource/import semantics belong to small handlers."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from heckle.core.compilation import (
    Candidate,
    CompiledProject,
    CompiledResource,
    ImportPlan,
    Reference,
)
from heckle.core.model import Entity, ForgeModel
from heckle.errors import GenerationError
from heckle.hcl.render import hcl_string, literal_string
from heckle.compatibility import empty_string_unset_attributes
from heckle.hcl.schema import ProviderSchema, contains_sensitive
from heckle.hcl.types import Body, Resource
from heckle.providers.base import ProviderAdapter


class NativeProvider(ProviderAdapter):
    """Dispatch native object kinds to explicit mapping functions, not heuristics."""

    HANDLERS: dict[str, str] = {}
    UNSUPPORTED: dict[str, tuple[str, str]] = {}

    def plan(self, model: ForgeModel, workspace: Path) -> ImportPlan:
        plan = ImportPlan(model)
        for entity in model.ordered():
            if entity.native_kind == "user_namespace":
                plan.coverage.add(
                    entity.key,
                    entity.native_kind,
                    "not_applicable",
                    "Existing personal namespace; account settings are not managed",
                )
                continue
            if entity.native.get("archived") and entity.kind in {"repository", "namespace"}:
                plan.coverage.add(entity.key, entity.native_kind, "skipped", "Archived object")
                continue
            handler = self.HANDLERS.get(entity.native_kind)
            if handler is None:
                status, reason = self.UNSUPPORTED.get(
                    entity.native_kind,
                    ("inventory_only", "No verified import mapping in this Heckle adapter"),
                )
                plan.coverage.add(entity.key, entity.native_kind, status, reason)
                continue
            candidate = getattr(self, handler)(entity, model, plan)
            if candidate is not None:
                plan.candidates.append(candidate)
                plan.coverage.add(
                    entity.key,
                    entity.native_kind,
                    "import_planned",
                    resource_type=candidate.resource_type,
                )
        plan.validate()
        return plan

    def make(
        self,
        entity: Entity,
        suffix: str,
        import_id: str,
        *,
        module: str | None = None,
        parent_attribute: str | None = None,
        parent_value: str = "id",
    ) -> Candidate:
        resource_type = f"{self.spec.local_name}_{suffix}"
        candidate = Candidate(entity, resource_type, import_id, module or resource_type)
        if parent_attribute and entity.parent:
            candidate.references[parent_attribute] = Reference(entity.parent, parent_value)
        return candidate

    def compile(
        self, plan: ImportPlan, resources: list[Resource], schema: ProviderSchema
    ) -> CompiledProject:
        by_address = {resource.address: resource for resource in resources}
        candidates = {candidate.entity.uid: candidate for candidate in plan.candidates}
        project = CompiledProject(plan)
        for candidate in plan.candidates:
            for reference in candidate.references.values():
                target = candidates.get(reference.target_uid)
                if target:
                    project.output_attributes.setdefault(target.module, set()).add(
                        reference.attribute
                    )
        for candidate in plan.candidates:
            resource = by_address.get(candidate.flat_address)
            if resource is None:
                raise GenerationError(
                    f"Provider hydration omitted import target {candidate.flat_address}"
                )
            body = schema.clean(candidate.resource_type, resource.body)
            configurable_items = schema.configurable(candidate.resource_type) | set(
                schema.block(candidate.resource_type).get("block_types", {})
            )
            ignored = set(candidate.ignore_attributes) & configurable_items
            for name in candidate.drop_attributes | ignored:
                body.attributes.pop(name, None)
                body.blocks = [block for block in body.blocks if block.name != name]
            for name in empty_string_unset_attributes(self.spec, candidate.resource_type):
                if literal_string(body.attributes.get(name)) == "":
                    body.attributes.pop(name, None)
            self.adjust(candidate, body, schema, plan)
            # Adapter adjustments can discover additional per-instance fields
            # that must be unmanaged (for example provider-defaulted settings
            # whose parent feature is disabled). Fold those into this compiled
            # instance after adjustment rather than forcing module-wide ignores.
            adjusted_ignored = set(candidate.ignore_attributes) & configurable_items
            newly_ignored = adjusted_ignored - ignored
            for name in newly_ignored:
                body.attributes.pop(name, None)
                body.blocks = [block for block in body.blocks if block.name != name]
            ignored.update(adjusted_ignored)
            self._private_inputs(
                project, candidate, body, schema.block(candidate.resource_type), ignored
            )
            # Use actual dependencies for managed parent objects. External parents
            # stay literal, because Heckle must not expand the requested scope.
            for attribute, reference in candidate.references.items():
                target = candidates.get(reference.target_uid)
                if target and attribute in schema.configurable(candidate.resource_type):
                    if target.module == candidate.module:
                        raise GenerationError(
                            "A recursive resource family needs separate dependency levels"
                        )
                    if reference.attribute not in schema.block(target.resource_type).get(
                        "attributes", {}
                    ):
                        raise GenerationError(f"Missing referenced attribute {reference.attribute}")
                    body.attributes[attribute] = (
                        f"module.{target.module}.objects[{hcl_string(target.key)}].{reference.attribute}"
                    )
            required = {
                name
                for name, attribute in schema.block(candidate.resource_type)
                .get("attributes", {})
                .items()
                if attribute.get("required")
            }
            missing = [name for name in required if body.attributes.get(name, "null") == "null"]
            if missing:
                raise GenerationError(
                    f"Hydrated {candidate.resource_type} is missing required values: {', '.join(sorted(missing))}"
                )
            project.resources.append(CompiledResource(candidate, body, ignored))
        project.assign_presence_profiles()
        return project

    def adjust(
        self, candidate: Candidate, body: Body, schema: ProviderSchema, plan: ImportPlan
    ) -> None:
        """Native adapters override this for documented provider quirks."""

    def _private_inputs(
        self,
        project: CompiledProject,
        candidate: Candidate,
        body: Body,
        block_schema: dict[str, Any],
        ignored: set[str],
        path: tuple[str, ...] = (),
    ) -> None:
        for name, definition in block_schema.get("attributes", {}).items():
            secret = contains_sensitive(definition)
            webhook_url = candidate.entity.kind == "webhook" and name in {
                "url",
                "config",
                "configuration",
            }
            if not (secret or webhook_url):
                continue
            address = ".".join((*path, name))
            # Write-only optional values have no trustworthy imported value.
            if secret and definition.get("optional") and not webhook_url:
                body.attributes.pop(name, None)
                ignored.add(address)
                continue
            value = body.attributes.get(name)
            if value is None or value == "null":
                continue
            digest = hashlib.sha256(f"{candidate.flat_address}/{address}".encode()).hexdigest()[:12]
            variable = f"input_{candidate.resource_type}_{digest}"
            project.variables[variable] = (
                f"Private {address} for {candidate.entity.key}; supply the existing value."
            )
            # Retain the hydrated expression only in memory so the final adoption
            # safety plan can use the existing remote value without publishing it.
            project.validation_inputs[variable] = value
            body.attributes[name] = f"var.{variable}"
        for block in body.blocks:
            definition = block_schema.get("block_types", {}).get(block.name, {})
            # Use wildcard-independent top-level ignores for secret nested blocks.
            nested_ignored: set[str] = set()
            self._private_inputs(
                project,
                candidate,
                block.body,
                definition.get("block", {}),
                nested_ignored,
                path + (block.name,),
            )
            if nested_ignored:
                ignored.add(block.name)

    def rendered_imports(self, project: CompiledProject) -> list[tuple[str, str]]:
        """Return final native addresses in dependency order for state-only adoption."""
        by_uid = {item.candidate.entity.uid: item for item in project.resources}
        ordered: list[CompiledResource] = []
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(item: CompiledResource) -> None:
            uid = item.candidate.entity.uid
            if uid in visited:
                return
            if uid in visiting:
                raise GenerationError(
                    f"Resource dependency cycle while ordering adoption imports: {uid}"
                )
            visiting.add(uid)
            for reference in item.candidate.references.values():
                target = by_uid.get(reference.target_uid)
                if target is not None:
                    visit(target)
            visiting.remove(uid)
            visited.add(uid)
            ordered.append(item)

        for item in sorted(project.resources, key=lambda resource: resource.address):
            visit(item)
        return [
            (item.address, item.candidate.import_id)
            for item in ordered
            if item.address not in project.skip_imports
        ]

    def render(
        self,
        project: CompiledProject,
        destination: Path,
        *,
        prevent_destroy: bool,
        split_teams: bool,
    ) -> None:
        from heckle.generators.project import write_project

        write_project(project, self.spec, destination, prevent_destroy=prevent_destroy)
