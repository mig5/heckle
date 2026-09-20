from __future__ import annotations

from heckle.compatibility import provider_spec, known_provider_behaviour_for

from heckle.core.compilation import Candidate, ImportPlan
from heckle.errors import UnsafeChange
from heckle.hcl.schema import ProviderSchema
from heckle.hcl.types import Body
from heckle.providers.base import ProviderSpec
from heckle.providers.native import NativeProvider
from .handlers import ForgejoHandlers
from .repository import KNOWN_PROVIDER_BEHAVIOUR_FIELDS, normalize_repository


class ForgejoProvider(ForgejoHandlers, NativeProvider):
    spec = provider_spec("forgejo")
    MANAGED_RESOURCE_TYPES = frozenset({
        "forgejo_branch_protection", "forgejo_repository",
        "forgejo_repository_webhook", "forgejo_team",
    })
    HANDLERS = {"repository": "repository", "team": "team", "repository_webhook": "webhook", "branch_protection": "protection"}
    UNSUPPORTED = {
        "organization": ("not_importable", "svalabs/forgejo 1.6.0 organization implements no ImportState method"),
        "team_member": ("not_importable", "svalabs/forgejo 1.6.0 team_member has no verified import implementation"),
        "collaborator": ("inventory_only", "svalabs/forgejo 1.6.0 collaborator has no verified import implementation"),
        "deploy_key": ("not_importable", "svalabs/forgejo 1.6.0 deploy_key has no verified import implementation"),
        "team_repository": ("provider_unsupported", "Team repository grants are not exposed by the selected Forgejo provider"),
        "organization_webhook": ("provider_unsupported", "Selected Forgejo provider exposes repository webhooks only"),
        "organization_member": ("inventory_only", "Existing users and organization memberships are never recreated"),
        "repository_variable": ("inventory_only", "Variable values are redacted and no import contract is verified"),
        "organization_variable": ("inventory_only", "Variable values are redacted and no import contract is verified"),
        "repository_secret": ("inventory_only", "Secret values are unavailable and never replaced by placeholders"),
        "organization_secret": ("inventory_only", "Secret values are unavailable and never replaced by placeholders"),
    }

    def adjust(self, candidate: Candidate, body: Body, schema: ProviderSchema, plan: ImportPlan) -> None:
        if candidate.resource_type == "forgejo_repository":
            candidate.ignore_attributes.update(normalize_repository(body))
        if candidate.resource_type == "forgejo_team":
            # organization and organization_id are mutually exclusive.
            if body.attributes.get("organization_id", "null") != "null":
                body.attributes.pop("organization", None)
        if candidate.resource_type == "forgejo_branch_protection":
            # The provider rejects even configured false/empty dependent fields
            # when their enabling toggle is false. Omit inert subordinate fields.
            for enabled, fields in {
                "enable_push": ("enable_push_whitelist",),
                "enable_push_whitelist": ("push_whitelist_usernames", "push_whitelist_teams", "push_whitelist_deploy_keys"),
                "enable_merge_whitelist": ("merge_whitelist_usernames", "merge_whitelist_teams"),
                "enable_approvals_whitelist": ("approvals_whitelist_usernames", "approvals_whitelist_teams"),
                "enable_status_check": ("status_check_contexts",),
            }.items():
                if body.attributes.get(enabled, "false") != "true":
                    for field in fields:
                        body.attributes.pop(field, None)
    def known_provider_behaviour(self, change: UnsafeChange):
        return known_provider_behaviour_for(self.spec, change)
