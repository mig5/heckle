from __future__ import annotations

from heckle.hcl.render import hcl_literal

import hashlib
import re
from pathlib import Path
from typing import Iterable

from heckle.errors import GenerationError
from heckle.hcl.render import render_value


def provider_pins_literal_owner(text: str, org: str) -> bool:
    """Return whether provider HCL pins owner to the requested literal org.

    HCL formatting may align the equals sign with surrounding attributes, so
    this deliberately accepts arbitrary horizontal whitespace around ``=``.
    """
    literal = re.escape(hcl_literal(org))
    return (
        re.search(
            rf"(?m)^\s*owner\s*=\s*{literal}\s*(?:#.*)?$",
            text,
        )
        is not None
    )


def assert_literal_provider_owner(output: Path, org: str) -> None:
    provider_path = output / "provider.tf"
    text = provider_path.read_text(encoding="utf-8")
    if not provider_pins_literal_owner(text, org):
        raise GenerationError(
            f"generated provider does not pin the requested organization: {provider_path}"
        )
    forbidden = ('variable "github_org"', "owner = var.github_org")
    for value in forbidden:
        if value in text:
            raise GenerationError(
                f"generated provider is still overridable via github_org: {provider_path}"
            )


def emit_root_modules() -> str:
    return """module "members" {
  source = "./modules/members"

  providers = {
    github = github
  }

  members = local.github.members
}

module "teams" {
  source = "./modules/teams"

  providers = {
    github = github
  }

  teams            = local.github.teams
  member_usernames = module.members.usernames
}

module "repositories" {
  source = "./modules/repositories"

  providers = {
    github = github
  }

  repositories = local.github.repositories
  team_ids      = module.teams.ids
}

module "access" {
  source = "./modules/access"

  providers = {
    github = github
  }

  teams            = local.github.teams
  team_ids         = module.teams.ids
  repository_names = module.repositories.names
}

module "organization" {
  source = "./modules/organization"

  providers = {
    github = github
  }

  organization   = local.github.organization
  repository_ids = module.repositories.ids
  team_ids       = module.teams.ids
}
"""


def identifier_stem(value: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_]+", "_", value.lower()).strip("_") or "item"
    return f"item_{stem}" if stem[0].isdigit() else stem


def filename_stem(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value.lower()).strip("-.") or "item"


def unique_render_names(
    keys: Iterable[str], *, local_prefix: str, file_prefix: str
) -> dict[str, tuple[str, str]]:
    """Return readable HCL-local/file names, adding hashes only on collisions."""
    local_owners: dict[str, str] = {}
    file_owners: dict[str, str] = {}
    result: dict[str, tuple[str, str]] = {}
    for key in sorted(keys, key=str.casefold):
        local = f"{local_prefix}_{identifier_stem(key)}"
        filename = f"{file_prefix}{filename_stem(key)}.tf"
        digest = hashlib.sha1(key.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]

        if local in local_owners and local_owners[local] != key:
            local = f"{local}_{digest}"
        file_key = filename.casefold()
        if file_key in file_owners and file_owners[file_key] != key:
            filename = filename.removesuffix(".tf") + f"-{digest}.tf"
            file_key = filename.casefold()

        local_owners[local] = key
        file_owners[file_key] = key
        result[key] = (local, filename)
    return result


def render_local(name: str, value: Any) -> str:
    rendered = render_value(value, 2)
    lines = ["locals {", f"  {name} = {rendered[0]}"]
    lines.extend(rendered[1:])
    lines.extend(["}", ""])
    return "\n".join(lines)


def render_merged_local(name: str, fragments: Sequence[str]) -> str:
    if not fragments:
        return f"locals {{\n  {name} = {{}}\n}}\n"
    lines = ["locals {", f"  {name} = merge("]
    lines.extend(f"    local.{fragment}," for fragment in fragments)
    lines.extend(["  )", "}", ""])
    return "\n".join(lines)


def render_root_model() -> str:
    return """locals {
  github = {
    organization = local.github_organization
    members      = local.github_members
    teams        = local.github_teams
    repositories = local.github_repositories
  }
}
"""
