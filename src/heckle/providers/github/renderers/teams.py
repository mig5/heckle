from __future__ import annotations

from heckle.hcl.render import hcl_literal

from collections import defaultdict

from heckle.hcl.render import emit_config_fields
from heckle.providers.github.model.builder import compute_team_levels
from heckle.providers.github.model.types import Model

def emit_teams_module(model: Model) -> str:
    team_family = model.families.get("github_team")
    membership_family = model.families.get("github_team_membership")
    levels = compute_team_levels(model)
    grouped: dict[int, list[str]] = defaultdict(list)
    for slug, level in levels.items():
        grouped[level].append(slug)
    for values in grouped.values():
        values.sort(key=str.casefold)

    lines = [
        'variable "teams" {',
        "  type = any",
        "}",
        "",
        'variable "member_usernames" {',
        "  type = map(string)",
        "}",
        "",
    ]

    if not team_family:
        lines.extend(
            [
                "locals {",
                "  team_ids      = {}",
                "  team_node_ids = {}",
                "}",
                "",
            ]
        )

    if team_family:
        for level in sorted(grouped):
            keys = hcl_literal(grouped[level])
            lines.extend(
                [
                    "locals {",
                    f"  teams_level_{level} = {{",
                    "    for key, team in var.teams : key => team",
                    f"    if contains({keys}, key)",
                    "  }",
                    "}",
                    "",
                    f'resource "github_team" "level_{level}" {{',
                    f"  for_each = local.teams_level_{level}",
                    "",
                ]
            )
            lines.extend(
                emit_config_fields(
                    team_family,
                    "each.value.settings",
                    2,
                    exclude_attrs={"parent_team_key"},
                )
            )
            if level > 0:
                prior_maps = ", ".join(
                    f"{{ for key, team in github_team.level_{prior} : key => team.id }}"
                    for prior in range(level)
                    if prior in grouped
                )
                lines.append(
                    "  parent_team_id = "
                    f"merge({{}}, {prior_maps})[each.value.settings.parent_team_key]"
                )
            lines.extend(["}", ""])

        id_maps = ", ".join(
            f"{{ for key, team in github_team.level_{level} : key => team.id }}"
            for level in sorted(grouped)
        )
        node_maps = ", ".join(
            f"{{ for key, team in github_team.level_{level} : key => team.node_id }}"
            for level in sorted(grouped)
        )
        lines.extend(
            [
                "locals {",
                f"  team_ids      = merge({{}}, {id_maps})",
                f"  team_node_ids = merge({{}}, {node_maps})",
                "}",
                "",
            ]
        )

    if membership_family:
        lines.extend(
            [
                "locals {",
                "  memberships = merge({}, [",
                "    for team_key, team in var.teams : {",
                "      for username, config in try(team.members, {}) :",
                '      "${team_key}/${username}" => {',
                "        team_key = team_key",
                "        username = username",
                "        config   = config",
                "      }",
                "    }",
                "  ]...)",
                "}",
                "",
                'resource "github_team_membership" "this" {',
                "  for_each = local.memberships",
                "",
                "  team_id  = local.team_ids[each.value.team_key]",
                "  username = var.member_usernames[each.value.username]",
            ]
        )
        lines.extend(emit_config_fields(membership_family, "each.value.config", 2))
        lines.extend(["}", ""])

    lines.extend(
        [
            'output "ids" {',
            "  value = local.team_ids",
            "}",
            "",
            'output "node_ids" {',
            "  value = local.team_node_ids",
            "}",
            "",
        ]
    )
    return "\n".join(lines)
