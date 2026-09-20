from __future__ import annotations

from heckle.hcl.render import emit_config_fields, reindent_block
from heckle.providers.github.model.types import Model

ORG_COLLECTION_META = {
    "github_organization_ruleset": "rulesets",
    "github_organization_role": "roles",
    "github_organization_custom_properties": "custom_properties",
    "github_organization_webhook": "webhooks",
    "github_actions_organization_variable": "actions_variables",
    "github_actions_organization_secret": "actions_secrets",
    "github_dependabot_organization_secret": "dependabot_secrets",
    "github_actions_organization_secret_repositories": "actions_secret_repositories",
    "github_dependabot_organization_secret_repositories": "dependabot_secret_repositories",
}

ORG_SINGLETON_META = {
    "github_actions_organization_permissions": "actions_permissions",
    "github_actions_organization_workflow_permissions": "actions_workflow_permissions",
}

def emit_organization_module(model: Model) -> str:
    lines = [
        'variable "organization" {',
        "  type = any",
        "}",
        "",
        'variable "repository_ids" {',
        "  type    = map(number)",
        "  default = {}",
        "}",
        "",
        'variable "team_ids" {',
        "  type    = map(number)",
        "  default = {}",
        "}",
        "",
    ]
    settings = model.families.get("github_organization_settings")
    if settings:
        lines.extend(['resource "github_organization_settings" "this" {'])
        lines.extend(emit_config_fields(settings, "var.organization.settings", 2))
        lines.extend(["}", ""])

    for rtype, path in ORG_SINGLETON_META.items():
        family = model.families.get(rtype)
        if not family:
            continue
        lines.extend([f'resource "{rtype}" "this" {{'])
        lines.extend(emit_config_fields(family, f"var.organization.{path}", 2))
        lines.extend(["}", ""])

    for rtype, path in ORG_COLLECTION_META.items():
        family = model.families.get(rtype)
        if not family:
            continue
        special_ruleset = rtype == "github_organization_ruleset"
        names = ["this", "legacy_name"] if special_ruleset else ["this"]
        for resource_name in names:
            lines.extend(
                [
                    f'resource "{rtype}" "{resource_name}" {{',
                    (
                        f"  for_each = {{ for key, value in try(var.organization.{path}, {{}}) : key => value "
                        + (
                            "if try(value.manage_name, true)"
                            if resource_name == "this"
                            else "if !try(value.manage_name, true)"
                        )
                        + " }"
                        if special_ruleset
                        else f"  for_each = try(var.organization.{path}, {{}})"
                    ),
                    "",
                ]
            )
            if rtype in {"github_actions_organization_variable"}:
                lines.append("  variable_name = each.key")
            elif rtype in {
                "github_actions_organization_secret",
                "github_dependabot_organization_secret",
            }:
                lines.extend(
                    [
                        "  secret_name = each.key",
                        '  value       = "managed-outside-opentofu"',
                    ]
                )
            elif rtype == "github_organization_custom_properties":
                lines.append("  property_name = each.key")
            elif rtype in {
                "github_actions_organization_secret_repositories",
                "github_dependabot_organization_secret_repositories",
            }:
                lines.extend(
                    [
                        "  secret_name = each.key",
                        "  selected_repository_ids = [",
                        "    for repository_key in each.value.repository_keys :",
                        "    var.repository_ids[repository_key]",
                        "  ]",
                    ]
                )
            exclude = {"repository_keys", "manage_name", "observed_name"}
            lines.extend(
                emit_config_fields(family, "each.value", 2, exclude_attrs=exclude)
            )
            if rtype in {
                "github_actions_organization_secret",
                "github_dependabot_organization_secret",
            }:
                # The selected-repository allowlist is managed by the separate
                # *_organization_secret_repositories resource. The secret
                # resource also reads that remote field, so ignoring it here
                # prevents the two resources from fighting over the same list.
                lines.extend(
                    [
                        "",
                        "  lifecycle {",
                        "    ignore_changes = [value, selected_repository_ids]",
                        "  }",
                    ]
                )
            elif rtype == "github_organization_webhook":
                lines.extend(
                    [
                        "",
                        "  lifecycle {",
                        "    ignore_changes = [configuration[0].secret]",
                        "  }",
                    ]
                )
            elif special_ruleset and resource_name == "legacy_name":
                lines.extend(
                    ["", "  lifecycle {", "    ignore_changes = [name]", "  }"]
                )
            elif family.lifecycle_raw:
                lines.append("")
                lines.extend(reindent_block(family.lifecycle_raw, 2))
            lines.extend(["}", ""])
    return "\n".join(lines)
