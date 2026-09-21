from collections import OrderedDict
from pathlib import Path
import pytest

from heckle.core.compilation import Family
from heckle.core.model import Entity
from heckle.core.registry import backend
from heckle.hcl.schema import ProviderSchema
from heckle.hcl.types import Body
from heckle.hcl.render import hcl_string, literal_string, literal_list, render_expr
from heckle.errors import GenerationError
from fixtures import model_for, FakeRunner


def test_forgejo_unsupported_objects_are_not_created(tmp_path):
    model = model_for("forgejo")
    model.add(Entity("branch_protection", "example/api/release/*", "release/*", "branch_protection", {"branch_name": "release/*", "_parent_path": "example/api"}, "repository:example/api"))
    plan = backend("forgejo").provider().plan(model, tmp_path)
    assert not any(c.resource_type == "forgejo_organization" for c in plan.candidates)
    assert not any(c.entity.key.endswith("release/*") for c in plan.candidates)
    assert len([c for c in plan.coverage.items if c.status == "not_importable"]) == 2


def test_gitlab_label_uses_numeric_id(tmp_path):
    model = model_for("gitlab")
    model.add(Entity("label", "example/platform/api/17", "17", "project_label", {"name": "bug", "_parent_id": "10"}, "project:example/platform/api"))
    plan = backend("gitlab").provider().plan(model, tmp_path)
    label = next(c for c in plan.candidates if c.resource_type == "gitlab_project_label")
    assert label.import_id == "10:17"
    member = next(c for c in plan.candidates if c.resource_type == "gitlab_project_membership")
    assert "project" in member.references and "project_id" not in member.references


def test_gitea_teams_are_inventory_only_but_complete_memberships_use_literal_team_id(tmp_path):
    provider = backend("gitea").provider()
    model = model_for("gitea")
    plan = provider.plan(model, tmp_path)

    assert not any(candidate.resource_type == "gitea_team" for candidate in plan.candidates)
    members = next(
        candidate for candidate in plan.candidates
        if candidate.resource_type == "gitea_team_members"
    )
    assert members.import_id == "2"
    assert any(
        item.key == "example/platform" and item.status == "inventory_only"
        for item in plan.coverage.items
    )

    resources, schema = FakeRunner().hydrate(provider, plan, tmp_path / "hydrate-gitea-team")
    project = provider.compile(plan, resources, schema)
    compiled = next(
        item for item in project.resources
        if item.candidate.resource_type == "gitea_team_members"
    )
    assert compiled.body.attributes["team_id"] == "2"
    assert not compiled.body.attributes["team_id"].startswith("module.")

    output = tmp_path / "gitea-output"
    provider.render(project, output, prevent_destroy=True, split_teams=False)
    assert not (output / "modules/gitea_team").exists()
    assert (output / "modules/gitea_team_members").is_dir()


def test_schema_removes_computed_only_and_rejects_unknown():
    schema = ProviderSchema("example/provider", {"example_thing": {"block": {"attributes": {"id": {"computed": True}, "name": {"required": True}}}}})
    body = schema.clean("example_thing", Body(OrderedDict(id='"1"', name='"Test"')))
    assert list(body.attributes) == ["name"]
    with pytest.raises(GenerationError, match="unknown"):
        schema.clean("example_thing", Body(OrderedDict(unknown="false")))


def test_schema_required_private_input_not_defaulted(tmp_path):
    provider = backend("forgejo").provider()
    plan = provider.plan(model_for("forgejo"), tmp_path)
    resources, schema = FakeRunner().hydrate(provider, plan, tmp_path / "hydrate")
    schema.resources["forgejo_repository_webhook"]["block"]["attributes"]["config"].update({"required": True})
    project = provider.compile(plan, resources, schema)
    provider.render(project, tmp_path / "out", prevent_destroy=True, split_teams=False)
    assert "private-value" not in "".join(p.read_text() for p in (tmp_path / "out").rglob("*.tf"))
    variables = (tmp_path / "out/variables.tf").read_text()
    assert "sensitive = true" in variables and "default" not in variables


def test_recursive_groups_form_separate_modules(tmp_path):
    provider = backend("gitlab").provider()
    plan = provider.plan(model_for("gitlab"), tmp_path)
    resources, schema = FakeRunner().hydrate(provider, plan, tmp_path / "hydrate")
    project = provider.compile(plan, resources, schema)
    groups = [item for item in project.resources if item.candidate.resource_type == "gitlab_group"]
    assert {item.candidate.module for item in groups} == {"gitlab_group_level_0", "gitlab_group_level_1"}
    child = next(item for item in groups if item.candidate.entity.key.endswith("platform"))
    assert child.body.attributes["parent_id"] == 'module.gitlab_group_level_0.objects["example"].id'


@pytest.mark.parametrize("text", ['literal ${token}', '%{ if condition }', 'Café "quoted"', 'line1\nline2', 'a,]b'])
def test_hcl_string_round_trip(text):
    assert literal_string(hcl_string(text)) == text


def test_literal_trailing_comma_does_not_mutate_strings():
    assert literal_list('[10, 11,]') == [10, 11]
    assert literal_list('["a,]b",]') == ["a,]b"]
    assert literal_list('[var.dynamic,]') is None


def test_heredoc_content_not_reindented():
    assert render_expr('<<EOT\nno indent\n  two spaces\nEOT', 8) == ['<<EOT', 'no indent', '  two spaces', 'EOT']


def test_incompatible_lifecycles_are_rejected():
    family = Family("example")
    family.add(Body(), None)
    with pytest.raises(GenerationError):
        family.add(Body(), "lifecycle { prevent_destroy = true }")


def test_native_renderer_splits_missing_attribute_presence(tmp_path):
    from heckle.core.compilation import Candidate, CompiledProject, CompiledResource, ImportPlan
    from heckle.core.model import ForgeModel, Source
    from heckle.generators.project import emit_module
    model = ForgeModel(Source('forgejo', 'https://forge.example.test', 'alice', 'user'))
    one = Entity('repository', 'alice/one', '1', 'repository', {})
    two = Entity('repository', 'alice/two', '2', 'repository', {})
    model.add(one); model.add(two)
    plan = ImportPlan(model)
    c1 = Candidate(one, 'example_repository', 'alice/one', 'example_repository')
    c2 = Candidate(two, 'example_repository', 'alice/two', 'example_repository')
    project = CompiledProject(plan, resources=[
        CompiledResource(c1, Body(OrderedDict(name='"one"', optional_flag='true'))),
        CompiledResource(c2, Body(OrderedDict(name='"two"'))),
    ])
    text = emit_module('example_repository', project.resources, project, prevent_destroy=True)
    assert text.count('resource "example_repository"') == 2
    assert 'optional_flag = try(' not in text
    assert 'resource "example_repository" "this"' in text
    assert 'resource "example_repository" "profile_' in text


def test_native_renderer_fails_closed_on_unsafe_nested_presence():
    from heckle.core.compilation import Candidate, CompiledProject, CompiledResource, ImportPlan
    from heckle.core.model import ForgeModel, Source
    from heckle.generators.project import emit_module
    from heckle.hcl.types import NestedBlock
    model = ForgeModel(Source('gitlab', 'https://gitlab.example.test', 'alice', 'user'))
    entity = model.add(Entity('repository', 'alice/one', '1', 'project', {}))
    candidate = Candidate(entity, 'example_project', '1', 'example_project')
    body = Body(OrderedDict(name='"one"'), blocks=[
        NestedBlock('rule', (), Body(OrderedDict(name='"a"', optional='true')), ''),
        NestedBlock('rule', (), Body(OrderedDict(name='"b"')), ''),
    ])
    project = CompiledProject(ImportPlan(model), resources=[CompiledResource(candidate, body)])
    with pytest.raises(GenerationError, match='unsafe heterogeneous nested'):
        emit_module('example_project', project.resources, project, prevent_destroy=True)


def test_state_only_import_order_follows_resource_dependencies():
    from heckle.core.compilation import Candidate, CompiledProject, CompiledResource, ImportPlan, Reference
    from heckle.core.model import ForgeModel, Source
    from heckle.providers.forgejo.adapter import ForgejoProvider

    model = ForgeModel(Source('forgejo', 'https://forge.example.test', 'alice', 'organization'))
    repo = model.add(Entity('repository', 'alice/repo', '10', 'repository', {}))
    hook = model.add(Entity('webhook', 'alice/repo/7', '7', 'repository_webhook', {}, repo.uid))
    plan = ImportPlan(model)
    parent = Candidate(repo, 'forgejo_repository', 'alice/repo', 'forgejo_repository')
    child = Candidate(hook, 'forgejo_repository_webhook', 'alice/repo/7', 'forgejo_repository_webhook')
    child.references['repository_id'] = Reference(repo.uid, 'id')
    project = CompiledProject(plan, resources=[
        CompiledResource(child, Body(OrderedDict())),
        CompiledResource(parent, Body(OrderedDict())),
    ])
    imports = ForgejoProvider().rendered_imports(project)
    assert imports == [
        (parent.address, 'alice/repo'),
        (child.address, 'alice/repo/7'),
    ]


def test_existing_legacy_state_becomes_moved_block_not_reimport(tmp_path):
    from collections import OrderedDict
    from heckle.core.compilation import Candidate, CompiledProject, CompiledResource, ImportPlan
    from heckle.core.model import Entity, ForgeModel, Source
    from heckle.hcl.types import Body
    from heckle.pipeline import reconcile_existing_state
    from heckle.providers.forgejo.adapter import ForgejoProvider

    model = ForgeModel(Source("forgejo", "https://forge.example.test", "alice", "user"))
    entity = Entity("repository", "alice/legacy", "1", "repository", {}, owner="alice")
    model.add(entity)
    candidate = Candidate(entity, "forgejo_repository", "alice/legacy", "forgejo_repository")
    plan = ImportPlan(model, [candidate])
    item = CompiledResource(
        candidate,
        Body(OrderedDict([("owner", '"alice"'), ("name", '"legacy"')])),
        resource_name="profile_deadbeef00",
    )
    project = CompiledProject(plan, resources=[item])

    legacy = candidate.address
    current = item.address
    assert legacy != current
    reconcile_existing_state(project, {legacy})

    assert current in project.skip_imports
    assert project.moved == [(legacy, current)]

    out = tmp_path / "out"
    ForgejoProvider().render(project, out, prevent_destroy=True, split_teams=False)
    assert not (out / "imports.tf").read_text().strip()
    moved = (out / "moved.tf").read_text()
    assert f"from = {legacy}" in moved
    assert f"to   = {current}" in moved


def test_gitlab_project_drops_fork_only_mr_target_without_fork_relationship(tmp_path):
    provider = backend("gitlab").provider()
    model = model_for("gitlab")
    plan = provider.plan(model, tmp_path)
    resources, schema = FakeRunner().hydrate(provider, plan, tmp_path / "hydrate-gitlab-fork-fields")

    project_resource = next(resource for resource in resources if resource.resource_type == "gitlab_project")
    project_resource.body.attributes["mr_default_target_self"] = "false"
    schema.resources["gitlab_project"]["block"]["attributes"]["mr_default_target_self"] = {
        "optional": True,
        "type": "bool",
    }

    project = provider.compile(plan, resources, schema)
    compiled = next(item for item in project.resources if item.candidate.resource_type == "gitlab_project")
    assert "forked_from_project_id" not in compiled.body.attributes
    assert "mr_default_target_self" not in compiled.body.attributes


def test_gitlab_project_drops_empty_reviewer_assignment_strategy(tmp_path):
    provider = backend("gitlab").provider()
    model = model_for("gitlab")
    plan = provider.plan(model, tmp_path)
    resources, schema = FakeRunner().hydrate(provider, plan, tmp_path / "hydrate-gitlab-reviewer-strategy")

    project_resource = next(resource for resource in resources if resource.resource_type == "gitlab_project")
    project_resource.body.attributes["reviewer_assignment_strategy"] = '""'
    schema.resources["gitlab_project"]["block"]["attributes"]["reviewer_assignment_strategy"] = {
        "optional": True,
        "type": "string",
    }

    project = provider.compile(plan, resources, schema)
    compiled = next(item for item in project.resources if item.candidate.resource_type == "gitlab_project")
    assert "reviewer_assignment_strategy" not in compiled.body.attributes


def test_gitlab_project_preserves_valid_reviewer_assignment_strategy(tmp_path):
    provider = backend("gitlab").provider()
    model = model_for("gitlab")
    plan = provider.plan(model, tmp_path)
    resources, schema = FakeRunner().hydrate(provider, plan, tmp_path / "hydrate-gitlab-valid-reviewer-strategy")

    project_resource = next(resource for resource in resources if resource.resource_type == "gitlab_project")
    project_resource.body.attributes["reviewer_assignment_strategy"] = '"disabled"'
    schema.resources["gitlab_project"]["block"]["attributes"]["reviewer_assignment_strategy"] = {
        "optional": True,
        "type": "string",
    }

    project = provider.compile(plan, resources, schema)
    compiled = next(item for item in project.resources if item.candidate.resource_type == "gitlab_project")
    assert compiled.body.attributes["reviewer_assignment_strategy"] == '"disabled"'


def test_gitlab_project_drops_empty_provider_validated_strings(tmp_path):
    provider = backend("gitlab").provider()
    model = model_for("gitlab")
    plan = provider.plan(model, tmp_path)
    resources, schema = FakeRunner().hydrate(provider, plan, tmp_path / "hydrate-gitlab-empty-enums")

    resource = next(r for r in resources if r.resource_type == "gitlab_project")
    names = {
        "visibility_level", "merge_method", "resource_group_default_process_mode",
        "squash_option", "pages_access_level",
        "ci_pipeline_variables_minimum_override_role", "analytics_access_level",
        "auto_cancel_pending_pipelines", "auto_devops_deploy_strategy",
        "build_git_strategy", "builds_access_level", "container_registry_access_level",
        "forking_access_level", "issues_access_level", "merge_requests_access_level",
        "repository_access_level", "requirements_access_level",
        "reviewer_assignment_strategy", "security_and_compliance_access_level",
        "snippets_access_level", "wiki_access_level", "releases_access_level",
        "environments_access_level", "feature_flags_access_level",
        "infrastructure_access_level", "monitor_access_level",
        "model_experiments_access_level", "model_registry_access_level",
        "package_registry_access_level",
    }
    for name in names:
        resource.body.attributes[name] = '""'
        _schema_attr(schema, resource.resource_type, name)

    project = provider.compile(plan, resources, schema)
    compiled = next(r for r in project.resources if r.candidate.resource_type == "gitlab_project")
    assert not (names & compiled.body.attributes.keys())


def test_gitlab_project_preserves_valid_provider_validated_strings(tmp_path):
    provider = backend("gitlab").provider()
    model = model_for("gitlab")
    plan = provider.plan(model, tmp_path)
    resources, schema = FakeRunner().hydrate(provider, plan, tmp_path / "hydrate-gitlab-valid-enums")
    resource = next(r for r in resources if r.resource_type == "gitlab_project")
    values = {
        "visibility_level": '"private"',
        "merge_method": '"merge"',
        "resource_group_default_process_mode": '"unordered"',
        "reviewer_assignment_strategy": '"disabled"',
    }
    for name, value in values.items():
        resource.body.attributes[name] = value
        _schema_attr(schema, resource.resource_type, name)

    project = provider.compile(plan, resources, schema)
    compiled = next(r for r in project.resources if r.candidate.resource_type == "gitlab_project")
    for name, value in values.items():
        assert compiled.body.attributes[name] == value


def test_gitlab_group_drops_empty_provider_validated_strings(tmp_path):
    provider = backend("gitlab").provider()
    model = model_for("gitlab")
    plan = provider.plan(model, tmp_path)
    resources, schema = FakeRunner().hydrate(provider, plan, tmp_path / "hydrate-gitlab-group-empty-enums")
    resource = next(r for r in resources if r.resource_type == "gitlab_group")
    names = {
        "visibility_level", "project_creation_level", "subgroup_creation_level",
        "wiki_access_level", "shared_runners_setting",
    }
    for name in names:
        resource.body.attributes[name] = '""'
        _schema_attr(schema, resource.resource_type, name)

    project = provider.compile(plan, resources, schema)
    compiled = next(r for r in project.resources if r.candidate.resource_type == "gitlab_group")
    assert not (names & compiled.body.attributes.keys())


def test_gitlab_group_preserves_valid_provider_validated_strings(tmp_path):
    provider = backend("gitlab").provider()
    model = model_for("gitlab")
    plan = provider.plan(model, tmp_path)
    resources, schema = FakeRunner().hydrate(provider, plan, tmp_path / "hydrate-gitlab-group-valid-enums")
    resource = next(r for r in resources if r.resource_type == "gitlab_group")
    values = {
        "visibility_level": '"private"',
        "project_creation_level": '"developer"',
        "subgroup_creation_level": '"maintainer"',
    }
    for name, value in values.items():
        resource.body.attributes[name] = value
        _schema_attr(schema, resource.resource_type, name)

    project = provider.compile(plan, resources, schema)
    compiled = next(r for r in project.resources if r.candidate.resource_type == "gitlab_group")
    for name, value in values.items():
        assert compiled.body.attributes[name] == value


def test_gitlab_branch_protection_drops_empty_legacy_access_levels(tmp_path):
    provider = backend("gitlab").provider()
    model = model_for("gitlab")
    plan = provider.plan(model, tmp_path)
    resources, schema = FakeRunner().hydrate(provider, plan, tmp_path / "hydrate-gitlab-branch-empty-access")
    resource = next(r for r in resources if r.resource_type == "gitlab_branch_protection")
    for name in ("merge_access_level", "push_access_level"):
        resource.body.attributes[name] = '""'
        _schema_attr(schema, resource.resource_type, name)

    project = provider.compile(plan, resources, schema)
    compiled = next(r for r in project.resources if r.candidate.resource_type == "gitlab_branch_protection")
    assert "merge_access_level" not in compiled.body.attributes
    assert "push_access_level" not in compiled.body.attributes


def test_gitlab_project_membership_drops_empty_expiry(tmp_path):
    provider = backend("gitlab").provider()
    model = model_for("gitlab")
    plan = provider.plan(model, tmp_path)
    resources, schema = FakeRunner().hydrate(provider, plan, tmp_path / "hydrate-gitlab-membership-expiry")

    membership = next(resource for resource in resources if resource.resource_type == "gitlab_project_membership")
    membership.body.attributes["expires_at"] = '""'
    schema.resources["gitlab_project_membership"]["block"]["attributes"]["expires_at"] = {
        "optional": True,
        "computed": True,
        "type": "string",
    }

    project = provider.compile(plan, resources, schema)
    compiled = next(item for item in project.resources if item.candidate.resource_type == "gitlab_project_membership")
    assert "expires_at" not in compiled.body.attributes


def test_gitlab_project_membership_preserves_valid_expiry(tmp_path):
    provider = backend("gitlab").provider()
    model = model_for("gitlab")
    plan = provider.plan(model, tmp_path)
    resources, schema = FakeRunner().hydrate(provider, plan, tmp_path / "hydrate-gitlab-membership-valid-expiry")

    membership = next(resource for resource in resources if resource.resource_type == "gitlab_project_membership")
    membership.body.attributes["expires_at"] = '"2027-01-31"'
    schema.resources["gitlab_project_membership"]["block"]["attributes"]["expires_at"] = {
        "optional": True,
        "computed": True,
        "type": "string",
    }

    project = provider.compile(plan, resources, schema)
    compiled = next(item for item in project.resources if item.candidate.resource_type == "gitlab_project_membership")
    assert compiled.body.attributes["expires_at"] == '"2027-01-31"'


def _gitlab_add_scoped(model, native_kind, kind, identity, native=None):
    project = next(e for e in model.entities.values() if e.native_kind == "project")
    data = {"_parent_id": project.remote_id, "_parent_path": project.key, **(native or {})}
    return model.add(Entity(kind, f"{project.key}/{identity}", identity, native_kind, data, project.uid, project.key))


def _schema_attr(schema, rtype, name, *, attr_type="string", optional=True, computed=True):
    schema.resources[rtype]["block"]["attributes"][name] = {
        "type": attr_type,
        "optional": optional,
        "computed": computed,
    }


def test_gitlab_environment_normalizes_unset_values_and_destroy_control(tmp_path):
    provider = backend("gitlab").provider()
    model = model_for("gitlab")
    _gitlab_add_scoped(model, "project_environment", "environment", "41", {"name": "review"})
    plan = provider.plan(model, tmp_path)
    resources, schema = FakeRunner().hydrate(provider, plan, tmp_path / "hydrate-gitlab-environment")
    resource = next(r for r in resources if r.resource_type == "gitlab_project_environment")
    for name in ("external_url", "tier", "kubernetes_namespace", "flux_resource_path", "auto_stop_setting"):
        resource.body.attributes[name] = '""'
        _schema_attr(schema, resource.resource_type, name)
    resource.body.attributes["stop_before_destroy"] = "false"
    _schema_attr(schema, resource.resource_type, "stop_before_destroy", attr_type="bool")

    project = provider.compile(plan, resources, schema)
    compiled = next(r for r in project.resources if r.candidate.resource_type == "gitlab_project_environment")
    for name in ("external_url", "tier", "kubernetes_namespace", "flux_resource_path", "auto_stop_setting", "stop_before_destroy"):
        assert name not in compiled.body.attributes


def test_gitlab_environment_preserves_valid_optional_values(tmp_path):
    provider = backend("gitlab").provider()
    model = model_for("gitlab")
    _gitlab_add_scoped(model, "project_environment", "environment", "42", {"name": "production"})
    plan = provider.plan(model, tmp_path)
    resources, schema = FakeRunner().hydrate(provider, plan, tmp_path / "hydrate-gitlab-environment-valid")
    resource = next(r for r in resources if r.resource_type == "gitlab_project_environment")
    values = {
        "external_url": '"https://example.test"',
        "tier": '"production"',
        "cluster_agent_id": "123",
        "kubernetes_namespace": '"apps"',
        "flux_resource_path": '"clusters/apps"',
        "auto_stop_setting": '"always"',
    }
    for name, value in values.items():
        resource.body.attributes[name] = value
        _schema_attr(
            schema, resource.resource_type, name,
            attr_type="number" if name == "cluster_agent_id" else "string",
        )

    project = provider.compile(plan, resources, schema)
    compiled = next(r for r in project.resources if r.candidate.resource_type == "gitlab_project_environment")
    for name, value in values.items():
        assert compiled.body.attributes[name] == value


def test_gitlab_environment_drops_cluster_children_without_cluster_agent(tmp_path):
    provider = backend("gitlab").provider()
    model = model_for("gitlab")
    _gitlab_add_scoped(model, "project_environment", "environment", "43", {"name": "review"})
    plan = provider.plan(model, tmp_path)
    resources, schema = FakeRunner().hydrate(provider, plan, tmp_path / "hydrate-gitlab-environment-no-agent")
    resource = next(r for r in resources if r.resource_type == "gitlab_project_environment")
    resource.body.attributes["kubernetes_namespace"] = '"apps"'
    resource.body.attributes["flux_resource_path"] = '"clusters/apps"'
    for name in ("kubernetes_namespace", "flux_resource_path"):
        _schema_attr(schema, resource.resource_type, name)

    project = provider.compile(plan, resources, schema)
    compiled = next(r for r in project.resources if r.candidate.resource_type == "gitlab_project_environment")
    assert "kubernetes_namespace" not in compiled.body.attributes
    assert "flux_resource_path" not in compiled.body.attributes


def test_gitlab_environment_drops_flux_without_kubernetes_namespace(tmp_path):
    provider = backend("gitlab").provider()
    model = model_for("gitlab")
    _gitlab_add_scoped(model, "project_environment", "environment", "44", {"name": "review"})
    plan = provider.plan(model, tmp_path)
    resources, schema = FakeRunner().hydrate(provider, plan, tmp_path / "hydrate-gitlab-environment-no-namespace")
    resource = next(r for r in resources if r.resource_type == "gitlab_project_environment")
    resource.body.attributes["cluster_agent_id"] = "123"
    resource.body.attributes["flux_resource_path"] = '"clusters/apps"'
    _schema_attr(schema, resource.resource_type, "cluster_agent_id", attr_type="number")
    _schema_attr(schema, resource.resource_type, "flux_resource_path")

    project = provider.compile(plan, resources, schema)
    compiled = next(r for r in project.resources if r.candidate.resource_type == "gitlab_project_environment")
    assert compiled.body.attributes["cluster_agent_id"] == "123"
    assert "flux_resource_path" not in compiled.body.attributes


@pytest.mark.parametrize("rtype", ["gitlab_project_hook", "gitlab_group_hook"])
def test_gitlab_hook_drops_empty_branch_filter_strategy(tmp_path, rtype):
    provider = backend("gitlab").provider()
    model = model_for("gitlab")
    if rtype == "gitlab_group_hook":
        root = next(e for e in model.entities.values() if e.native_kind == "group" and e.parent is None)
        model.add(Entity("webhook", f"{root.key}/hook-9", "9", "group_hook", {"_parent_id": root.remote_id, "url": "https://hook.example.test"}, root.uid, root.key))
    plan = provider.plan(model, tmp_path)
    resources, schema = FakeRunner().hydrate(provider, plan, tmp_path / f"hydrate-{rtype}")
    resource = next(r for r in resources if r.resource_type == rtype)
    resource.body.attributes["branch_filter_strategy"] = '""'
    _schema_attr(schema, rtype, "branch_filter_strategy")

    project = provider.compile(plan, resources, schema)
    compiled = next(r for r in project.resources if r.candidate.resource_type == rtype)
    assert "branch_filter_strategy" not in compiled.body.attributes


@pytest.mark.parametrize("rtype", ["gitlab_project_hook", "gitlab_group_hook"])
def test_gitlab_hook_all_branches_drops_push_filter(tmp_path, rtype):
    provider = backend("gitlab").provider()
    model = model_for("gitlab")
    if rtype == "gitlab_group_hook":
        root = next(e for e in model.entities.values() if e.native_kind == "group" and e.parent is None)
        model.add(Entity("webhook", f"{root.key}/hook-10", "10", "group_hook", {"_parent_id": root.remote_id, "url": "https://hook.example.test"}, root.uid, root.key))
    plan = provider.plan(model, tmp_path)
    resources, schema = FakeRunner().hydrate(provider, plan, tmp_path / f"hydrate-all-branches-{rtype}")
    resource = next(r for r in resources if r.resource_type == rtype)
    resource.body.attributes["branch_filter_strategy"] = '"all_branches"'
    resource.body.attributes["push_events_branch_filter"] = '"devel"'
    _schema_attr(schema, rtype, "branch_filter_strategy")
    _schema_attr(schema, rtype, "push_events_branch_filter")

    project = provider.compile(plan, resources, schema)
    compiled = next(r for r in project.resources if r.candidate.resource_type == rtype)
    assert compiled.body.attributes["branch_filter_strategy"] == '"all_branches"'
    assert "push_events_branch_filter" not in compiled.body.attributes


@pytest.mark.parametrize("rtype", ["gitlab_project_hook", "gitlab_group_hook"])
def test_gitlab_hook_non_all_branches_preserves_push_filter(tmp_path, rtype):
    provider = backend("gitlab").provider()
    model = model_for("gitlab")
    if rtype == "gitlab_group_hook":
        root = next(e for e in model.entities.values() if e.native_kind == "group" and e.parent is None)
        model.add(Entity("webhook", f"{root.key}/hook-11", "11", "group_hook", {"_parent_id": root.remote_id, "url": "https://hook.example.test"}, root.uid, root.key))
    plan = provider.plan(model, tmp_path)
    resources, schema = FakeRunner().hydrate(provider, plan, tmp_path / f"hydrate-wildcard-{rtype}")
    resource = next(r for r in resources if r.resource_type == rtype)
    resource.body.attributes["branch_filter_strategy"] = '"wildcard"'
    resource.body.attributes["push_events_branch_filter"] = '"release/*"'
    _schema_attr(schema, rtype, "branch_filter_strategy")
    _schema_attr(schema, rtype, "push_events_branch_filter")

    project = provider.compile(plan, resources, schema)
    compiled = next(r for r in project.resources if r.candidate.resource_type == rtype)
    assert compiled.body.attributes["push_events_branch_filter"] == '"release/*"'


def test_gitlab_any_approver_rule_is_inventory_only(tmp_path):
    provider = backend("gitlab").provider()
    model = model_for("gitlab")
    entity = _gitlab_add_scoped(
        model, "project_approval_rule", "approval_rule", "57",
        {"name": "All Members", "rule_type": "any_approver", "approvals_required": 0},
    )
    plan = provider.plan(model, tmp_path)
    assert not any(c.entity.uid == entity.uid for c in plan.candidates)
    coverage = next(item for item in plan.coverage.items if item.key == entity.key)
    assert coverage.status == "inventory_only"
    assert "any_approver" in coverage.reason


def test_gitlab_approval_rule_normalizes_provider_constraints(tmp_path):
    provider = backend("gitlab").provider()
    model = model_for("gitlab")
    _gitlab_add_scoped(model, "project_approval_rule", "approval_rule", "55", {"name": "Maintainers"})
    plan = provider.plan(model, tmp_path)
    resources, schema = FakeRunner().hydrate(provider, plan, tmp_path / "hydrate-gitlab-approval")
    resource = next(r for r in resources if r.resource_type == "gitlab_project_approval_rule")
    attrs = {
        "rule_type": '"regular"',
        "report_type": '""',
        "disable_importing_default_any_approver_rule_on_create": "false",
        "applies_to_all_protected_branches": "true",
        "protected_branch_ids": "[101, 102]",
    }
    for name, value in attrs.items():
        resource.body.attributes[name] = value
        _schema_attr(schema, resource.resource_type, name, attr_type="bool" if value in {"true", "false"} else "string")

    project = provider.compile(plan, resources, schema)
    compiled = next(r for r in project.resources if r.candidate.resource_type == "gitlab_project_approval_rule")
    assert "report_type" not in compiled.body.attributes
    assert "disable_importing_default_any_approver_rule_on_create" not in compiled.body.attributes
    assert "protected_branch_ids" not in compiled.body.attributes
    assert "rule_type" not in compiled.body.attributes


def test_gitlab_report_approval_rule_preserves_rule_and_report_type(tmp_path):
    provider = backend("gitlab").provider()
    model = model_for("gitlab")
    _gitlab_add_scoped(model, "project_approval_rule", "approval_rule", "56", {"name": "Coverage-Check"})
    plan = provider.plan(model, tmp_path)
    resources, schema = FakeRunner().hydrate(provider, plan, tmp_path / "hydrate-gitlab-report-approval")
    resource = next(r for r in resources if r.resource_type == "gitlab_project_approval_rule")
    resource.body.attributes["rule_type"] = '"report_approver"'
    resource.body.attributes["report_type"] = '"code_coverage"'
    _schema_attr(schema, resource.resource_type, "rule_type")
    _schema_attr(schema, resource.resource_type, "report_type")

    project = provider.compile(plan, resources, schema)
    compiled = next(r for r in project.resources if r.candidate.resource_type == "gitlab_project_approval_rule")
    assert compiled.body.attributes["rule_type"] == '"report_approver"'
    assert compiled.body.attributes["report_type"] == '"code_coverage"'


def test_gitlab_group_push_rules_are_left_unmanaged(tmp_path):
    from heckle.hcl.types import NestedBlock
    provider = backend("gitlab").provider()
    model = model_for("gitlab")
    plan = provider.plan(model, tmp_path)
    resources, schema = FakeRunner().hydrate(provider, plan, tmp_path / "hydrate-gitlab-group-push-rules")
    resource = next(r for r in resources if r.resource_type == "gitlab_group")
    resource.body.blocks.append(NestedBlock("push_rules", (), Body(OrderedDict(reject_unsigned_commits="false")), ""))
    schema.resources[resource.resource_type]["block"]["block_types"]["push_rules"] = {
        "nesting_mode": "list",
        "block": {"attributes": {"reject_unsigned_commits": {"optional": True, "type": "bool"}}},
    }

    project = provider.compile(plan, resources, schema)
    compiled = next(r for r in project.resources if r.candidate.resource_type == "gitlab_group")
    assert all(block.name != "push_rules" for block in compiled.body.blocks)


def test_gitlab_group_membership_drops_destroy_only_controls(tmp_path):
    provider = backend("gitlab").provider()
    model = model_for("gitlab")
    root = next(e for e in model.entities.values() if e.native_kind == "group" and e.parent is None)
    model.add(Entity("membership", f"{root.key}/5", "5", "group_membership", {"_parent_id": root.remote_id, "access_level": 40}, root.uid, root.key))
    plan = provider.plan(model, tmp_path)
    resources, schema = FakeRunner().hydrate(provider, plan, tmp_path / "hydrate-gitlab-group-membership-controls")
    resource = next(r for r in resources if r.resource_type == "gitlab_group_membership")
    for name in ("skip_subresources_on_destroy", "unassign_issuables_on_destroy"):
        resource.body.attributes[name] = "false"
        _schema_attr(schema, resource.resource_type, name, attr_type="bool")

    project = provider.compile(plan, resources, schema)
    compiled = next(r for r in project.resources if r.candidate.resource_type == "gitlab_group_membership")
    assert "skip_subresources_on_destroy" not in compiled.body.attributes
    assert "unassign_issuables_on_destroy" not in compiled.body.attributes


def test_gitlab_deploy_key_drops_empty_expiry(tmp_path):
    provider = backend("gitlab").provider()
    model = model_for("gitlab")
    _gitlab_add_scoped(model, "deploy_key", "deploy_key", "77", {"title": "deploy", "key": "ssh-ed25519 AAA"})
    plan = provider.plan(model, tmp_path)
    resources, schema = FakeRunner().hydrate(provider, plan, tmp_path / "hydrate-gitlab-deploy-key")
    resource = next(r for r in resources if r.resource_type == "gitlab_deploy_key")
    resource.body.attributes["expires_at"] = '""'
    _schema_attr(schema, resource.resource_type, "expires_at")

    project = provider.compile(plan, resources, schema)
    compiled = next(r for r in project.resources if r.candidate.resource_type == "gitlab_deploy_key")
    assert "expires_at" not in compiled.body.attributes


def test_gitlab_project_drops_empty_container_expiration_cadence(tmp_path):
    from heckle.hcl.types import NestedBlock

    provider = backend("gitlab").provider()
    model = model_for("gitlab")
    plan = provider.plan(model, tmp_path)
    resources, schema = FakeRunner().hydrate(provider, plan, tmp_path / "hydrate-gitlab-container-policy-empty-cadence")
    resource = next(r for r in resources if r.resource_type == "gitlab_project")
    policy = NestedBlock(
        "container_expiration_policy",
        (),
        Body(OrderedDict(cadence='""', keep_n="10", enabled="false")),
        "",
    )
    resource.body.blocks.append(policy)
    schema.resources[resource.resource_type]["block"]["block_types"]["container_expiration_policy"] = {
        "nesting_mode": "list",
        "block": {
            "attributes": {
                "cadence": {"optional": True, "computed": True, "type": "string"},
                "keep_n": {"optional": True, "computed": True, "type": "number"},
                "enabled": {"optional": True, "computed": True, "type": "bool"},
            }
        },
    }

    project = provider.compile(plan, resources, schema)
    compiled = next(r for r in project.resources if r.candidate.resource_type == "gitlab_project")
    policy = next(block for block in compiled.body.blocks if block.name == "container_expiration_policy")
    assert "cadence" not in policy.body.attributes
    assert policy.body.attributes["keep_n"] == "10"
    assert policy.body.attributes["enabled"] == "false"


def test_gitlab_project_preserves_valid_container_expiration_cadence(tmp_path):
    from heckle.hcl.types import NestedBlock

    provider = backend("gitlab").provider()
    model = model_for("gitlab")
    plan = provider.plan(model, tmp_path)
    resources, schema = FakeRunner().hydrate(provider, plan, tmp_path / "hydrate-gitlab-container-policy-valid-cadence")
    resource = next(r for r in resources if r.resource_type == "gitlab_project")
    resource.body.blocks.append(
        NestedBlock("container_expiration_policy", (), Body(OrderedDict(cadence='"7d"')), "")
    )
    schema.resources[resource.resource_type]["block"]["block_types"]["container_expiration_policy"] = {
        "nesting_mode": "list",
        "block": {"attributes": {"cadence": {"optional": True, "computed": True, "type": "string"}}},
    }

    project = provider.compile(plan, resources, schema)
    compiled = next(r for r in project.resources if r.candidate.resource_type == "gitlab_project")
    policy = next(block for block in compiled.body.blocks if block.name == "container_expiration_policy")
    assert policy.body.attributes["cadence"] == '"7d"'


def test_gitlab_tag_protection_prefers_explicit_acl_identity(tmp_path):
    from heckle.hcl.types import NestedBlock

    provider = backend("gitlab").provider()
    model = model_for("gitlab")
    _gitlab_add_scoped(model, "tag_protection", "tag_protection", "v*", {"name": "v*"})
    plan = provider.plan(model, tmp_path)
    resources, schema = FakeRunner().hydrate(provider, plan, tmp_path / "hydrate-gitlab-tag-acl")
    resource = next(r for r in resources if r.resource_type == "gitlab_tag_protection")
    resource.body.attributes["tag"] = '"v*"'
    _schema_attr(schema, resource.resource_type, "tag")
    resource.body.blocks.append(
        NestedBlock(
            "allowed_to_create",
            (),
            Body(OrderedDict(access_level='"maintainer"', user_id="42")),
            "",
        )
    )
    schema.resources[resource.resource_type]["block"]["block_types"]["allowed_to_create"] = {
        "nesting_mode": "set",
        "block": {
            "attributes": {
                "access_level": {"optional": True, "computed": True, "type": "string"},
                "user_id": {"optional": True, "type": "number"},
            }
        },
    }

    project = provider.compile(plan, resources, schema)
    compiled = next(r for r in project.resources if r.candidate.resource_type == "gitlab_tag_protection")
    acl = next(block for block in compiled.body.blocks if block.name == "allowed_to_create")
    assert "access_level" not in acl.body.attributes
    assert acl.body.attributes["user_id"] == "42"


def test_gitlab_tag_protection_preserves_access_level_without_identity(tmp_path):
    from heckle.hcl.types import NestedBlock

    provider = backend("gitlab").provider()
    model = model_for("gitlab")
    _gitlab_add_scoped(model, "tag_protection", "tag_protection", "release-*", {"name": "release-*"})
    plan = provider.plan(model, tmp_path)
    resources, schema = FakeRunner().hydrate(provider, plan, tmp_path / "hydrate-gitlab-tag-access-level")
    resource = next(r for r in resources if r.resource_type == "gitlab_tag_protection")
    resource.body.attributes["tag"] = '"release-*"'
    _schema_attr(schema, resource.resource_type, "tag")
    resource.body.blocks.append(
        NestedBlock("allowed_to_create", (), Body(OrderedDict(access_level='"maintainer"')), "")
    )
    schema.resources[resource.resource_type]["block"]["block_types"]["allowed_to_create"] = {
        "nesting_mode": "set",
        "block": {"attributes": {"access_level": {"optional": True, "computed": True, "type": "string"}}},
    }

    project = provider.compile(plan, resources, schema)
    compiled = next(r for r in project.resources if r.candidate.resource_type == "gitlab_tag_protection")
    acl = next(block for block in compiled.body.blocks if block.name == "allowed_to_create")
    assert acl.body.attributes["access_level"] == '"maintainer"'


def test_gitlab_noncanonical_report_approver_rule_is_inventory_only(tmp_path):
    provider = backend("gitlab").provider()
    model = model_for("gitlab")
    entity = _gitlab_add_scoped(
        model,
        "project_approval_rule",
        "approval_rule",
        "58",
        {"name": "Security-Report", "rule_type": "report_approver", "report_type": "code_coverage"},
    )
    plan = provider.plan(model, tmp_path)
    assert not any(c.entity.uid == entity.uid for c in plan.candidates)
    coverage = next(item for item in plan.coverage.items if item.key == entity.key)
    assert coverage.status == "inventory_only"
    assert "Coverage-Check" in coverage.reason
