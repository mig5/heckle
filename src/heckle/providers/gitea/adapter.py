from __future__ import annotations

from heckle.compatibility import provider_spec

from pathlib import Path
from heckle.core.compilation import Candidate, ImportPlan
from heckle.core.model import Entity, ForgeModel
from heckle.hcl.render import hcl_list
from heckle.hcl.schema import ProviderSchema
from heckle.hcl.types import Body
from heckle.providers.base import ProviderSpec
from heckle.providers.native import NativeProvider
from .handlers import GiteaHandlers


class GiteaProvider(GiteaHandlers, NativeProvider):
    spec = provider_spec("gitea")
    MANAGED_RESOURCE_TYPES = frozenset({
        "gitea_org", "gitea_repository", "gitea_repository_webhook",
        "gitea_team", "gitea_team_members",
    })
    HANDLERS = {"organization": "organization", "repository": "repository", "team": "team", "repository_webhook": "webhook", "team_members": "team_members"}
    UNSUPPORTED = {
        "team_member": ("inventory_only", "Covered by the team's authoritative gitea_team_members resource when discovery is complete"),
        "team_repository": ("inventory_only", "Covered by the repositories attribute on gitea_team when discovery is complete"),
        "organization_member": ("inventory_only", "Organization membership is not a separately verified import contract"),
        "repository_variable": ("inventory_only", "Variable values are deliberately redacted"),
        "organization_variable": ("inventory_only", "Variable values are deliberately redacted"),
        "repository_secret": ("inventory_only", "Secret values are unavailable and never replaced by placeholders"),
        "organization_secret": ("inventory_only", "Secret values are unavailable and never replaced by placeholders"),
    }

    def plan(self, model: ForgeModel, workspace: Path) -> ImportPlan:
        plan = super().plan(model, workspace)
        for team in model.of_kind("team"):
            if team.native.get("_members_complete"):
                entity = Entity("membership", team.key + "/members", team.remote_id, "team_members", {"members": team.native["_members"]}, team.uid, team.owner)
                candidate = self.team_members(entity, model, plan)
                plan.candidates.append(candidate)
                plan.coverage.add(entity.key, entity.native_kind, "import_planned", resource_type=candidate.resource_type)
        plan.validate()
        return plan

    def adjust(self, candidate: Candidate, body: Body, schema: ProviderSchema, plan: ImportPlan) -> None:
        if candidate.resource_type == "gitea_team":
            if candidate.entity.native.get("_repositories_complete"):
                body.attributes["repositories"] = hcl_list(candidate.entity.native["_repositories"])
            if body.attributes.get("units_map", "null") not in {"null", "{}"}:
                body.attributes.pop("units", None)
        if candidate.resource_type == "gitea_team_members":
            body.attributes["members"] = hcl_list(candidate.entity.native["members"])
