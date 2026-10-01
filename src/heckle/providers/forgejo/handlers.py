from __future__ import annotations

from heckle.core.compilation import Candidate, ImportPlan
from heckle.core.model import Entity, ForgeModel
from .repository import IGNORED_FIELDS, IMPORT_SETTINGS_WARNING


class ForgejoHandlers:
    def repository(self, entity: Entity, model: ForgeModel, plan: ImportPlan) -> Candidate:
        candidate = self.make(entity, "repository", entity.key)
        candidate.ignore_attributes.update(IGNORED_FIELDS)
        if IMPORT_SETTINGS_WARNING not in plan.coverage.warnings:
            plan.coverage.warnings.append(IMPORT_SETTINGS_WARNING)
        return candidate

    def team(self, entity: Entity, model: ForgeModel, plan: ImportPlan) -> Candidate:
        return self.make(entity, "team", entity.key)

    def webhook(self, entity: Entity, model: ForgeModel, plan: ImportPlan) -> Candidate:
        candidate = self.make(
            entity,
            "repository_webhook",
            f"{entity.native['_parent_path']}/{entity.remote_id}",
            parent_attribute="repository_id",
        )
        candidate.ignore_attributes.update({"secret", "authorization_header", "branch_filter"})
        return candidate

    def protection(self, entity: Entity, model: ForgeModel, plan: ImportPlan) -> Candidate | None:
        branch = entity.native.get("rule_name") or entity.native.get("branch_name")
        if not branch or "/" in branch:
            plan.coverage.add(
                entity.key,
                entity.native_kind,
                "not_importable",
                "Forgejo 1.6.0 import IDs require exactly owner/repo/branch; slash-containing patterns cannot be represented",
            )
            return None
        return self.make(
            entity,
            "branch_protection",
            f"{entity.native['_parent_path']}/{branch}",
            parent_attribute="repository_id",
        )
