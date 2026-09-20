from __future__ import annotations

from heckle.hcl.render import emit_config_fields
from heckle.providers.github.model.types import Model

def emit_members_module(model: Model) -> str:
    family = model.families.get("github_membership")
    if not family:
        return "\n".join(
            [
                'variable "members" {',
                '  type = any',
                '}',
                '',
                'output "usernames" {',
                '  value = {}',
                '}',
                '',
            ]
        )
    lines = [
        'variable "members" {',
        "  type = any",
        "}",
        "",
        'resource "github_membership" "this" {',
        "  for_each = var.members",
        "",
        "  username = each.key",
    ]
    lines.extend(emit_config_fields(family, "each.value", 2))
    lines.extend(
        [
            "}",
            "",
            'output "usernames" {',
            "  value = { for key, membership in github_membership.this : key => membership.username }",
            "}",
            "",
        ]
    )
    return "\n".join(lines)
