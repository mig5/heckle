from __future__ import annotations

from collections import OrderedDict

import pytest

from fixtures import connection
from heckle.compatibility import (
    audited,
    audited_resource_types,
    compatibility_summary,
    provider_spec,
    unmanaged_attributes,
)
from heckle.errors import GenerationError, UnsafeChange
from heckle.hcl.types import Body, NestedBlock, Resource
from heckle.pipeline import Options, Pipeline
from heckle.providers.gitlab.adapter import GitLabProvider
from heckle.providers.gitea.adapter import GiteaProvider
from heckle.providers.forgejo.adapter import ForgejoProvider
from heckle.providers.github.model.builder import DomainModelBuilder
from heckle.providers.github.model.types import ImportRecord


def test_provider_pins_are_explicitly_audited():
    for forge in ("github", "gitlab", "gitea", "forgejo"):
        spec = provider_spec(forge)
        assert audited(spec)
        assert compatibility_summary(spec)["audited"] is True




def test_gitlab_audited_resource_scope_matches_managed_adapter():
    spec = provider_spec("gitlab")
    managed = {
        "gitlab_group" if kind == "group" else
        "gitlab_project" if kind == "project" else
        f"gitlab_{kind}"
        for kind in GitLabProvider.HANDLERS
    }
    assert audited_resource_types(spec) == managed
    assert compatibility_summary(spec)["audited_resource_types"] == sorted(managed)


@pytest.mark.parametrize("forge,provider", [
    ("gitea", GiteaProvider), ("forgejo", ForgejoProvider),
])
def test_native_audited_resource_scope_matches_managed_adapter(forge, provider):
    spec = provider_spec(forge)
    managed = provider.MANAGED_RESOURCE_TYPES
    assert audited_resource_types(spec) == managed


def test_github_audit_scope_covers_every_planned_resource_family():
    spec = provider_spec("github")
    # The source-level 6.13.0 audit covers every family emitted by the native
    # builder, including conditional families absent from small fixtures.
    required = {
        "github_repository", "github_repository_pages", "github_repository_ruleset",
        "github_organization_settings", "github_organization_ruleset", "github_team",
        "github_actions_repository_permissions", "github_repository_environment",
        "github_repository_webhook", "github_actions_secret",
    }
    assert required <= audited_resource_types(spec)


def test_gitea_create_destroy_and_parent_controlled_fields_are_recorded():
    spec = provider_spec("gitea")
    assert "repo_admin_change_team_access" in unmanaged_attributes(spec, "gitea_org")
    assert "archive_on_destroy" in unmanaged_attributes(spec, "gitea_repository")
    assert any(q.id == "gitea-team-all-repositories" for q in __import__("heckle.compatibility", fromlist=["quirks_for"]).quirks_for(spec, "gitea_team"))

def test_unaudited_provider_override_requires_explicit_opt_in(tmp_path):
    with pytest.raises(GenerationError, match="has not been audited"):
        Pipeline(
            connection("github"),
            Options(output=tmp_path / "out", provider_version="99.0.0"),
        )
    pipeline = Pipeline(
        connection("github"),
        Options(
            output=tmp_path / "out",
            provider_version="99.0.0",
            allow_untested_provider=True,
        ),
    )
    assert pipeline.provider.spec.version == "99.0.0"


def _github_inventory(root):
    (root / "repositories.json").write_text("[]")
    (root / "teams.json").write_text("[]")


def test_github_coupled_repository_creation_flags_are_unmanaged(tmp_path):
    _github_inventory(tmp_path)
    body = Body(
        OrderedDict(
            billing_email='"ops@example.test"',
            members_can_create_repositories="true",
            members_can_create_public_repositories="false",
            members_can_create_private_repositories="true",
            members_can_create_internal_repositories="false",
        )
    )
    resource = Resource(
        "github_organization_settings",
        "example",
        body,
        None,
        tmp_path / "generated.tf",
    )
    record = ImportRecord(resource.address, "1")
    model = DomainModelBuilder("example", tmp_path).build([resource], [record])
    settings = model.organization["settings"]
    assert "billing_email" in settings
    for name in unmanaged_attributes(provider_spec("github"), "github_organization_settings"):
        assert name not in settings
    assert any("repository-creation booleans" in note for note in model.report["compatibility_notes"])


def test_github_empty_actions_patterns_are_not_enforced(tmp_path):
    _github_inventory(tmp_path)
    body = Body(
        OrderedDict(enabled_repositories='"all"', allowed_actions='"selected"'),
        [
            NestedBlock(
                "allowed_actions_config",
                (),
                Body(
                    OrderedDict(
                        github_owned_allowed="false",
                        patterns_allowed="[]",
                        verified_allowed="false",
                    )
                ),
                "",
            )
        ],
    )
    resource = Resource(
        "github_actions_organization_permissions",
        "example",
        body,
        None,
        tmp_path / "generated.tf",
    )
    record = ImportRecord(resource.address, "example")
    model = DomainModelBuilder("example", tmp_path).build([resource], [record])
    config = model.organization["actions_permissions"]
    block = config["__blocks"]["allowed_actions_config"][0]
    assert "patterns_allowed" not in block
    assert any("patterns_allowed" in note for note in model.report["compatibility_notes"])


def test_gitlab_create_and_destroy_control_fields_are_unmanaged():
    spec = provider_spec("gitlab")
    assert {
        "skip_wait_for_default_branch_protection",
        "permanently_delete_on_destroy",
        "archive_on_destroy",
        "public_builds",
        "forked_from_project_id",
        "mr_default_target_self",
    } <= unmanaged_attributes(spec, "gitlab_project")
    assert {"archive_on_destroy", "permanently_remove_on_delete"} <= unmanaged_attributes(spec, "gitlab_group")


def test_gitlab_known_pipeline_behaviour_is_recognised_under_guard():
    provider = GitLabProvider()
    change = UnsafeChange(
        'module.gitlab_project.gitlab_project.this["example/project"]',
        ("update",),
        ("allow_merge_on_skipped_pipeline",),
        before={
            "only_allow_merge_if_pipeline_succeeds": False,
            "allow_merge_on_skipped_pipeline": False,
        },
        after={
            "only_allow_merge_if_pipeline_succeeds": False,
            "allow_merge_on_skipped_pipeline": True,
        },
    )
    behaviour = provider.known_provider_behaviour(change)
    assert behaviour is not None
    assert behaviour.guards == (("only_allow_merge_if_pipeline_succeeds", False),)


def test_gitlab_merge_train_behaviour_is_not_recognised_when_guard_changes():
    provider = GitLabProvider()
    change = UnsafeChange(
        'module.gitlab_project.gitlab_project.this["example/project"]',
        ("update",),
        ("merge_trains_enabled", "merge_pipelines_enabled"),
        before={"merge_pipelines_enabled": False, "merge_trains_enabled": False},
        after={"merge_pipelines_enabled": True, "merge_trains_enabled": True},
    )
    assert provider.known_provider_behaviour(change) is None


def test_gitlab_empty_reviewer_strategy_is_version_scoped_compatibility():
    from heckle.compatibility import empty_string_unset_attributes, provider_spec
    assert "reviewer_assignment_strategy" in empty_string_unset_attributes(
        provider_spec("gitlab"), "gitlab_project"
    )


def test_gitlab_membership_empty_expiry_is_version_scoped_compatibility():
    from heckle.compatibility import empty_string_unset_attributes, provider_spec
    spec = provider_spec("gitlab")
    assert "expires_at" in empty_string_unset_attributes(spec, "gitlab_project_membership")
    assert "expires_at" in empty_string_unset_attributes(spec, "gitlab_group_membership")


def test_gitlab_systematic_audit_rules_are_version_scoped():
    from heckle.compatibility import empty_string_unset_attributes, provider_spec, unmanaged_attributes, quirks_for
    spec = provider_spec("gitlab")
    assert {"skip_subresources_on_destroy", "unassign_issuables_on_destroy"} <= unmanaged_attributes(spec, "gitlab_group_membership")
    assert "push_rules" in unmanaged_attributes(spec, "gitlab_group")
    assert "stop_before_destroy" in unmanaged_attributes(spec, "gitlab_project_environment")
    assert {"external_url", "tier", "kubernetes_namespace", "flux_resource_path", "auto_stop_setting"} <= empty_string_unset_attributes(spec, "gitlab_project_environment")
    environment_quirks = {q.id for q in quirks_for(spec, "gitlab_project_environment")}
    assert "gitlab-environment-cluster-field-dependencies" in environment_quirks
    assert "branch_filter_strategy" in empty_string_unset_attributes(spec, "gitlab_project_hook")
    assert "branch_filter_strategy" in empty_string_unset_attributes(spec, "gitlab_group_hook")
    assert "report_type" in empty_string_unset_attributes(spec, "gitlab_project_approval_rule")
    assert "disable_importing_default_any_approver_rule_on_create" in unmanaged_attributes(spec, "gitlab_project_approval_rule")
    assert {
        "visibility_level", "merge_method", "resource_group_default_process_mode",
        "squash_option", "pages_access_level", "ci_pipeline_variables_minimum_override_role",
        "analytics_access_level", "auto_cancel_pending_pipelines",
        "auto_devops_deploy_strategy", "build_git_strategy", "builds_access_level",
        "container_registry_access_level", "forking_access_level", "issues_access_level",
        "merge_requests_access_level", "repository_access_level", "requirements_access_level",
        "reviewer_assignment_strategy", "security_and_compliance_access_level",
        "snippets_access_level", "wiki_access_level", "releases_access_level",
        "environments_access_level", "feature_flags_access_level",
        "infrastructure_access_level", "monitor_access_level",
        "model_experiments_access_level", "model_registry_access_level",
        "package_registry_access_level",
    } <= empty_string_unset_attributes(spec, "gitlab_project")
    assert {
        "visibility_level", "project_creation_level", "subgroup_creation_level",
        "wiki_access_level", "shared_runners_setting",
    } <= empty_string_unset_attributes(spec, "gitlab_group")
    assert {"merge_access_level", "push_access_level"} <= empty_string_unset_attributes(spec, "gitlab_branch_protection")
    hook_quirks = {q.id for rtype in ("gitlab_project_hook", "gitlab_group_hook") for q in quirks_for(spec, rtype)}
    assert "gitlab-project-hook-all-branches-filter" in hook_quirks
    assert "gitlab-group-hook-all-branches-filter" in hook_quirks
    membership_warnings = {
        q.id for rtype in ("gitlab_project_membership", "gitlab_group_membership")
        for q in quirks_for(spec, rtype) if q.kind == "warning"
    }
    assert {
        "gitlab-project-membership-custom-role-update",
        "gitlab-group-membership-custom-role-update",
    } <= membership_warnings
    approval_quirks = {q.id for q in quirks_for(spec, "gitlab_project_approval_rule")}
    assert "gitlab-approval-rule-any-approver" in approval_quirks
    assert "gitlab-approval-report-approver-name" in approval_quirks
    project_quirks = {q.id for q in quirks_for(spec, "gitlab_project")}
    assert "gitlab-project-container-expiration-empty-cadence" in project_quirks
    tag_quirks = {q.id for q in quirks_for(spec, "gitlab_tag_protection")}
    assert "gitlab-tag-protection-selector-normalization" in tag_quirks
    assert "expires_at" in empty_string_unset_attributes(spec, "gitlab_deploy_key")
    assert {
        "initialize_with_readme", "import_url", "import_url_username", "import_url_password",
        "use_custom_template", "template_name", "template_project_id",
        "group_with_project_templates_id", "avatar", "avatar_hash", "branches",
    } <= unmanaged_attributes(spec, "gitlab_project")
    assert {"avatar", "avatar_hash"} <= unmanaged_attributes(spec, "gitlab_group")
