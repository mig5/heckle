from __future__ import annotations

from heckle.core.compilation import Candidate, ImportPlan
from heckle.core.model import Entity, ForgeModel
from heckle.compatibility import unmanaged_attributes


class GiteaHandlers:
    def organization(self, entity: Entity, model: ForgeModel, plan: ImportPlan) -> Candidate:
        candidate = self.make(entity, "org", entity.remote_id)
        # The provider sends this only while creating an organisation and does
        # not read or update it. Its schema default is not imported state.
        candidate.ignore_attributes.update(unmanaged_attributes(self.spec, "gitea_org"))
        return candidate

    def repository(self, entity: Entity, model: ForgeModel, plan: ImportPlan) -> Candidate:
        candidate = self.make(entity, "repository", entity.remote_id, parent_attribute="username", parent_value="name")
        candidate.ignore_attributes.update({
            "auto_init", "gitignores", "license", "readme", "issue_labels", "source_template", "source_template_items",
            "migration_clone_address", "migration_clone_addresse", "migration_service", "migration_service_auth_username",
            "migration_service_auth_password", "migration_service_auth_token", "migration_issue_labels", "migration_lfs",
            "migration_lfs_endpoint", "migration_milestones", "migration_releases",
        })
        candidate.ignore_attributes.update(unmanaged_attributes(self.spec, "gitea_repository"))
        return candidate

    def team(self, entity: Entity, model: ForgeModel, plan: ImportPlan) -> Candidate:
        candidate = self.make(entity, "team", entity.remote_id, parent_attribute="organisation", parent_value="name")
        # This provider exposes an authoritative repo list on the team resource.
        if not entity.native.get("_repositories_complete"):
            candidate.ignore_attributes.add("repositories")
            plan.coverage.warnings.append(f"Repository grants for {entity.key} are unreadable; repositories is ignored")
        if entity.native.get("include_all_repositories") is True:
            candidate.ignore_attributes.add("repositories")
        return candidate

    def webhook(self, entity: Entity, model: ForgeModel, plan: ImportPlan) -> Candidate:
        candidate = self.make(entity, "repository_webhook", f"{entity.native['_parent_path']}/{entity.remote_id}", parent_attribute="name", parent_value="name")
        candidate.ignore_attributes.update({"secret", "authorization_header"})
        return candidate

    def team_members(self, entity: Entity, model: ForgeModel, plan: ImportPlan) -> Candidate:
        return self.make(entity, "team_members", entity.remote_id, parent_attribute="team_id")
