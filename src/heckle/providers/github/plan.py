from __future__ import annotations

import threading
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from heckle.core.identifiers import escape_colons, filesystem_name
from heckle.hcl.render import hcl_bool, hcl_label, hcl_list, hcl_string
from heckle.providers.github.model.inventory import read_json


def secret_lifecycle(*, indent: str, extra_ignored: Iterable[str] = ()) -> str:
    ignored = ["value", *extra_ignored]
    ignored_lines = "\n".join(f"{indent}    {attribute}," for attribute in ignored)
    return f"""{indent}lifecycle {{
{indent}  # GitHub never returns the existing value. Preserve out-of-band values.
{indent}  ignore_changes = [
{ignored_lines}
{indent}  ]
{indent}}}"""

class GitHubPlanBuilder:
    """Provider import contracts are built from inventory, never from live API calls."""
    def __init__(self, org: str) -> None:
        self.org = org
        self.imports: list[tuple[str, str]] = []
        self.manual_resources: list[str] = []
        self.addresses: set[str] = set()
        self._import_counts: Counter[str] = Counter()
        self._manual_counts: Counter[str] = Counter()
        self._lock = threading.RLock()
        self.notes: list[str] = []

    def note_not_managed(self, message: str) -> None:
        self.notes.append(message)

    def add_import(self, resource_type: str, label_source: str, import_id: str) -> str:
        label = hcl_label(resource_type.removeprefix("github_"), label_source)
        address = f"{resource_type}.{label}"
        with self._lock:
            if address in self.addresses:
                raise RuntimeError(f"Duplicate Terraform/OpenTofu address: {address}")
            self.addresses.add(address)
            self.imports.append((address, import_id))
            self._import_counts[resource_type] += 1
        return label

    def add_manual_resource(self, resource_type: str, block: str) -> None:
        with self._lock:
            self.manual_resources.append(block.rstrip() + "\n")
            self._manual_counts[resource_type] += 1

    def add_organization_webhook(self, hook: dict[str, Any]) -> None:
        resource_type = "github_organization_webhook"
        hook_id = str(hook["id"])
        config = hook.get("config") or {}
        url = config.get("url")
        if not url:
            self.note_not_managed(
                f"Organization webhook {hook_id} could not be rendered because GitHub did not return its URL."
            )
            return
        label = self.add_import(resource_type, hook_id, hook_id)
        content_type = config.get("content_type") or "json"
        insecure_ssl = str(config.get("insecure_ssl", "0")) == "1"
        events = hook.get("events") or ["push"]
        active = bool(hook.get("active", True))
        block = f"""resource "{resource_type}" "{label}" {{
  active = {hcl_bool(active)}
  events = {hcl_list(events)}

  configuration {{
    url          = {hcl_string(url)}
    content_type = {hcl_string(content_type)}
    insecure_ssl = {hcl_bool(insecure_ssl)}
  }}

  lifecycle {{
    # GitHub does not return the existing webhook secret.
    ignore_changes = [configuration[0].secret]
  }}
}}
"""
        self.add_manual_resource(resource_type, block)

    def add_organization_secret(self, kind: str, secret: dict[str, Any]) -> None:
        name = secret["name"]
        visibility = secret.get("visibility", "all")
        if kind == "actions":
            resource_type = "github_actions_organization_secret"
            allowlist_type = "github_actions_organization_secret_repositories"
        else:
            resource_type = "github_dependabot_organization_secret"
            allowlist_type = "github_dependabot_organization_secret_repositories"

        label = self.add_import(resource_type, name, name)
        block = f"""resource "{resource_type}" "{label}" {{
  secret_name = {hcl_string(name)}
  visibility  = {hcl_string(visibility)}

  # Required only to satisfy the provider schema. It is ignored during and
  # after import, so the real GitHub secret is never replaced.
  value = "managed-outside-opentofu"

{secret_lifecycle(indent="  ", extra_ignored=("selected_repository_ids",) if visibility == "selected" else ())}
}}
"""
        self.add_manual_resource(resource_type, block)
        if visibility == "selected":
            self.add_import(allowlist_type, name, name)

    def add_repository_secret(self, kind: str, repo: str, secret_name: str) -> None:
        resource_type = (
            "github_actions_secret" if kind == "actions" else "github_dependabot_secret"
        )
        label = self.add_import(
            resource_type,
            f"{repo}:{secret_name}",
            f"{repo}:{secret_name}",
        )
        block = f"""resource "{resource_type}" "{label}" {{
  repository  = {hcl_string(repo)}
  secret_name = {hcl_string(secret_name)}

  # Required only to satisfy the provider schema. It is ignored during and
  # after import, so the real GitHub secret is never replaced.
  value = "managed-outside-opentofu"

{secret_lifecycle(indent="  ")}
}}
"""
        self.add_manual_resource(resource_type, block)

    def add_environment_secret(
        self, repo: str, environment: str, secret_name: str
    ) -> None:
        resource_type = "github_actions_environment_secret"
        import_id = f"{repo}:{escape_colons(environment)}:{secret_name}"
        label = self.add_import(
            resource_type,
            f"{repo}:{environment}:{secret_name}",
            import_id,
        )
        block = f"""resource "{resource_type}" "{label}" {{
  repository  = {hcl_string(repo)}
  environment = {hcl_string(environment)}
  secret_name = {hcl_string(secret_name)}

  # Required only to satisfy the provider schema. It is ignored during and
  # after import, so the real GitHub secret is never replaced.
  value = "managed-outside-opentofu"

{secret_lifecycle(indent="  ")}
}}
"""
        self.add_manual_resource(resource_type, block)

    def add_repository_webhook(self, repo: str, hook: dict[str, Any]) -> None:
        resource_type = "github_repository_webhook"
        hook_id = str(hook["id"])
        config = hook.get("config") or {}
        url = config.get("url")
        if not url:
            self.note_not_managed(
                f"Webhook {repo}/{hook_id} could not be rendered because GitHub did not return its URL."
            )
            return
        label = self.add_import(
            resource_type,
            f"{repo}:{hook_id}",
            f"{repo}/{hook_id}",
        )
        content_type = config.get("content_type") or "json"
        insecure_ssl = str(config.get("insecure_ssl", "0")) == "1"
        events = hook.get("events") or ["push"]
        active = bool(hook.get("active", True))
        block = f"""resource "{resource_type}" "{label}" {{
  repository = {hcl_string(repo)}
  active     = {hcl_bool(active)}
  events     = {hcl_list(events)}

  configuration {{
    url          = {hcl_string(url)}
    content_type = {hcl_string(content_type)}
    insecure_ssl = {hcl_bool(insecure_ssl)}
  }}

  lifecycle {{
    # GitHub returns an existing webhook secret only as ********.
    ignore_changes = [configuration[0].secret]
  }}
}}
"""
        self.add_manual_resource(resource_type, block)

    def _organization(self, inventory: Path, active: set[str]) -> None:
        def load(name: str, default: Any = None) -> Any:
            return read_json(inventory / name, default)
        organization = load("organization.json", {})
        self.add_import("github_organization_settings", self.org, str(organization["id"]))
        for member in load("memberships.json", []):
            username = member["user"]["login"]
            if member.get("state", "active") == "active":
                self.add_import("github_membership", username, f"{self.org}:{username}")
        for team in load("teams.json", []):
            slug, remote_id = team["slug"], str(team["id"])
            self.add_import("github_team", remote_id, remote_id)
            for member in load(f"teams/{slug}/memberships.json", []):
                username = (member.get("user") or {}).get("login")
                # GitHub's team-membership response does not always embed a user.
                # The inventory collector persists the requested login explicitly.
                username = username or member.get("_username")
                if not username:
                    raise ValueError(f"Team membership in {slug} is missing a username")
                if member.get("state", "active") == "active":
                    self.add_import("github_team_membership", f"{remote_id}:{username}", f"{remote_id}:{username}")
            for repo in load(f"teams/{slug}/repositories.json", []):
                if repo["name"] in active:
                    self.add_import("github_team_repository", f"{remote_id}:{repo['name']}", f"{remote_id}:{repo['name']}")
        for filename, rtype, field in (
            ("organization-rulesets.json", "github_organization_ruleset", "id"),
            ("custom-organization-roles.json", "github_organization_role", "id"),
            ("custom-properties/schema.json", "github_organization_custom_properties", "property_name"),
            ("actions/organization-variables.json", "github_actions_organization_variable", "name"),
        ):
            for item in load(filename, []):
                value = str(item[field]); self.add_import(rtype, value, value)
        for filename, rtype in (
            ("actions/organization-permissions.json", "github_actions_organization_permissions"),
            ("actions/organization-workflow-permissions.json", "github_actions_organization_workflow_permissions"),
        ):
            if load(filename) is not None:
                self.add_import(rtype, self.org, self.org)
        for kind in ("actions", "dependabot"):
            for secret in load(f"{kind}/organization-secrets.json", []):
                self.add_organization_secret(kind, secret)
        for hook in load("organization-webhooks.json", []):
            self.add_organization_webhook(hook)

    def build(self, inventory: Path, *, personal: bool = False) -> GitHubPlanBuilder:
        def load(name: str, default: Any = None) -> Any:
            return read_json(inventory / name, default)
        repositories = [r for r in load("repositories.json", []) if not r.get("archived")]
        if not personal:
            self._organization(inventory, {r["name"] for r in repositories})
        for repository in sorted(repositories, key=lambda r: r["name"]):
            repo = repository["name"]
            self.add_import("github_repository", repo, repo)
            prefix = f"repositories/{repo}"
            for filename, rtype, field, separator in (
                ("direct-collaborators", "github_repository_collaborator", "login", ":"),
                ("branch-protection-rules", "github_branch_protection", "pattern", ":"),
                ("rulesets", "github_repository_ruleset", "id", ":"),
                ("actions-variables", "github_actions_variable", "name", ":"),
                ("deploy-keys", "github_repository_deploy_key", "id", ":"),
                ("autolinks", "github_repository_autolink_reference", "id", "/"),
            ):
                for item in load(f"{prefix}/{filename}.json", []):
                    if filename == "rulesets" and (item.get("source_type", "Repository") != "Repository" or item.get("source", repo) not in {repo, f"{self.org}/{repo}"}):
                        continue
                    value = str(item[field])
                    self.add_import(rtype, f"{repo}:{value}", f"{repo}{separator}{value}")
            if load(f"{prefix}/actions-permissions.json") is not None:
                self.add_import("github_actions_repository_permissions", repo, repo)
            for prop in load(f"{prefix}/custom-properties.json", []) or []:
                name = prop.get("property_name")
                if name:
                    self.add_import("github_repository_custom_property", f"{repo}:{name}", f"{self.org}:{repo}:{name}")
            for kind in ("actions", "dependabot"):
                for secret in load(f"{prefix}/{kind}-secrets.json", []):
                    self.add_repository_secret(kind, repo, secret["name"])
            for hook in load(f"{prefix}/webhooks.json", []):
                self.add_repository_webhook(repo, hook)
            for environment in load(f"{prefix}/environments.json", []):
                name = environment["name"]
                self.add_import("github_repository_environment", f"{repo}:{name}", f"{repo}:{escape_colons(name)}")
                env_prefix = f"{prefix}/environments/{filesystem_name(name)}"
                for variable in load(f"{env_prefix}/variables.json", []):
                    key = variable["name"]
                    self.add_import("github_actions_environment_variable", f"{repo}:{name}:{key}", f"{repo}:{escape_colons(name)}:{key}")
                for secret in load(f"{env_prefix}/secrets.json", []):
                    self.add_environment_secret(repo, name, secret["name"])
        return self
