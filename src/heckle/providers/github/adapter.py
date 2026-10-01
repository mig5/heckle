"""GitHub's mature native model behind Heckle's forge/provider boundary.

Existing final module/resource addresses are retained. Inventory-to-import planning
is separate from the API collector, so snapshot replay uses the same contracts.
"""

from __future__ import annotations

from heckle.compatibility import provider_spec

from dataclasses import dataclass
from pathlib import Path
import re

from heckle.core.compilation import Candidate, CompiledProject, ImportPlan, ImportRecord
from heckle.core.model import Entity, ForgeModel
from heckle.core.security import write_private_json
from heckle.errors import GenerationError
from heckle.hcl.schema import ProviderSchema
from heckle.hcl.types import Resource
from heckle.hcl.parser import extract_top_blocks, parse_resource
from heckle.providers.base import ProviderAdapter, ProviderSpec
from heckle.providers.github.plan import GitHubPlanBuilder
from heckle.providers.github.model.builder import DomainModelBuilder
from heckle.providers.github.renderers.project import write_output


@dataclass
class GitHubNativePlan:
    inventory: Path
    builder: GitHubPlanBuilder


def restore_snapshot(model: ForgeModel, destination: Path) -> None:
    files = model.extensions.get("github", {}).get("files")
    profile = "user.json" if model.source.namespace_type == "user" else "organization.json"
    if not isinstance(files, dict) or profile not in files:
        raise GenerationError("GitHub snapshot lacks the native inventory")
    destination.mkdir(parents=True)
    for relative, value in files.items():
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts or path.suffix != ".json":
            raise GenerationError("Invalid path in native inventory")
        write_private_json(destination / path, value)


def public_iteration_keys(root: Path) -> None:
    """Keep private values out of for_each while retaining original addresses.

    This is a transformation of our own generated module HCL, not arbitrary user
    code. Keys are names/patterns/IDs already present in declarative imports.
    """
    for path in sorted((root / "modules").rglob("main.tf")):
        blocks, _ = extract_top_blocks(path)
        text = path.read_text(encoding="utf-8")
        for block in blocks:
            if block.kind != "resource":
                continue
            resource = parse_resource(block)
            expression = resource.body.attributes.get("for_each")
            if expression is None:
                continue
            name = f"instances_{resource.resource_type}_{resource.name}"
            local = f"local.{name}"
            replacement = block.raw.replace("each.value", f"{local}[each.key]")
            # A provider-native emitter writes for_each on one physical line.
            replacement, count = re.subn(
                r"(?m)^\s*for_each\s*=.*$",
                f"  for_each = toset(try(nonsensitive(keys({local})), keys({local})))",
                replacement,
                count=1,
            )
            if count != 1:
                raise GenerationError("Unable to make GitHub iteration keys public")
            replacement = f"locals {{\n  {name} = {expression}\n}}\n\n" + replacement
            text = text.replace(block.raw, replacement, 1)
        path.write_text(text, encoding="utf-8")


class GitHubProvider(ProviderAdapter):
    spec = provider_spec("github")

    def plan(self, model: ForgeModel, workspace: Path) -> ImportPlan:
        inventory = workspace / "github-provider-inventory"
        restore_snapshot(model, inventory)
        builder = GitHubPlanBuilder(model.source.scope)
        builder.build(inventory, personal=model.source.namespace_type == "user")
        plan = ImportPlan(model, native=GitHubNativePlan(inventory, builder))
        for address, import_id in builder.imports:
            rtype = address.split(".", 1)[0]
            entity = Entity("native", import_id, import_id, rtype, {})
            candidate = Candidate(entity, rtype, import_id, "github_native", flat_override=address)
            plan.candidates.append(candidate)
            plan.coverage.add(import_id, rtype, "import_planned", resource_type=rtype)
        if model.source.namespace_type == "user":
            plan.coverage.add(
                model.source.scope,
                "user_namespace",
                "not_applicable",
                "Existing personal namespace; account settings are not managed",
            )
        plan.coverage.warnings.extend(builder.notes)
        plan.coverage.warnings.extend(
            model.extensions.get("github", {})
            .get("files", {})
            .get("coverage.json", {})
            .get("not_managed", [])
        )
        plan.validate()
        return plan

    def bootstrap_extra(self, plan: ImportPlan) -> str:
        return "\n".join(sorted(plan.native.builder.manual_resources))

    def compile(
        self, plan: ImportPlan, resources: list[Resource], schema: ProviderSchema
    ) -> CompiledProject:
        imports = [ImportRecord(address, import_id) for address, import_id in self.imports(plan)]
        cleaned = [
            Resource(
                r.resource_type,
                r.name,
                schema.clean(r.resource_type, r.body),
                r.lifecycle_raw,
                r.source,
            )
            for r in resources
        ]
        model = DomainModelBuilder(plan.model.source.scope, plan.native.inventory, self.spec).build(
            cleaned, imports
        )
        missing = sorted(set(model.families) - set(schema.resources))
        if missing:
            raise GenerationError(
                "Provider lacks synthetic native resources: " + ", ".join(missing)
            )
        project = CompiledProject(plan, native=model)
        from heckle.hcl.render import hcl_string

        project.validation_inputs.update(
            {name: hcl_string(variable.value) for name, variable in model.webhook_variables.items()}
        )
        return project

    def rendered_imports(self, project: CompiledProject) -> list[tuple[str, str]]:
        return [
            (record.new_address, record.import_id)
            for record in project.native.imports
            if record.new_address is not None and record.new_address not in project.skip_imports
        ]

    def render(
        self,
        project: CompiledProject,
        destination: Path,
        *,
        prevent_destroy: bool,
        split_teams: bool,
    ) -> None:
        model = project.native
        model.imports = [
            record for record in model.imports if record.new_address not in project.skip_imports
        ]
        write_output(
            destination,
            model,
            prevent_destroy=prevent_destroy,
            split_teams=split_teams,
            spec=self.spec,
            source=project.plan.model.source,
        )
        from heckle.providers.github.renderers.common import assert_literal_provider_owner

        assert_literal_provider_owner(destination, project.plan.model.source.scope)
        public_iteration_keys(destination)
