"""Regression cases for the 1.6.0 import defaults in the reported plan errors.

These are offline compiler/renderer tests, not a claim of live provider testing.
The values model repository_resource.go ImportState; identities are synthetic.
"""

from collections import OrderedDict
from dataclasses import replace
from pathlib import Path

import pytest

from heckle.core.model import Entity, ForgeModel, Source
from heckle.hcl.render import hcl_literal
from heckle.hcl.schema import ProviderSchema
from heckle.hcl.types import Body, Resource
from heckle.providers.forgejo.adapter import ForgejoProvider
from heckle.providers.forgejo.repository import (
    CONDITIONAL_FIELDS,
    IGNORED_FIELDS,
    UNREADABLE_FIELDS,
)
from heckle.pipeline import Pipeline, Options
from heckle.reporting import format_report, generation_report
from fixtures import FakeRunner, FakeForge, connection

# The provider sets these without reading their real values during import.
IMPORT_DEFAULTS = {
    "allow_manual_merge": False,
    "archive_on_destroy": False,
    "auto_init": True,
    "autodetect_manual_merge": False,
    "default_delete_branch_after_merge": False,
    "allow_fast_forward_only_merge": False,
    "allow_rebase_update": True,
    "default_allow_maintainer_edit": False,
    "default_update_style": "merge",
    "enable_prune": False,
    "globally_editable_wiki": False,
    "wiki_branch": "",
    "gitignores": "",
    "issue_labels": "",
    "labels": False,
    "lfs": False,
    "lfs_endpoint": "",
    "license": "",
    "milestones": False,
    "readme": "",
    "service": "",
    "trust_model": "default",
    "auth_token": None,
}

READABLE = {
    "has_pull_requests": True,
    "has_wiki": True,
    "has_issues": True,
    "allow_merge_commits": True,
    "allow_rebase": False,
    "allow_rebase_explicit": True,
    "allow_squash_merge": True,
    "ignore_whitespace_conflicts": False,
    "default_merge_style": "squash",
    "private": True,
    "archived": False,
    "mirror": False,
    "mirror_interval": "",
    "clone_addr": "",
    "internal_tracker": None,
    "external_tracker": None,
    "external_wiki": None,
    "name": "api",
    "owner": "alice",
    "description": "Existing repository",
}


def compile_repositories(tmp_path, overrides):
    provider = ForgejoProvider()
    model = ForgeModel(Source("forgejo", "https://forge.example.test", "alice", "user"))
    for index, values in enumerate(overrides):
        name = f"repo-{index}"
        model.add(
            Entity(
                "repository",
                f"alice/{name}",
                str(index + 1),
                "repository",
                {"name": name, "owner": {"login": "alice"}, **values},
                owner="alice",
            )
        )
    plan = provider.plan(model, tmp_path)
    resources = []
    for candidate, values in zip(plan.candidates, overrides):
        data = {**READABLE, **IMPORT_DEFAULTS, "name": candidate.key.split("/")[1], **values}
        resources.append(
            Resource(
                "forgejo_repository",
                candidate.flat_address.split(".")[1],
                Body(OrderedDict((name, hcl_literal(value)) for name, value in data.items())),
                None,
                tmp_path / "generated.tf",
            )
        )
    attrs = {name: {"optional": True, "computed": True} for name in {*READABLE, *IMPORT_DEFAULTS}}
    attrs["name"] = {"required": True}
    attrs["auth_token"] = {"optional": True, "sensitive": True}
    schema = ProviderSchema(
        provider.spec.source, {"forgejo_repository": {"block": {"attributes": attrs}}}
    )
    return provider, provider.compile(plan, resources, schema), resources, schema


@pytest.mark.parametrize("prs", [False, True])
@pytest.mark.parametrize("wiki", [False, True])
@pytest.mark.parametrize("issues", [False, True])
@pytest.mark.parametrize("mirror", [False, True])
def test_import_defaults_never_become_configuration(tmp_path, prs, wiki, issues, mirror):
    provider, project, resources, _ = compile_repositories(
        tmp_path,
        [
            {
                "has_pull_requests": prs,
                "has_wiki": wiki,
                "has_issues": issues,
                "mirror": mirror,
                "mirror_interval": "8h0m0s" if mirror else "",
                "internal_tracker": {"enable_time_tracker": False},
                "external_wiki": {"external_wiki_url": "https://wiki.example.test"},
            }
        ],
    )
    item = project.resources[0]
    assert not (IGNORED_FIELDS & item.body.attributes.keys())
    assert IGNORED_FIELDS <= item.ignored
    assert item.body.attributes["has_pull_requests"] == hcl_literal(prs)
    assert item.body.attributes["has_wiki"] == hcl_literal(wiki)
    assert item.body.attributes["has_issues"] == hcl_literal(issues)
    assert item.body.attributes["private"] == "true"
    for switch, fields in CONDITIONAL_FIELDS.items():
        if item.body.attributes[switch] == "false":
            assert not (set(fields) & item.body.attributes.keys())
            # Inapplicable Optional+Computed fields must also be ignored because
            # the provider can otherwise inject defaults for omitted arguments
            # and turn an import into a live update.
            assert set(fields) <= item.ignored
    # Input snapshots are not mutated by compilation.
    assert resources[0].body.attributes["mirror"] == hcl_literal(mirror)
    assert resources[0].body.attributes["labels"] == "false"
    assert (
        item.candidate.address
        == 'module.forgejo_repository.forgejo_repository.this["alice/repo-0"]'
    )


def test_write_only_defaults_omitted_even_for_enabled_features(tmp_path):
    _, project, _, _ = compile_repositories(tmp_path, [{}])
    assert UNREADABLE_FIELDS <= project.resources[0].ignored
    assert not (UNREADABLE_FIELDS & project.resources[0].body.attributes.keys())
    # Real readable settings remain manageable, including explicit false values.
    for field in CONDITIONAL_FIELDS["has_pull_requests"]:
        assert project.resources[0].body.attributes[field] == hcl_literal(READABLE[field])
        assert field not in project.resources[0].ignored


def test_mixed_repositories_split_profiles_instead_of_emitting_null(tmp_path):
    provider, project, _, _ = compile_repositories(
        tmp_path,
        [
            {"has_pull_requests": False, "has_wiki": False},
            {},
        ],
    )
    output = tmp_path / "output"
    provider.render(project, output, prevent_destroy=True, split_teams=False)
    module = (output / "modules/forgejo_repository/main.tf").read_text()
    for name in IGNORED_FIELDS:
        assert not any(line.lstrip().startswith(name + " =") for line in module.splitlines())
    assert "allow_merge_commits = try(" not in module
    assert "prevent_destroy = true" in module
    assert "ignore_changes = [" in module and "mirror_interval" in module
    assert module.count('resource "forgejo_repository"') == 2
    assert 'resource "forgejo_repository" "this"' in module
    assert 'resource "forgejo_repository" "profile_' in module
    # The reduced profile is the one used by the three real-world repositories
    # whose first import plan previously enabled merge defaults. Its lifecycle
    # must ignore those provider-defaulted PR attributes while the full profile
    # continues to manage them.
    blocks = module.split('resource "forgejo_repository"')
    reduced = next(block for block in blocks if '"profile_' in block)
    full = next(block for block in blocks if '"this"' in block)
    assert "allow_merge_commits" in reduced.split("ignore_changes =", 1)[1]
    assert "default_merge_style" in reduced.split("ignore_changes =", 1)[1]
    assert "allow_merge_commits" not in full.split("ignore_changes =", 1)[1]
    imports = (output / "imports.tf").read_text()
    assert imports.count("import {") == 2
    assert ".profile_" in imports
    assert not list(output.rglob("*.py"))


def test_unknown_parent_switch_is_not_assumed_disabled(tmp_path):
    _, project, _, _ = compile_repositories(tmp_path, [{"has_pull_requests": None}])
    assert project.resources[0].body.attributes["allow_rebase"] == "false"
    assert project.resources[0].body.attributes["has_pull_requests"] == "null"


def test_ignored_attributes_are_limited_to_actual_provider_schema(tmp_path):
    provider, project, resources, schema = compile_repositories(tmp_path, [{}])
    for name in IGNORED_FIELDS:
        schema.resources["forgejo_repository"]["block"]["attributes"].pop(name, None)
        resources[0].body.attributes.pop(name, None)
    result = provider.compile(project.plan, resources, schema)
    assert not result.resources[0].ignored


def test_omissions_are_reported_in_plain_text(tmp_path):
    provider, project, _, _ = compile_repositories(tmp_path, [{}, {}])
    report = generation_report(project.plan, provider.spec, project=project)
    assert len(report["coverage"]["warnings"]) == 1
    assert "settings this provider cannot read" in format_report(report)
    assert "mirror" in report["ignored_attributes"][project.resources[0].candidate.address]


def test_personal_readme_uses_plain_warning(tmp_path):
    _, project, _, _ = compile_repositories(tmp_path, [{}])
    conn = replace(connection("forgejo"), source=project.plan.model.source)
    output = tmp_path / "output"
    Pipeline(
        conn,
        Options(output),
        runner=FakeRunner(),
        forge=FakeForge(project.plan.model),
        progress=lambda _: None,
    ).run()
    readme = (output / "README.md").read_text()
    assert "server-admin" in readme
    assert "stop and check" in readme
    assert "AdminCreateRepo" not in readme
    assert "Heckle keeps your username" in readme


@pytest.mark.parametrize(
    ("controller", "attribute", "before_value", "after_value"),
    [
        (
            "has_issues",
            "internal_tracker.enable_time_tracker",
            {"enable_time_tracker": False},
            {"enable_time_tracker": True},
        ),
        ("has_wiki", "external_wiki", None, {"external_url": "https://example.invalid/wiki"}),
        ("mirror", "enable_prune", False, True),
    ],
)
def test_inactive_feature_drift_rules_are_guarded(controller, attribute, before_value, after_value):
    from heckle.errors import UnsafeChange
    from heckle.providers.forgejo.adapter import ForgejoProvider

    before = {controller: False}
    after = {controller: False}
    top = attribute.split(".", 1)[0]
    before[top] = before_value
    after[top] = after_value
    change = UnsafeChange(
        'module.forgejo_repository.forgejo_repository.profile_deadbeef00["mig5/repo"]',
        ("update",),
        (attribute,),
        before,
        after,
    )
    decision = ForgejoProvider().known_provider_behaviour(change)
    assert decision is not None
    assert dict(decision.guards) == {controller: False}
