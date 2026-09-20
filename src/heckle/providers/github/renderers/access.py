from __future__ import annotations

from heckle.hcl.render import emit_config_fields
from heckle.providers.github.model.types import Model

def emit_access_module(model: Model) -> str:
    family = model.families.get("github_team_repository")
    if not family:
        return ""
    lines = [
        'variable "teams" {',
        "  type = any",
        "}",
        "",
        'variable "team_ids" {',
        "  type = map(number)",
        "}",
        "",
        'variable "repository_names" {',
        "  type = map(string)",
        "}",
        "",
        "locals {",
        "  repository_grants = merge({}, [",
        "    for team_key, team in var.teams : {",
        "      for repository_key, config in try(team.repositories, {}) :",
        '      "${team_key}/${repository_key}" => {',
        "        team_key       = team_key",
        "        repository_key = repository_key",
        "        config         = config",
        "      }",
        "    }",
        "  ]...)",
        "}",
        "",
        'resource "github_team_repository" "this" {',
        "  for_each = local.repository_grants",
        "",
        "  team_id    = var.team_ids[each.value.team_key]",
        "  repository = var.repository_names[each.value.repository_key]",
    ]
    lines.extend(emit_config_fields(family, "each.value.config", 2))
    lines.extend(["}", ""])
    return "\n".join(lines)
