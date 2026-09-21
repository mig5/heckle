"""Version-scoped provider compatibility knowledge.

Keep upstream-provider quirks here rather than scattering issue-specific conditionals
through the pipeline.  A quirk must identify the exact audited provider version and,
where possible, an upstream issue or documentation URL.  Provider bumps are therefore
explicit compatibility reviews rather than silent upgrades.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from heckle.providers.base import ProviderSpec
from heckle.errors import KnownProviderBehaviour, UnsafeChange


@dataclass(frozen=True)
class ProviderQuirk:
    id: str
    provider: str
    versions: frozenset[str]
    resource_type: str
    kind: str
    summary: str
    upstream: str
    attributes: tuple[str, ...] = ()
    guard: tuple[str, object] | None = None

    def applies(self, spec: ProviderSpec) -> bool:
        return self.provider == spec.source and spec.version in self.versions

    def warning(self) -> str:
        suffix = f" Upstream: {self.upstream}" if self.upstream else ""
        return f"Provider compatibility: {self.summary}{suffix}"


PROVIDER_SPECS: dict[str, ProviderSpec] = {
    "github": ProviderSpec("github", "github", "integrations/github", "6.13.0", "base_url", "GITHUB_TOKEN"),
    "gitlab": ProviderSpec("gitlab", "gitlab", "gitlabhq/gitlab", "19.3.0", "base_url", "GITLAB_TOKEN"),
    "gitea": ProviderSpec("gitea", "gitea", "go-gitea/gitea", "0.8.1", "base_url", "GITEA_TOKEN"),
    "forgejo": ProviderSpec("forgejo", "forgejo", "svalabs/forgejo", "1.6.0", "host", "FORGEJO_API_TOKEN"),
}


def provider_spec(forge: str) -> ProviderSpec:
    return PROVIDER_SPECS[forge]


# Exact versions that have been reviewed by Heckle.  Overriding a provider pin to a
# version outside this set requires an explicit experimental opt-in.
# Deliberately separate from PROVIDER_SPECS. Changing a pin must also update this
# audit allowlist after the compatibility checklist has been completed; tests make
# a casual version bump fail until that happens.
AUDITED_VERSIONS: dict[str, frozenset[str]] = {
    "integrations/github": frozenset({"6.13.0"}),
    "gitlabhq/gitlab": frozenset({"19.3.0"}),
    "go-gitea/gitea": frozenset({"0.8.1"}),
    "svalabs/forgejo": frozenset({"1.6.0"}),
}

# Resource families whose import/read/schema contracts were reviewed for the exact
# provider pin. This is deliberately separate from QUIRKS: an audited resource can
# require no workaround at all. Tests keep the GitLab set aligned with the adapter,
# so a newly-managed resource cannot silently bypass provider-compatibility review.
AUDITED_RESOURCE_TYPES: dict[tuple[str, str], frozenset[str]] = {
    ("integrations/github", "6.13.0"): frozenset({
        "github_actions_environment_secret", "github_actions_environment_variable",
        "github_actions_organization_permissions", "github_actions_organization_secret",
        "github_actions_organization_secret_repositories", "github_actions_organization_variable",
        "github_actions_organization_workflow_permissions", "github_actions_repository_permissions",
        "github_actions_secret",
        "github_actions_variable", "github_branch_default", "github_branch_protection",
        "github_dependabot_organization_secret", "github_dependabot_organization_secret_repositories",
        "github_dependabot_secret", "github_membership", "github_organization_custom_properties",
        "github_organization_role", "github_organization_ruleset", "github_organization_settings",
        "github_organization_webhook", "github_repository", "github_repository_autolink_reference",
        "github_repository_collaborator", "github_repository_custom_property",
        "github_repository_deploy_key", "github_repository_environment", "github_repository_pages",
        "github_repository_ruleset", "github_repository_webhook", "github_team",
        "github_team_membership", "github_team_repository",
    }),
    ("gitlabhq/gitlab", "19.3.0"): frozenset({
        "gitlab_group",
        "gitlab_project",
        "gitlab_group_membership",
        "gitlab_project_membership",
        "gitlab_branch_protection",
        "gitlab_tag_protection",
        "gitlab_group_hook",
        "gitlab_project_hook",
        "gitlab_deploy_key",
        "gitlab_project_environment",
        "gitlab_project_approval_rule",
        "gitlab_group_label",
        "gitlab_project_label",
        "gitlab_group_badge",
        "gitlab_project_badge",
    }),
    ("go-gitea/gitea", "0.8.1"): frozenset({
        "gitea_org", "gitea_repository", "gitea_repository_webhook",
        "gitea_team_members",
    }),
    ("svalabs/forgejo", "1.6.0"): frozenset({
        "forgejo_branch_protection", "forgejo_repository",
        "forgejo_repository_webhook", "forgejo_team",
    }),
}


QUIRKS: tuple[ProviderQuirk, ...] = (
    ProviderQuirk(
        id="gitea-organization-create-only-team-access",
        provider="go-gitea/gitea", versions=frozenset({"0.8.1"}),
        resource_type="gitea_org", kind="unmanaged_attributes",
        attributes=("repo_admin_change_team_access",),
        summary="Gitea's organisation team-access switch is create-only and is neither read nor updated by provider 0.8.1.",
        upstream="https://github.com/go-gitea/terraform-provider-gitea/blob/v0.8.1/gitea/resource_gitea_organisation.go",
    ),
    ProviderQuirk(
        id="gitea-repository-destroy-control",
        provider="go-gitea/gitea", versions=frozenset({"0.8.1"}),
        resource_type="gitea_repository", kind="unmanaged_attributes",
        attributes=("archive_on_destroy",),
        summary="archive_on_destroy controls provider deletion behaviour rather than imported repository state.",
        upstream="https://github.com/go-gitea/terraform-provider-gitea/blob/v0.8.1/gitea/resource_gitea_repository.go",
    ),
    ProviderQuirk(
        id="gitea-repository-unreadable-manual-merge-settings",
        provider="go-gitea/gitea", versions=frozenset({"0.8.1"}),
        resource_type="gitea_repository", kind="unmanaged_attributes",
        attributes=("allow_manual_merge", "autodetect_manual_merge"),
        summary=(
            "Gitea repository imports do not populate the manual-merge settings; provider "
            "defaults would otherwise produce a live update after state-only adoption."
        ),
        upstream="https://github.com/go-gitea/terraform-provider-gitea/blob/v0.8.1/gitea/resource_gitea_repository.go",
    ),
    ProviderQuirk(
        id="gitea-team-import-update-unsafe",
        provider="go-gitea/gitea", versions=frozenset({"0.8.1"}),
        resource_type="gitea_team", kind="warning",
        attributes=("permission", "units", "units_map"),
        summary=(
            "Imported modern and owner teams cannot be updated safely: provider 0.8.1 "
            "drops per-unit permissions, stringifies units with unstable ordering and rejects "
            "the imported none/owner permission modes. Teams remain inventory-only."
        ),
        upstream="https://github.com/go-gitea/terraform-provider-gitea/blob/v0.8.1/gitea/resource_gitea_team.go",
    ),
    ProviderQuirk(
        id="github-org-repository-creation-partial-patch",
        provider="integrations/github",
        versions=frozenset({"6.13.0"}),
        resource_type="github_organization_settings",
        kind="unmanaged_attributes",
        attributes=(
            "members_can_create_repositories",
            "members_can_create_public_repositories",
            "members_can_create_private_repositories",
            "members_can_create_internal_repositories",
        ),
        summary=(
            "GitHub organization repository-creation booleans are left unmanaged because "
            "the provider can silently change sibling values when only part of the coupled "
            "set is updated (issue #3429)."
        ),
        upstream="https://github.com/integrations/terraform-provider-github/issues/3429",
    ),
    ProviderQuirk(
        id="github-actions-empty-patterns",
        provider="integrations/github",
        versions=frozenset({"6.13.0"}),
        resource_type="github_actions_organization_permissions",
        kind="conditional_unmanaged",
        attributes=("allowed_actions_config.patterns_allowed",),
        summary=(
            "An explicit empty Actions patterns_allowed list is not reliably sent by the "
            "provider; Heckle does not try to enforce an empty list (issue #3458)."
        ),
        upstream="https://github.com/integrations/terraform-provider-github/issues/3458",
    ),
    ProviderQuirk(
        id="gitlab-default-branch-protection-takeover",
        provider="gitlabhq/gitlab",
        versions=frozenset({"19.3.0"}),
        resource_type="gitlab_branch_protection",
        kind="warning",
        summary=(
            "GitLab documents that creating management for an existing default branch can "
            "temporarily unprotect and re-protect it; Heckle therefore relies on state adoption "
            "and the final safety plan rather than recreating branch protection."
        ),
        upstream="https://gitlab.com/gitlab-org/terraform-provider-gitlab/-/raw/v19.3.0/docs/resources/branch_protection.md",
    ),
    ProviderQuirk(
        id="gitlab-branch-protection-empty-access-levels",
        provider="gitlabhq/gitlab",
        versions=frozenset({"19.3.0"}),
        resource_type="gitlab_branch_protection",
        kind="empty_string_unset",
        attributes=("merge_access_level", "push_access_level"),
        summary=(
            "Provider 19.3.0 validates the optional/computed legacy access-level strings. "
            "If hydration yields an empty string, Heckle treats it as unset while retaining "
            "valid access levels and the richer nested allowed_to_* ACLs."
        ),
        upstream="https://gitlab.com/gitlab-org/terraform-provider-gitlab/-/raw/v19.3.0/internal/provider/resource_gitlab_branch_protection.go",
    ),
    ProviderQuirk(
        id="gitlab-project-create-only-fields",
        provider="gitlabhq/gitlab",
        versions=frozenset({"19.3.0"}),
        resource_type="gitlab_project",
        kind="unmanaged_attributes",
        attributes=(
            "skip_wait_for_default_branch_protection",
            "permanently_delete_on_destroy",
            "archive_on_destroy",
            "initialize_with_readme",
            "import_url",
            "import_url_username",
            "import_url_password",
            "use_custom_template",
            "template_name",
            "template_project_id",
            "group_with_project_templates_id",
        ),
        summary=(
            "GitLab project creation/import/destroy controls are not faithfully importable "
            "remote project state and are excluded from adopted configuration."
        ),
        upstream="https://gitlab.com/gitlab-org/terraform-provider-gitlab/-/raw/v19.3.0/docs/resources/project.md",
    ),
    ProviderQuirk(
        id="gitlab-project-deprecated-public-builds",
        provider="gitlabhq/gitlab",
        versions=frozenset({"19.3.0"}),
        resource_type="gitlab_project",
        kind="unmanaged_attributes",
        attributes=("public_builds",),
        summary=(
            "The deprecated public_builds alias conflicts with the canonical public_jobs "
            "attribute. Heckle keeps only public_jobs when adopting existing projects."
        ),
        upstream="https://gitlab.com/gitlab-org/terraform-provider-gitlab/-/blob/v19.3.0/internal/provider/sdk/resource_gitlab_project.go",
    ),
    ProviderQuirk(
        id="gitlab-project-import-unavailable-fields",
        provider="gitlabhq/gitlab",
        versions=frozenset({"19.3.0"}),
        resource_type="gitlab_project",
        kind="unmanaged_attributes",
        attributes=("avatar", "avatar_hash", "branches"),
        summary=(
            "Project avatar inputs and provider-only branch bootstrap data cannot be "
            "faithfully reconstructed from an imported project and are left unmanaged."
        ),
        upstream="https://gitlab.com/gitlab-org/terraform-provider-gitlab/-/blob/v19.3.0/docs/resources/project.md",
    ),
    ProviderQuirk(
        id="gitlab-project-empty-validated-values",
        provider="gitlabhq/gitlab",
        versions=frozenset({"19.3.0"}),
        resource_type="gitlab_project",
        kind="empty_string_unset",
        attributes=(
            "visibility_level",
            "merge_method",
            "resource_group_default_process_mode",
            "squash_option",
            "pages_access_level",
            "ci_pipeline_variables_minimum_override_role",
            "analytics_access_level",
            "auto_cancel_pending_pipelines",
            "auto_devops_deploy_strategy",
            "build_git_strategy",
            "builds_access_level",
            "container_registry_access_level",
            "forking_access_level",
            "issues_access_level",
            "merge_requests_access_level",
            "repository_access_level",
            "requirements_access_level",
            "reviewer_assignment_strategy",
            "security_and_compliance_access_level",
            "snippets_access_level",
            "wiki_access_level",
            "releases_access_level",
            "environments_access_level",
            "feature_flags_access_level",
            "infrastructure_access_level",
            "monitor_access_level",
            "model_experiments_access_level",
            "model_registry_access_level",
            "package_registry_access_level",
        ),
        summary=(
            "Provider 19.3.0 validates these optional/computed project strings against "
            "non-empty enumerations. If GitLab/provider hydration represents an unset value "
            "as an empty string, Heckle omits only that empty value while preserving every "
            "valid configured value."
        ),
        upstream="https://gitlab.com/gitlab-org/terraform-provider-gitlab/-/raw/v19.3.0/internal/provider/sdk/resource_gitlab_project.go",
    ),
    ProviderQuirk(
        id="gitlab-project-container-expiration-empty-cadence",
        provider="gitlabhq/gitlab",
        versions=frozenset({"19.3.0"}),
        resource_type="gitlab_project",
        kind="conditional_normalization",
        attributes=("container_expiration_policy.cadence",),
        summary=(
            "Provider 19.3.0 validates container_expiration_policy.cadence against a "
            "non-empty enum while its read flattener copies the API value directly. "
            "Heckle omits an empty cadence and preserves valid cadence values."
        ),
        upstream="https://gitlab.com/gitlab-org/terraform-provider-gitlab/-/raw/v19.3.0/internal/provider/sdk/resource_gitlab_project.go",
    ),
    ProviderQuirk(
        id="gitlab-membership-empty-expiry",
        provider="gitlabhq/gitlab",
        versions=frozenset({"19.3.0"}),
        resource_type="gitlab_project_membership",
        kind="empty_string_unset",
        attributes=("expires_at",),
        summary=(
            "GitLab can represent a membership with no expiry as an empty value during "
            "provider hydration; provider 19.3.0 validates configured expires_at values as "
            "YYYY-MM-DD, so Heckle treats an empty value as unset."
        ),
        upstream="https://docs.gitlab.com/api/project_members/",
    ),
    ProviderQuirk(
        id="gitlab-group-membership-empty-expiry",
        provider="gitlabhq/gitlab",
        versions=frozenset({"19.3.0"}),
        resource_type="gitlab_group_membership",
        kind="empty_string_unset",
        attributes=("expires_at",),
        summary=(
            "GitLab can represent a membership with no expiry as an empty value during "
            "provider hydration; provider 19.3.0 expects configured expires_at values in "
            "YYYY-MM-DD form, so Heckle treats an empty value as unset."
        ),
        upstream="https://docs.gitlab.com/api/group_members/",
    ),
    ProviderQuirk(
        id="gitlab-project-fork-only-fields",
        provider="gitlabhq/gitlab",
        versions=frozenset({"19.3.0"}),
        resource_type="gitlab_project",
        kind="unmanaged_attributes",
        attributes=("forked_from_project_id", "mr_default_target_self"),
        summary=(
            "GitLab mr_default_target_self is only valid for forked projects and the provider "
            "requires forked_from_project_id alongside it. Heckle does not reconstruct fork "
            "creation relationships during adoption, so both fields are left unmanaged."
        ),
        upstream="https://registry.terraform.io/providers/gitlabhq/gitlab/19.3.0/docs/resources/project",
    ),
    ProviderQuirk(
        id="gitlab-group-import-unavailable-avatar",
        provider="gitlabhq/gitlab",
        versions=frozenset({"19.3.0"}),
        resource_type="gitlab_group",
        kind="unmanaged_attributes",
        attributes=("avatar", "avatar_hash"),
        summary=(
            "Group avatar inputs are local-file/provider helper values and are not "
            "available for faithful reconstruction from imported groups."
        ),
        upstream="https://gitlab.com/gitlab-org/terraform-provider-gitlab/-/blob/v19.3.0/docs/resources/group.md",
    ),
    ProviderQuirk(
        id="gitlab-group-empty-validated-values",
        provider="gitlabhq/gitlab",
        versions=frozenset({"19.3.0"}),
        resource_type="gitlab_group",
        kind="empty_string_unset",
        attributes=(
            "visibility_level",
            "project_creation_level",
            "subgroup_creation_level",
            "wiki_access_level",
            "shared_runners_setting",
        ),
        summary=(
            "Provider 19.3.0 validates these optional/computed group strings against "
            "non-empty enumerations. Empty hydrated values mean unset and are omitted; "
            "valid configured values remain managed."
        ),
        upstream="https://gitlab.com/gitlab-org/terraform-provider-gitlab/-/raw/v19.3.0/internal/provider/sdk/resource_gitlab_group.go",
    ),
    ProviderQuirk(
        id="gitlab-group-destroy-control-fields",
        provider="gitlabhq/gitlab",
        versions=frozenset({"19.3.0"}),
        resource_type="gitlab_group",
        kind="unmanaged_attributes",
        attributes=("archive_on_destroy", "permanently_remove_on_delete"),
        summary=(
            "GitLab group destroy-control fields are local Terraform lifecycle behaviour, not "
            "settings discovered from an existing group."
        ),
        upstream="https://gitlab.com/gitlab-org/terraform-provider-gitlab/-/raw/v19.3.0/docs/resources/group.md",
    ),
    ProviderQuirk(
        id="gitlab-group-membership-destroy-controls",
        provider="gitlabhq/gitlab",
        versions=frozenset({"19.3.0"}),
        resource_type="gitlab_group_membership",
        kind="unmanaged_attributes",
        attributes=("skip_subresources_on_destroy", "unassign_issuables_on_destroy"),
        summary=(
            "GitLab group-membership destroy options affect only how Terraform removes a "
            "membership; they are not remote membership state and are left unmanaged."
        ),
        upstream="https://gitlab.com/gitlab-org/terraform-provider-gitlab/-/raw/v19.3.0/internal/provider/resource_gitlab_group_membership.go",
    ),
    ProviderQuirk(
        id="gitlab-project-membership-custom-role-update",
        provider="gitlabhq/gitlab",
        versions=frozenset({"19.3.0"}),
        resource_type="gitlab_project_membership",
        kind="warning",
        attributes=("member_role_id",),
        summary=(
            "Provider issue #6619 documents unreliable transitions from a custom "
            "member_role_id back to a normal project role. Heckle preserves imported "
            "custom-role state but flags the provider limitation for later edits."
        ),
        upstream="https://gitlab.com/gitlab-org/terraform-provider-gitlab/-/issues/6619",
    ),
    ProviderQuirk(
        id="gitlab-group-membership-custom-role-update",
        provider="gitlabhq/gitlab",
        versions=frozenset({"19.3.0"}),
        resource_type="gitlab_group_membership",
        kind="warning",
        attributes=("member_role_id",),
        summary=(
            "Provider issue #6619 documents unreliable transitions from a custom "
            "member_role_id back to a normal group role. Heckle preserves imported "
            "custom-role state but flags the provider limitation for later edits."
        ),
        upstream="https://gitlab.com/gitlab-org/terraform-provider-gitlab/-/issues/6619",
    ),
    ProviderQuirk(
        id="gitlab-group-push-rules-zero-values",
        provider="gitlabhq/gitlab",
        versions=frozenset({"19.3.0"}),
        resource_type="gitlab_group",
        kind="unmanaged_attributes",
        attributes=("push_rules",),
        summary=(
            "Group push_rules are left unmanaged because the provider cannot reliably "
            "distinguish explicit false/zero/empty values from omitted values (issue #6832)."
        ),
        upstream="https://gitlab.com/gitlab-org/terraform-provider-gitlab/-/work_items/6832",
    ),
    ProviderQuirk(
        id="gitlab-environment-empty-optional-values",
        provider="gitlabhq/gitlab",
        versions=frozenset({"19.3.0"}),
        resource_type="gitlab_project_environment",
        kind="empty_string_unset",
        attributes=(
            "external_url", "tier", "kubernetes_namespace", "flux_resource_path",
            "auto_stop_setting",
        ),
        summary=(
            "The environment read path writes empty strings for several optional fields, "
            "while provider validators reject or add dependencies for configured empty strings; "
            "Heckle treats those empty values as unset."
        ),
        upstream="https://gitlab.com/gitlab-org/terraform-provider-gitlab/-/raw/v19.3.0/internal/provider/resource_gitlab_project_environment.go",
    ),
    ProviderQuirk(
        id="gitlab-environment-cluster-field-dependencies",
        provider="gitlabhq/gitlab",
        versions=frozenset({"19.3.0"}),
        resource_type="gitlab_project_environment",
        kind="conditional_normalization",
        attributes=("cluster_agent_id", "kubernetes_namespace", "flux_resource_path"),
        summary=(
            "Provider 19.3.0 requires kubernetes_namespace to be accompanied by "
            "cluster_agent_id, and flux_resource_path to be accompanied by both. "
            "Heckle omits dependent values whenever their required parent values are "
            "absent after hydration normalization."
        ),
        upstream="https://gitlab.com/gitlab-org/terraform-provider-gitlab/-/raw/v19.3.0/internal/provider/resource_gitlab_project_environment.go",
    ),
    ProviderQuirk(
        id="gitlab-environment-destroy-control",
        provider="gitlabhq/gitlab",
        versions=frozenset({"19.3.0"}),
        resource_type="gitlab_project_environment",
        kind="unmanaged_attributes",
        attributes=("stop_before_destroy",),
        summary=(
            "stop_before_destroy controls Terraform deletion behaviour rather than remote "
            "environment state and is therefore left unmanaged during adoption."
        ),
        upstream="https://gitlab.com/gitlab-org/terraform-provider-gitlab/-/raw/v19.3.0/internal/provider/resource_gitlab_project_environment.go",
    ),
    ProviderQuirk(
        id="gitlab-hook-empty-branch-filter-strategy",
        provider="gitlabhq/gitlab",
        versions=frozenset({"19.3.0"}),
        resource_type="gitlab_project_hook",
        kind="empty_string_unset",
        attributes=("branch_filter_strategy",),
        summary=(
            "GitLab hooks can return an empty branch_filter_strategy, while provider 19.3.0 "
            "only accepts wildcard, regex, or all_branches when the field is configured."
        ),
        upstream="https://gitlab.com/gitlab-org/terraform-provider-gitlab/-/raw/v19.3.0/internal/provider/resource_gitlab_project_hook.go",
    ),
    ProviderQuirk(
        id="gitlab-group-hook-empty-branch-filter-strategy",
        provider="gitlabhq/gitlab",
        versions=frozenset({"19.3.0"}),
        resource_type="gitlab_group_hook",
        kind="empty_string_unset",
        attributes=("branch_filter_strategy",),
        summary=(
            "GitLab group hooks can return an empty branch_filter_strategy, while provider "
            "19.3.0 only accepts wildcard, regex, or all_branches when configured."
        ),
        upstream="https://gitlab.com/gitlab-org/terraform-provider-gitlab/-/raw/v19.3.0/internal/provider/resource_gitlab_group_hook.go",
    ),
    ProviderQuirk(
        id="gitlab-project-hook-all-branches-filter",
        provider="gitlabhq/gitlab",
        versions=frozenset({"19.3.0"}),
        resource_type="gitlab_project_hook",
        kind="conditional_normalization",
        attributes=("branch_filter_strategy", "push_events_branch_filter"),
        summary=(
            "When branch_filter_strategy is all_branches, GitLab normalizes the branch "
            "filter to an empty string. Heckle omits push_events_branch_filter for that "
            "strategy to avoid provider inconsistent-result behaviour (issue #6574)."
        ),
        upstream="https://gitlab.com/gitlab-org/terraform-provider-gitlab/-/issues/6574",
    ),
    ProviderQuirk(
        id="gitlab-group-hook-all-branches-filter",
        provider="gitlabhq/gitlab",
        versions=frozenset({"19.3.0"}),
        resource_type="gitlab_group_hook",
        kind="conditional_normalization",
        attributes=("branch_filter_strategy", "push_events_branch_filter"),
        summary=(
            "When branch_filter_strategy is all_branches, GitLab normalizes the branch "
            "filter to an empty string. Heckle omits push_events_branch_filter for that "
            "strategy to avoid provider inconsistent-result behaviour (issue #6574)."
        ),
        upstream="https://gitlab.com/gitlab-org/terraform-provider-gitlab/-/issues/6574",
    ),
    ProviderQuirk(
        id="gitlab-approval-rule-import-controls",
        provider="gitlabhq/gitlab",
        versions=frozenset({"19.3.0"}),
        resource_type="gitlab_project_approval_rule",
        kind="unmanaged_attributes",
        attributes=("disable_importing_default_any_approver_rule_on_create",),
        summary=(
            "The default-any-approver import switch controls provider create behaviour, not "
            "remote approval-rule state, and is left unmanaged."
        ),
        upstream="https://gitlab.com/gitlab-org/terraform-provider-gitlab/-/raw/v19.3.0/internal/provider/sdk/resource_gitlab_project_approval_rule.go",
    ),
    ProviderQuirk(
        id="gitlab-approval-rule-any-approver",
        provider="gitlabhq/gitlab",
        versions=frozenset({"19.3.0"}),
        resource_type="gitlab_project_approval_rule",
        kind="warning",
        attributes=("rule_type", "report_type"),
        summary=(
            "Provider 19.3.0 makes report_type RequiredWith rule_type even though "
            "report_type is only valid for report_approver. Existing any_approver rules "
            "therefore cannot be faithfully emitted as ordinary adopted HCL and are kept "
            "inventory-only."
        ),
        upstream="https://gitlab.com/gitlab-org/terraform-provider-gitlab/-/raw/v19.3.0/internal/provider/sdk/resource_gitlab_project_approval_rule.go",
    ),
    ProviderQuirk(
        id="gitlab-approval-report-approver-name",
        provider="gitlabhq/gitlab",
        versions=frozenset({"19.3.0"}),
        resource_type="gitlab_project_approval_rule",
        kind="warning",
        attributes=("rule_type", "report_type", "name"),
        summary=(
            "Provider 19.3.0 only recreates report_approver rules when their name is "
            "Coverage-Check. Other imported report-approver rules are inventory-only so "
            "Heckle does not generate configuration the provider cannot recreate."
        ),
        upstream="https://gitlab.com/gitlab-org/terraform-provider-gitlab/-/raw/v19.3.0/internal/provider/sdk/resource_gitlab_project_approval_rule.go",
    ),
    ProviderQuirk(
        id="gitlab-approval-rule-type-coupling",
        provider="gitlabhq/gitlab",
        versions=frozenset({"19.3.0"}),
        resource_type="gitlab_project_approval_rule",
        kind="conditional_normalization",
        attributes=("rule_type", "report_type"),
        summary=(
            "Provider 19.3.0 couples rule_type and report_type during validation. "
            "For ordinary imported rules Heckle omits both and lets imported state "
            "represent the rule type; report-approver rules retain both values."
        ),
        upstream="https://gitlab.com/gitlab-org/terraform-provider-gitlab/-/raw/v19.3.0/internal/provider/sdk/resource_gitlab_project_approval_rule.go",
    ),
    ProviderQuirk(
        id="gitlab-approval-rule-protected-branch-conflict",
        provider="gitlabhq/gitlab",
        versions=frozenset({"19.3.0"}),
        resource_type="gitlab_project_approval_rule",
        kind="conditional_normalization",
        attributes=("applies_to_all_protected_branches", "protected_branch_ids"),
        summary=(
            "When an approval rule applies to all protected branches, provider 19.3.0 "
            "declares an explicit protected_branch_ids list conflicting; Heckle omits it."
        ),
        upstream="https://gitlab.com/gitlab-org/terraform-provider-gitlab/-/raw/v19.3.0/internal/provider/sdk/resource_gitlab_project_approval_rule.go",
    ),
    ProviderQuirk(
        id="gitlab-approval-rule-empty-report-type",
        provider="gitlabhq/gitlab",
        versions=frozenset({"19.3.0"}),
        resource_type="gitlab_project_approval_rule",
        kind="empty_string_unset",
        attributes=("report_type",),
        summary=(
            "Normal approval rules can hydrate report_type as an empty string, but provider "
            "19.3.0 validates a configured value as code_coverage only; empty means unset."
        ),
        upstream="https://gitlab.com/gitlab-org/terraform-provider-gitlab/-/raw/v19.3.0/internal/provider/sdk/resource_gitlab_project_approval_rule.go",
    ),
    ProviderQuirk(
        id="gitlab-deploy-key-empty-expiry",
        provider="gitlabhq/gitlab",
        versions=frozenset({"19.3.0"}),
        resource_type="gitlab_deploy_key",
        kind="empty_string_unset",
        attributes=("expires_at",),
        summary=(
            "A deploy key with no expiry must omit expires_at; provider 19.3.0 validates any "
            "configured value as RFC3339, so an empty hydrated value is treated as unset."
        ),
        upstream="https://gitlab.com/gitlab-org/terraform-provider-gitlab/-/raw/v19.3.0/internal/provider/sdk/resource_gitlab_deploy_key.go",
    ),
    ProviderQuirk(
        id="gitlab-tag-protection-takeover",
        provider="gitlabhq/gitlab",
        versions=frozenset({"19.3.0"}),
        resource_type="gitlab_tag_protection",
        kind="warning",
        summary=(
            "GitLab documents that creating management for an existing protected tag may "
            "unprotect and re-protect it; Heckle therefore adopts existing protections via state."
        ),
        upstream="https://gitlab.com/gitlab-org/terraform-provider-gitlab/-/raw/v19.3.0/docs/resources/tag_protection.md",
    ),
    ProviderQuirk(
        id="gitlab-tag-protection-selector-normalization",
        provider="gitlabhq/gitlab",
        versions=frozenset({"19.3.0"}),
        resource_type="gitlab_tag_protection",
        kind="conditional_normalization",
        attributes=(
            "allowed_to_create.access_level",
            "allowed_to_create.user_id",
            "allowed_to_create.group_id",
            "allowed_to_create.deploy_key_id",
        ),
        summary=(
            "The provider schema requires each allowed_to_create entry to select an access "
            "level or an explicit user/group/deploy key, not both. Heckle prefers an explicit "
            "identity when provider hydration also supplies access_level, and treats an empty "
            "access_level as unset."
        ),
        upstream="https://gitlab.com/gitlab-org/terraform-provider-gitlab/-/raw/v19.3.0/docs/resources/tag_protection.md",
    ),
    ProviderQuirk(
        id="gitlab-skipped-pipeline-parent-disabled",
        provider="gitlabhq/gitlab",
        versions=frozenset({"19.3.0"}),
        resource_type="gitlab_project",
        kind="known_provider_behaviour",
        attributes=("allow_merge_on_skipped_pipeline",),
        guard=("only_allow_merge_if_pipeline_succeeds", False),
        summary="allow_merge_on_skipped_pipeline is only relevant when successful pipelines are required.",
        upstream="https://gitlab.com/gitlab-org/terraform-provider-gitlab/-/raw/v19.3.0/docs/resources/project.md",
    ),
    ProviderQuirk(
        id="gitlab-group-skipped-pipeline-parent-disabled",
        provider="gitlabhq/gitlab",
        versions=frozenset({"19.3.0"}),
        resource_type="gitlab_group",
        kind="known_provider_behaviour",
        attributes=("allow_merge_on_skipped_pipeline",),
        guard=("only_allow_merge_if_pipeline_succeeds", False),
        summary="allow_merge_on_skipped_pipeline is only relevant when successful pipelines are required.",
        upstream="https://gitlab.com/gitlab-org/terraform-provider-gitlab/-/raw/v19.3.0/docs/resources/group.md",
    ),
    ProviderQuirk(
        id="gitlab-merge-trains-parent-disabled",
        provider="gitlabhq/gitlab",
        versions=frozenset({"19.3.0"}),
        resource_type="gitlab_project",
        kind="known_provider_behaviour",
        attributes=("merge_trains_enabled", "merge_trains_skip_train_allowed"),
        guard=("merge_pipelines_enabled", False),
        summary="Merge-train settings are only relevant while merged-results pipelines are enabled.",
        upstream="https://gitlab.com/gitlab-org/terraform-provider-gitlab/-/raw/v19.3.0/docs/resources/project.md",
    ),
    ProviderQuirk(
        id="forgejo-pull-request-parent-disabled",
        provider="svalabs/forgejo",
        versions=frozenset({"1.6.0"}),
        resource_type="forgejo_repository",
        kind="known_provider_behaviour",
        attributes=(
            "ignore_whitespace_conflicts", "allow_merge_commits", "allow_rebase",
            "allow_rebase_explicit", "allow_squash_merge", "default_merge_style",
            "allow_manual_merge", "autodetect_manual_merge",
            "default_delete_branch_after_merge", "allow_fast_forward_only_merge",
            "allow_rebase_update", "default_allow_maintainer_edit", "default_update_style",
        ),
        guard=("has_pull_requests", False),
        summary="Pull-request subordinate settings can round-trip to provider defaults while pull requests are disabled.",
        upstream="https://github.com/svalabs/terraform-provider-forgejo/blob/v1.6.0/internal/provider/repository_resource.go",
    ),
    ProviderQuirk(
        id="forgejo-issue-parent-disabled",
        provider="svalabs/forgejo",
        versions=frozenset({"1.6.0"}),
        resource_type="forgejo_repository",
        kind="known_provider_behaviour",
        attributes=("internal_tracker", "external_tracker"),
        guard=("has_issues", False),
        summary="Issue-tracker subordinate settings are only relevant while issues are enabled.",
        upstream="https://github.com/svalabs/terraform-provider-forgejo/blob/v1.6.0/internal/provider/repository_resource.go",
    ),
    ProviderQuirk(
        id="forgejo-wiki-parent-disabled",
        provider="svalabs/forgejo",
        versions=frozenset({"1.6.0"}),
        resource_type="forgejo_repository",
        kind="known_provider_behaviour",
        attributes=("globally_editable_wiki", "external_wiki", "wiki_branch"),
        guard=("has_wiki", False),
        summary="Wiki subordinate settings are only relevant while the wiki is enabled.",
        upstream="https://github.com/svalabs/terraform-provider-forgejo/blob/v1.6.0/internal/provider/repository_resource.go",
    ),
    ProviderQuirk(
        id="forgejo-mirror-parent-disabled",
        provider="svalabs/forgejo",
        versions=frozenset({"1.6.0"}),
        resource_type="forgejo_repository",
        kind="known_provider_behaviour",
        attributes=("enable_prune", "mirror_interval"),
        guard=("mirror", False),
        summary="Mirror subordinate settings are only relevant while mirroring is enabled.",
        upstream="https://github.com/svalabs/terraform-provider-forgejo/blob/v1.6.0/internal/provider/repository_resource.go",
    ),
)


def quirks_for(spec: ProviderSpec, resource_type: str | None = None) -> tuple[ProviderQuirk, ...]:
    return tuple(
        quirk
        for quirk in QUIRKS
        if quirk.applies(spec) and (resource_type is None or quirk.resource_type == resource_type)
    )


def unmanaged_attributes(spec: ProviderSpec, resource_type: str) -> set[str]:
    output: set[str] = set()
    for quirk in quirks_for(spec, resource_type):
        if quirk.kind == "unmanaged_attributes":
            output.update(quirk.attributes)
    return output



def empty_string_unset_attributes(spec: ProviderSpec, resource_type: str) -> set[str]:
    output: set[str] = set()
    for quirk in quirks_for(spec, resource_type):
        if quirk.kind == "empty_string_unset":
            output.update(quirk.attributes)
    return output

def audited(spec: ProviderSpec) -> bool:
    return spec.version in AUDITED_VERSIONS.get(spec.source, frozenset())


def audited_resource_types(spec: ProviderSpec) -> frozenset[str]:
    return AUDITED_RESOURCE_TYPES.get((spec.source, spec.version), frozenset())


def compatibility_summary(spec: ProviderSpec) -> dict[str, object]:
    return {
        "provider": spec.source,
        "version": spec.version,
        "audited": audited(spec),
        "audited_resource_types": sorted(audited_resource_types(spec)),
        "quirks": [
            {
                "id": quirk.id,
                "resource_type": quirk.resource_type,
                "kind": quirk.kind,
                "attributes": list(quirk.attributes),
                "summary": quirk.summary,
                "upstream": quirk.upstream,
                "guard": dict([quirk.guard]) if quirk.guard else {},
            }
            for quirk in quirks_for(spec)
        ],
    }


def compatibility_warnings(spec: ProviderSpec) -> Iterable[str]:
    for quirk in quirks_for(spec):
        yield quirk.warning()



def known_provider_behaviour_fields(spec: ProviderSpec, resource_type: str) -> dict[str, frozenset[str]]:
    grouped: dict[str, set[str]] = {}
    for quirk in quirks_for(spec, resource_type):
        if quirk.kind != "known_provider_behaviour" or quirk.guard is None:
            continue
        controller, expected = quirk.guard
        if expected is not False:
            continue
        grouped.setdefault(controller, set()).update(quirk.attributes)
    return {name: frozenset(values) for name, values in grouped.items()}


def known_provider_behaviour_for(spec: ProviderSpec, change: UnsafeChange) -> KnownProviderBehaviour | None:
    """Recognise version-scoped provider behaviour declared in the compatibility registry."""
    if change.actions != ("update",) or not isinstance(change.before, dict) or not isinstance(change.after, dict):
        return None
    resource_type = next((q.resource_type for q in quirks_for(spec) if f".{q.resource_type}." in change.address), None)
    if resource_type is None:
        return None
    changed_top = {
        path.split(".", 1)[0].split("[", 1)[0]
        for path in change.attributes
        if path and path != "<root>"
    }
    if not changed_top:
        return None
    permitted: set[str] = set()
    guards: list[tuple[str, object]] = []
    reasons: list[str] = []
    for quirk in quirks_for(spec, resource_type):
        if quirk.kind != "known_provider_behaviour" or quirk.guard is None:
            continue
        controller, expected = quirk.guard
        if change.before.get(controller) == expected and change.after.get(controller) == expected:
            overlap = changed_top & set(quirk.attributes)
            if overlap:
                permitted.update(quirk.attributes)
                guards.append(quirk.guard)
                reasons.append(quirk.id)
    if changed_top <= permitted and guards:
        return KnownProviderBehaviour(
            reason="known provider behaviour: " + ", ".join(sorted(set(reasons))),
            guards=tuple(sorted(set(guards))),
        )
    return None

def format_compatibility(specs: Iterable[ProviderSpec]) -> str:
    lines: list[str] = []
    for spec in specs:
        if lines:
            lines.append("")
        state = "audited" if audited(spec) else "UNTESTED"
        lines.append(f"{spec.forge}: {spec.source} {spec.version} ({state})")
        provider_quirks = quirks_for(spec)
        if not provider_quirks:
            lines.append("  no version-scoped quirks recorded")
            continue
        for quirk in provider_quirks:
            lines.append(f"  {quirk.id}: {quirk.summary}")
            lines.append(f"    {quirk.upstream}")
    return "\n".join(lines)
