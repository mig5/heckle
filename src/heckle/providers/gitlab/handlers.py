"""GitLab-specific import contracts and parent graph handling."""

from __future__ import annotations

from heckle.core.compilation import Candidate, ImportPlan
from heckle.core.model import Entity, ForgeModel
from heckle.errors import GenerationError
from heckle.compatibility import unmanaged_attributes


class GitLabHandlers:
    def group(self, entity: Entity, model: ForgeModel, plan: ImportPlan) -> Candidate:
        level = 0
        parent = entity.parent
        seen = {entity.uid}
        while parent is not None:
            if parent in seen:
                raise GenerationError("GitLab group parent cycle")
            seen.add(parent)
            ancestor = model.entities.get(parent)
            if ancestor is None:
                break
            level += 1
            parent = ancestor.parent
        candidate = self.make(
            entity,
            "group",
            entity.remote_id,
            module=f"gitlab_group_level_{level}",
            parent_attribute="parent_id",
        )
        candidate.ignore_attributes.update(unmanaged_attributes(self.spec, "gitlab_group"))
        return candidate

    def project(self, entity: Entity, model: ForgeModel, plan: ImportPlan) -> Candidate:
        candidate = self.make(entity, "project", entity.remote_id, parent_attribute="namespace_id")
        candidate.ignore_attributes.update(unmanaged_attributes(self.spec, "gitlab_project"))
        return candidate

    def scoped(self, entity: Entity, model: ForgeModel, plan: ImportPlan) -> Candidate | None:
        kind = entity.native_kind
        if entity.native.get("inherited"):
            plan.coverage.add(
                entity.key, kind, "skipped", "Inherited policy belongs to its original namespace"
            )
            return None
        parent = str(entity.native["_parent_id"])
        attribute = (
            "group_id"
            if kind == "group_membership"
            else (
                "project"
                if kind == "project_membership"
                else "group" if kind.startswith("group_") else "project"
            )
        )
        identifier = entity.remote_id
        if kind in {"branch_protection", "tag_protection"}:
            identifier = str(entity.native["name"])
        candidate = self.make(entity, kind, f"{parent}:{identifier}", parent_attribute=attribute)
        candidate.ignore_attributes.update(unmanaged_attributes(self.spec, f"gitlab_{kind}"))
        if entity.kind == "webhook":
            candidate.ignore_attributes.update(
                {"token", "signing_token", "custom_headers", "url_variables"}
            )
        if kind == "project_approval_rule" and entity.native.get("contains_hidden_groups"):
            plan.coverage.add(
                entity.key,
                kind,
                "inventory_only",
                "Hidden approver groups cannot be faithfully reconstructed",
            )
            return None
        if kind == "project_approval_rule" and entity.native.get("rule_type") == "any_approver":
            plan.coverage.add(
                entity.key,
                kind,
                "inventory_only",
                "GitLab provider 19.3.0 cannot faithfully encode imported any_approver rules because report_type is RequiredWith rule_type",
            )
            return None
        if (
            kind == "project_approval_rule"
            and entity.native.get("rule_type") == "report_approver"
            and entity.native.get("name") != "Coverage-Check"
        ):
            plan.coverage.add(
                entity.key,
                kind,
                "inventory_only",
                "GitLab provider 19.3.0 can only recreate report_approver rules named Coverage-Check",
            )
            return None
        return candidate
