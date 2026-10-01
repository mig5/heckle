from __future__ import annotations
from collections import OrderedDict
from pathlib import Path
from typing import Any, Mapping, Sequence
from heckle.errors import GenerationError
from heckle.hcl.types import Resource
from heckle.providers.github.model.inventory import (
    classify_repository_resource,
    import_by_address,
    load_identity_maps,
    resource_import_id,
)
from heckle.providers.github.model.types import ImportRecord, Model
from heckle.providers.github.model.helpers import (
    get_or_create,
    compute_team_levels,
    rewrite_team_import_levels,
)
from heckle.providers.github.handlers.access import AccessHandlers
from heckle.providers.github.handlers.repositories import RepositoryHandlers
from heckle.providers.github.handlers.actions import ActionsHandlers
from heckle.providers.github.handlers.webhooks import WebhookHandlers
from heckle.providers.github.handlers.organization import OrganizationHandlers
from heckle.providers.base import ProviderSpec
from heckle.compatibility import provider_spec as audited_provider_spec


class DomainModelBuilder(
    AccessHandlers, RepositoryHandlers, ActionsHandlers, WebhookHandlers, OrganizationHandlers
):
    """Normalize hydrated GitHub provider resources into the canonical model.

    The builder owns transformation state and dispatches each provider resource
    family to a small handler.  This keeps GitHub inventory concerns, HCL parsing,
    provider quirks, and final rendering separate from one another.
    """

    REPOSITORY_HANDLERS = {
        "github_repository": "_repository_settings",
        "github_repository_collaborator": "_repository_collaborator",
        "github_branch_protection": "_repository_branch_protection",
        "github_repository_ruleset": "_repository_ruleset",
        "github_actions_repository_permissions": "_repository_actions_permissions",
        "github_actions_variable": "_repository_actions_variable",
        "github_actions_secret": "_repository_secret",
        "github_dependabot_secret": "_repository_secret",
        "github_repository_environment": "_repository_environment",
        "github_actions_environment_variable": "_repository_environment_item",
        "github_actions_environment_secret": "_repository_environment_item",
        "github_repository_webhook": "_repository_webhook",
        "github_repository_deploy_key": "_repository_deploy_key",
        "github_repository_autolink_reference": "_repository_autolink",
        "github_repository_custom_property": "_repository_custom_property",
    }

    ORG_COLLECTION_NAMES = {
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

    ORG_SINGLETON_NAMES = {
        "github_actions_organization_permissions": "actions_permissions",
        "github_actions_organization_workflow_permissions": "actions_workflow_permissions",
    }

    TEAM_TYPES = {
        "github_team",
        "github_team_membership",
        "github_team_repository",
    }

    def __init__(
        self, org: str, inventory: Path, provider_spec: ProviderSpec | None = None
    ) -> None:
        self.org = org
        self.inventory = inventory
        self.provider_spec = provider_spec or audited_provider_spec("github")
        self.model: Model
        self.identities: Mapping[str, Any]
        self.imports: dict[str, ImportRecord]
        self.unsupported: list[str]
        self.long_rulesets: list[dict[str, str]]

    def build(self, resources: Sequence[Resource], import_records: list[ImportRecord]) -> Model:
        self.model = Model(org=self.org, imports=import_records)
        self.identities = load_identity_maps(self.inventory)
        self.imports = import_by_address(import_records)
        self.unsupported = []
        self.long_rulesets = []

        resources_by_address = {resource.address: resource for resource in resources}
        missing = sorted(set(self.imports) - set(resources_by_address))
        if missing:
            self.model.report["missing_resource_configurations"] = missing
            raise GenerationError(
                "provider hydration did not produce configuration for import targets:\n  "
                + "\n  ".join(missing[:30])
                + ("\n  ..." if len(missing) > 30 else "")
            )

        for repo_name in sorted(self.identities["repo_by_name"], key=str.casefold):
            self.model.repositories[repo_name] = OrderedDict()
        for _team_id, slug in sorted(
            self.identities["team_id_to_slug"].items(), key=lambda pair: pair[1].casefold()
        ):
            self.model.teams[slug] = OrderedDict()

        for resource in sorted(resources, key=lambda item: item.address):
            if not self._handle_resource(resource):
                self.unsupported.append(resource.address)

        team_levels = compute_team_levels(self.model)
        rewrite_team_import_levels(self.model, team_levels)
        self.model.report.update(
            {
                "unsupported_resources": self.unsupported,
                "team_levels": dict(sorted(team_levels.items())),
                "long_ruleset_names_preserved_with_ignore_changes": self.long_rulesets,
                "repository_count": len(self.model.repositories),
                "team_count": len(self.model.teams),
                "member_count": len(self.model.members),
                "webhook_variables": [
                    {
                        "name": variable.name,
                        "scope": variable.scope,
                        "hook_id": variable.hook_id,
                    }
                    for variable in self.model.webhook_variables.values()
                ],
            }
        )
        return self.model

    def _handle_resource(self, resource: Resource) -> bool:
        rtype = resource.resource_type
        import_id = resource_import_id(resource, self.imports)

        if rtype == "github_membership":
            self._membership(resource, import_id)
            return True
        if rtype in self.TEAM_TYPES:
            self._team_resource(resource, import_id)
            return True

        repo = classify_repository_resource(resource, import_id, self.identities)
        handler_name = self.REPOSITORY_HANDLERS.get(rtype)
        if repo is not None and handler_name is not None:
            repository = get_or_create(self.model.repositories, repo)
            getattr(self, handler_name)(resource, import_id, repo, repository)
            return True

        return self._organization_resource(resource, import_id)
