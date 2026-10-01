from __future__ import annotations

from heckle.compatibility import provider_spec, known_provider_behaviour_for

from heckle.core.compilation import Candidate, ImportPlan
from heckle.errors import UnsafeChange
from heckle.hcl.schema import ProviderSchema
from heckle.hcl.types import Body
from heckle.hcl.render import hcl_literal, literal_int, literal_string
from heckle.providers.base import ProviderSpec
from heckle.providers.native import NativeProvider
from .handlers import GitLabHandlers


class GitLabProvider(GitLabHandlers, NativeProvider):
    spec = provider_spec("gitlab")
    HANDLERS = {
        "group": "group",
        "project": "project",
        **{
            kind: "scoped"
            for kind in (
                "group_membership",
                "project_membership",
                "branch_protection",
                "tag_protection",
                "group_hook",
                "project_hook",
                "deploy_key",
                "project_environment",
                "project_approval_rule",
                "group_label",
                "project_label",
                "group_badge",
                "project_badge",
            )
        },
    }
    UNSUPPORTED = {
        "group_variable": (
            "inventory_only",
            "CI variable values are redacted; variables stay outside managed HCL",
        ),
        "project_variable": (
            "inventory_only",
            "CI variable values are redacted; variables stay outside managed HCL",
        ),
        "push_rules": (
            "inventory_only",
            "The parent project hydration owns its embedded push_rules block; no duplicate resource",
        ),
        "pipeline_schedule": (
            "inventory_only",
            "Schedule ownership, inputs and variables need a dedicated import contract",
        ),
        "group_runner": (
            "inventory_only",
            "Runner registration and secret credentials are not reproduced",
        ),
        "project_runner": (
            "inventory_only",
            "Runner registration and secret credentials are not reproduced",
        ),
        "group_access_request": (
            "inventory_only",
            "Pending access requests are not active memberships",
        ),
    }

    def adjust(
        self, candidate: Candidate, body: Body, schema: ProviderSchema, plan: ImportPlan
    ) -> None:
        if (
            candidate.resource_type == "gitlab_project"
            and plan.model.source.namespace_type == "user"
        ):
            namespace = plan.model.entities[candidate.entity.parent]
            body.attributes["namespace_id"] = hcl_literal(int(namespace.remote_id))
        if candidate.resource_type == "gitlab_project":
            # container_expiration_policy.cadence is optional/computed but provider
            # 19.3.0 validates any configured value against a non-empty enum.  The
            # provider flattener copies the API cadence unconditionally, so an empty
            # API value must be represented by omission rather than cadence = "".
            for block in body.blocks:
                if (
                    block.name == "container_expiration_policy"
                    and literal_string(block.body.attributes.get("cadence")) == ""
                ):
                    block.body.attributes.pop("cadence", None)
        if candidate.resource_type == "gitlab_group" and any(
            b.name == "default_branch_protection_defaults" for b in body.blocks
        ):
            body.attributes.pop("default_branch_protection", None)
        if candidate.resource_type == "gitlab_branch_protection":
            # Provider v19 represents EE ACLs as nested attributes rather than blocks.
            for acl, fallback in (
                ("allowed_to_push", "push_access_level"),
                ("allowed_to_merge", "merge_access_level"),
            ):
                if body.attributes.get(acl, "null").strip() not in {"null", "[]"}:
                    body.attributes.pop(fallback, None)
        if candidate.resource_type == "gitlab_tag_protection":
            # allowed_to_create uses an ExactlyOneOf-style selector contract.
            # Provider 19.3.0 can hydrate an access_level alongside an explicit
            # user/group/deploy-key selector, even though that combination cannot be
            # represented as valid configuration. Prefer the explicit identity.
            for block in body.blocks:
                if block.name != "allowed_to_create":
                    continue
                explicit_id = any(
                    (literal_int(block.body.attributes.get(name)) or 0) > 0
                    for name in ("user_id", "group_id", "deploy_key_id")
                )
                if explicit_id or literal_string(block.body.attributes.get("access_level")) == "":
                    block.body.attributes.pop("access_level", None)
        if candidate.resource_type == "gitlab_project_environment":
            # Provider 19.3.0 declares strict parent dependencies: a Kubernetes
            # namespace requires cluster_agent_id, and a Flux resource path requires
            # both. Imported API state can expose the children independently or as
            # empty values, so omit any child whose required parent is absent after
            # the generic empty-string normalization has run.
            cluster_agent = body.attributes.get("cluster_agent_id")
            namespace = body.attributes.get("kubernetes_namespace")
            if cluster_agent is None or cluster_agent.strip() == "null":
                body.attributes.pop("kubernetes_namespace", None)
                body.attributes.pop("flux_resource_path", None)
            elif namespace is None or namespace.strip() == "null":
                body.attributes.pop("flux_resource_path", None)
        if candidate.resource_type in {"gitlab_project_hook", "gitlab_group_hook"}:
            # GitLab/provider 19.3.0 normalises push_events_branch_filter to an
            # empty string when the strategy is all_branches. Keeping an explicit
            # filter in that mode can produce an inconsistent-result error after
            # apply (upstream #6574), and the value is semantically inactive anyway.
            if literal_string(body.attributes.get("branch_filter_strategy")) == "all_branches":
                body.attributes.pop("push_events_branch_filter", None)
        if candidate.resource_type == "gitlab_project_approval_rule":
            # report_type and rule_type are coupled by the provider schema. Ordinary
            # imported rules can hydrate an empty report_type; emitting rule_type without
            # report_type still triggers RequiredWith validation. For non-report rules,
            # both are therefore left for the provider to infer from imported state.
            # Report-approver rules keep both values. Also avoid the provider-declared
            # conflict between all-protected-branches and an explicit branch list.
            if literal_string(body.attributes.get("rule_type")) != "report_approver":
                body.attributes.pop("report_type", None)
                body.attributes.pop("rule_type", None)
            if body.attributes.get("applies_to_all_protected_branches") == "true":
                body.attributes.pop("protected_branch_ids", None)

    def known_provider_behaviour(self, change: UnsafeChange):
        return known_provider_behaviour_for(self.spec, change)
