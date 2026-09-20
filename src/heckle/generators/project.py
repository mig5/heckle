"""Render native resources as ordinary, editable local data and child modules.

Independent family locals are intentional: putting all parent references into a
single object and passing that object to every module creates dependency cycles.
Only public identity keys, never configuration values, are declassified for
resource for_each expressions.
"""
from __future__ import annotations

from collections import OrderedDict, defaultdict
from pathlib import Path
import hashlib
import re

from heckle.core.compilation import CompiledProject, CompiledResource, Family
from heckle.errors import GenerationError
from heckle.hcl.render import (body_presence_signature, body_presence_weight, body_to_value, emit_config_fields, hcl_string, render_value)
from heckle.providers.base import ProviderSpec


def local_block(name: str, value: object) -> str:
    lines = render_value(value, 2)
    return "\n".join(["locals {", f"  {name} = {lines[0]}", *lines[1:], "}", ""])


def stem(value: str) -> str:
    readable = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-").lower()[:100] or "scope"
    return readable


def domain_file(resource: CompiledResource, project: CompiledProject) -> str:
    entity = resource.candidate.entity
    # Group settings and scoped policies live beside their namespace; repository
    # settings and child resources live beside their repository/project.
    current = entity
    seen: set[str] = set()
    while current.kind not in {"namespace", "repository"} and current.parent:
        if current.uid in seen:
            raise GenerationError("Cycle while placing a native resource")
        seen.add(current.uid)
        parent = project.plan.model.entities.get(current.parent)
        if parent is None:
            break
        current = parent
    prefix = "project" if current.kind == "repository" and project.plan.model.source.forge == "gitlab" else "repository" if current.kind == "repository" else "group" if project.plan.model.source.forge == "gitlab" else "organization"
    # Always include a short stable identity hash: adding a colliding name later
    # must not rename the earlier file or overwrite it on case-insensitive hosts.
    digest = hashlib.sha256(current.uid.encode()).hexdigest()[:8]
    return f"{prefix}-{stem(current.key)}-{digest}.tf"


def emit_module(module: str, resources: list[CompiledResource], project: CompiledProject, *, prevent_destroy: bool) -> str:
    project.assign_presence_profiles()
    resource_type = resources[0].candidate.resource_type
    if any(item.candidate.resource_type != resource_type for item in resources):
        raise GenerationError("Different resource types assigned to the same module")
    profile_names = sorted({item.resource_name for item in resources}, key=lambda name: (name != "this", name))
    lines = [
        'variable "items" {', "  type = any", "}", "",
        'variable "profiles" {', "  type = map(string)", "}", "",
    ]
    for profile_name in profile_names:
        selected = [item for item in resources if item.resource_name == profile_name]
        # lifecycle policy must follow the same presence profile as the body.
        # A field that is inapplicable for one profile must not become ignored
        # for other instances where Heckle can safely manage it.
        ignored: set[str] = set()
        family = Family(resource_type)
        for item in selected:
            family.add(item.body, None)
            ignored.update(item.ignored)
        lines.extend([
            f'resource "{resource_type}" "{profile_name}" {{',
            f'  for_each = toset([for key, profile in var.profiles : key if profile == {hcl_string(profile_name)}])',
            "",
        ])
        try:
            lines.extend(emit_config_fields(
                family, "var.items[each.key]", 2, strict_presence=True
            ))
        except ValueError as exc:
            raise GenerationError(str(exc)) from exc
        if ignored or prevent_destroy:
            lines.extend(["", "  lifecycle {"])
            if prevent_destroy:
                lines.append("    prevent_destroy = true")
            if ignored:
                lines.append("    ignore_changes = [" + ", ".join(sorted(ignored)) + "]")
            lines.append("  }")
        lines.extend(["}", ""])

    lines.extend(['output "objects" {', "  value = merge("])
    for profile_name in profile_names:
        lines.append(f"    {{ for key, object in {resource_type}.{profile_name} : key => {{")
        for attribute in sorted(project.output_attributes.get(module, set())):
            lines.append(f"      {attribute} = object.{attribute}")
        lines.extend(["    } },"])
    lines.extend(["  )", "}", ""])
    return "\n".join(lines)

def imports_hcl(project: CompiledProject) -> str:
    return "\n".join(
        f"import {{\n  to = {item.address}\n  id = {hcl_string(item.candidate.import_id)}\n}}\n"
        for item in sorted(project.resources, key=lambda item: item.address)
        if item.address not in project.skip_imports
    )


def write_project(project: CompiledProject, spec: ProviderSpec, destination: Path, *, prevent_destroy: bool) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "versions.tf").write_text(spec.versions_hcl(), encoding="utf-8")
    (destination / "provider.tf").write_text(spec.configuration_hcl(project.plan.model.source), encoding="utf-8")
    files: dict[str, list[str]] = defaultdict(list)
    families: dict[str, list[CompiledResource]] = defaultdict(list)
    fragments: dict[str, list[str]] = defaultdict(list)
    for item in project.resources:
        families[item.candidate.module].append(item)
    for item in sorted(project.resources, key=lambda item: item.address):
        candidate = item.candidate
        digest = hashlib.sha256(candidate.flat_address.encode()).hexdigest()[:16]
        local = f"config_{digest}"
        files[domain_file(item, project)].append(local_block(local, OrderedDict([(candidate.key, body_to_value(item.body))])))
        fragments[candidate.module].append(local)
    for filename, blocks in files.items():
        (destination / filename).write_text("\n".join(blocks), encoding="utf-8")
    root: list[str] = ["# Keys are public resource identities; configuration values remain sensitive.", ""]
    modules: list[str] = []
    for module, resources in sorted(families.items()):
        root.extend(["locals {", f"  {module} = merge({{}}, " + ", ".join(f"local.{name}" for name in fragments[module]) + ")", "}", ""])
        profiles = OrderedDict((item.candidate.key, item.resource_name) for item in sorted(resources, key=lambda item: item.candidate.key))
        profile_lines = render_value(profiles, 4)
        modules.extend([
            f'module "{module}" {{', f'  source = "./modules/{module}"',
            f"  providers = {{ {spec.local_name} = {spec.local_name} }}",
            f"  items = local.{module}",
            f"  profiles = {profile_lines[0]}",
            *profile_lines[1:],
            "}", "",
        ])
        child = destination / "modules" / module
        child.mkdir(parents=True)
        (child / "versions.tf").write_text(spec.versions_hcl(child=True), encoding="utf-8")
        (child / "main.tf").write_text(emit_module(module, resources, project, prevent_destroy=prevent_destroy), encoding="utf-8")
    (destination / "heckle.tf").write_text("\n".join(root), encoding="utf-8")
    (destination / "modules.tf").write_text("\n".join(modules) or "# No importable resource families discovered.\n", encoding="utf-8")
    (destination / "imports.tf").write_text(imports_hcl(project), encoding="utf-8")
    if project.moved:
        moved = "\n".join(
            f"moved {{\n  from = {old}\n  to   = {new}\n}}\n"
            for old, new in sorted(project.moved)
        )
        (destination / "moved.tf").write_text(moved, encoding="utf-8")
    variables: list[str] = []
    for name, description in sorted(project.variables.items()):
        variables.append(f'variable "{name}" {{\n  description = {hcl_string(description)}\n  type = any\n  sensitive = true\n}}\n')
    if variables:
        (destination / "variables.tf").write_text("\n".join(variables), encoding="utf-8")
