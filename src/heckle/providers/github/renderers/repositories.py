from __future__ import annotations

from typing import Any, Mapping

from heckle.hcl.render import emit_config_fields, reindent_block, safe_identifier
from heckle.providers.github.model.types import Model

REPO_FAMILY_META: dict[str, dict[str, Any]] = {
    "github_branch_default": {
        "path": "default_branch",
        "depth": 0,
        "association": [
            "repository = github_repository.this[each.value.repository_key].name"
        ],
    },
    "github_repository_pages": {
        "path": "pages",
        "depth": 0,
        "association": [
            "repository = github_repository.this[each.value.repository_key].name"
        ],
    },
    "github_repository_collaborator": {
        "path": "collaborators",
        "depth": 1,
        "key_attr": "username",
        "association": [
            "repository = github_repository.this[each.value.repository_key].name",
            "username   = each.value.item_key",
        ],
    },
    "github_branch_protection": {
        "path": "branch_protections",
        "depth": 1,
        "key_attr": "pattern",
        "association": [
            "repository_id = github_repository.this[each.value.repository_key].node_id",
            "pattern       = each.value.item_key",
        ],
    },
    "github_repository_ruleset": {
        "path": "rulesets",
        "depth": 1,
        "association": [
            "repository = github_repository.this[each.value.repository_key].name"
        ],
        "special_ruleset": True,
    },
    "github_actions_repository_permissions": {
        "path": "actions.permissions",
        "depth": 0,
        "association": [
            "repository = github_repository.this[each.value.repository_key].name"
        ],
    },
    "github_actions_variable": {
        "path": "actions.variables",
        "depth": 1,
        "association": [
            "repository    = github_repository.this[each.value.repository_key].name",
            "variable_name = each.value.item_key",
        ],
    },
    "github_actions_secret": {
        "path": "actions.secrets",
        "depth": 1,
        "association": [
            "repository  = github_repository.this[each.value.repository_key].name",
            "secret_name = each.value.item_key",
            'value       = "managed-outside-opentofu"',
        ],
        "secret": True,
    },
    "github_dependabot_secret": {
        "path": "dependabot.secrets",
        "depth": 1,
        "association": [
            "repository  = github_repository.this[each.value.repository_key].name",
            "secret_name = each.value.item_key",
            'value       = "managed-outside-opentofu"',
        ],
        "secret": True,
    },
    "github_repository_environment": {
        "path": "environments",
        "depth": 1,
        "settings": True,
        "association": [
            "repository  = github_repository.this[each.value.repository_key].name",
            "environment = each.value.item_key",
        ],
    },
    "github_actions_environment_variable": {
        "path": "environments.*.variables",
        "depth": 2,
        "association": [
            "repository    = github_repository.this[each.value.repository_key].name",
            "environment   = each.value.environment_key",
            "variable_name = each.value.item_key",
        ],
    },
    "github_actions_environment_secret": {
        "path": "environments.*.secrets",
        "depth": 2,
        "association": [
            "repository  = github_repository.this[each.value.repository_key].name",
            "environment = each.value.environment_key",
            "secret_name = each.value.item_key",
            'value       = "managed-outside-opentofu"',
        ],
        "secret": True,
    },
    "github_repository_webhook": {
        "path": "webhooks",
        "depth": 1,
        "association": [
            "repository = github_repository.this[each.value.repository_key].name"
        ],
        "webhook": True,
    },
    "github_repository_deploy_key": {
        "path": "deploy_keys",
        "depth": 1,
        "association": [
            "repository = github_repository.this[each.value.repository_key].name"
        ],
    },
    "github_repository_autolink_reference": {
        "path": "autolinks",
        "depth": 1,
        "association": [
            "repository = github_repository.this[each.value.repository_key].name"
        ],
    },
    "github_repository_custom_property": {
        "path": "custom_properties",
        "depth": 1,
        "association": [
            "repository    = github_repository.this[each.value.repository_key].name",
            "property_name = each.value.item_key",
        ],
    },
}

def traversal(path: str, root: str) -> str:
    value = root
    for part in path.split("."):
        if part == "*":
            raise ValueError("wildcard traversal requires special handling")
        value += f".{part}"
    return value

def emit_repo_flatten_local(rtype: str, meta: Mapping[str, Any]) -> tuple[str, str]:
    local_name = safe_identifier(rtype.removeprefix("github_"))
    path = str(meta["path"])
    depth = int(meta["depth"])
    settings = bool(meta.get("settings"))
    if depth == 0:
        expr = traversal(path, "repository")
        text = "\n".join(
            [
                f"  {local_name} = {{",
                "    for repository_key, repository in var.repositories :",
                "    repository_key => {",
                "      repository_key = repository_key",
                f"      config         = {expr}",
                "    }",
                f"    if try({expr}, null) != null",
                "  }",
            ]
        )
    elif depth == 1:
        collection_expr = traversal(path, "repository")
        config_expr = "item.settings" if settings else "item"
        text = "\n".join(
            [
                f"  {local_name} = merge({{}}, [",
                "    for repository_key, repository in var.repositories : {",
                f"      for item_key, item in try({collection_expr}, {{}}) :",
                '      "${repository_key}/${item_key}" => {',
                "        repository_key = repository_key",
                "        item_key        = item_key",
                f"        config          = {config_expr}",
                "      }",
                "    }",
                "  ]...)",
            ]
        )
    else:
        # environments.*.<collection>
        tail = path.split(".*.", 1)[1]
        text = "\n".join(
            [
                f"  {local_name} = merge({{}}, [",
                "    for repository_key, repository in var.repositories : merge({}, [",
                "      for environment_key, environment in try(repository.environments, {}) : {",
                f"        for item_key, item in try(environment.{tail}, {{}}) :",
                '        "${repository_key}/${environment_key}/${item_key}" => {',
                "          repository_key  = repository_key",
                "          environment_key = environment_key",
                "          item_key        = item_key",
                "          config          = item",
                "        }",
                "      }",
                "    ]...)",
                "  ]...)",
            ]
        )
    return local_name, text

def emit_repository_module(model: Model, prevent_destroy: bool) -> str:
    repo_family = model.families.get("github_repository")
    lines = [
        'variable "repositories" {',
        "  type = any",
        "}",
        "",
        'variable "team_ids" {',
        "  type    = map(number)",
        "  default = {}",
        "}",
        "",
    ]
    local_defs: list[str] = []
    family_locals: dict[str, str] = {}
    for rtype, meta in REPO_FAMILY_META.items():
        if rtype in model.families:
            local_name, text = emit_repo_flatten_local(rtype, meta)
            family_locals[rtype] = local_name
            local_defs.append(text)
    if local_defs:
        lines.append("locals {")
        lines.extend(local_defs)
        lines.extend(["}", ""])

    if repo_family:
        lines.extend(
            [
                'resource "github_repository" "this" {',
                "  for_each = var.repositories",
                "",
                "  name = each.key",
            ]
        )
        lines.extend(emit_config_fields(repo_family, "each.value.settings", 2))
        if prevent_destroy:
            lines.extend(
                [
                    "",
                    "  lifecycle {",
                    "    prevent_destroy = true",
                    "  }",
                ]
            )
        lines.extend(["}", ""])

    for rtype, meta in REPO_FAMILY_META.items():
        family = model.families.get(rtype)
        if not family:
            continue
        local_name = family_locals[rtype]
        special_ruleset = bool(meta.get("special_ruleset"))
        resource_names = ["this"]
        if special_ruleset:
            resource_names = ["this", "legacy_name"]
        for resource_name in resource_names:
            lines.extend(
                [
                    f'resource "{rtype}" "{resource_name}" {{',
                    (
                        f"  for_each = {{ for key, value in local.{local_name} : key => value "
                        + (
                            "if try(value.config.manage_name, true)"
                            if resource_name == "this"
                            else "if !try(value.config.manage_name, true)"
                        )
                        + " }"
                        if special_ruleset
                        else f"  for_each = local.{local_name}"
                    ),
                    "",
                ]
            )
            for association in meta["association"]:
                lines.append("  " + association)
            exclude = {"manage_name", "observed_name"} if special_ruleset else set()
            config_family = family
            lines.extend(
                emit_config_fields(
                    config_family, "each.value.config", 2, exclude_attrs=exclude
                )
            )
            if meta.get("secret"):
                lines.extend(
                    [
                        "",
                        "  lifecycle {",
                        "    ignore_changes = [value]",
                        "  }",
                    ]
                )
            elif meta.get("webhook"):
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
                    [
                        "",
                        "  lifecycle {",
                        "    ignore_changes = [name]",
                        "  }",
                    ]
                )
            elif family.lifecycle_raw:
                lines.append("")
                lines.extend(reindent_block(family.lifecycle_raw, 2))
            lines.extend(["}", ""])

    if repo_family:
        names_value = "{ for key, repository in github_repository.this : key => repository.name }"
        ids_value = "{ for key, repository in github_repository.this : key => repository.repo_id }"
        node_ids_value = "{ for key, repository in github_repository.this : key => repository.node_id }"
    else:
        names_value = ids_value = node_ids_value = "{}"

    lines.extend(
        [
            'output "names" {',
            f"  value = {names_value}",
            "}",
            "",
            'output "ids" {',
            f"  value = {ids_value}",
            "}",
            "",
            'output "node_ids" {',
            f"  value = {node_ids_value}",
            "}",
            "",
        ]
    )
    return "\n".join(lines)
