"""Archiving a GitHub repository must regenerate as archived = true, never as a deletion."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import json
import re

import pytest

from heckle.errors import GenerationError

from heckle.core.security import write_private_json
from heckle.forges.github.discovery import GitHubInventoryExporter
from heckle.forges.github.normalize import normalize
from heckle.hcl.render import hcl_literal
from heckle.pipeline import Options, Pipeline
from heckle.providers.github.model.inventory import load_identity_maps
from heckle.providers.github.plan import GitHubPlanBuilder
from fixtures import BODIES, FakeForge, FakeRunner, connection

ACTIVE_ADDRESS = 'module.repositories.github_repository.this["dz-pytm"]'
ARCHIVED_ADDRESS = 'module.repositories.github_repository.archived["dz-pytm"]'


def repository(name: str, repo_id: int, *, archived: bool) -> dict:
    return {
        "id": repo_id,
        "name": name,
        "node_id": f"R_{repo_id}",
        "visibility": "private",
        "archived": archived,
        "default_branch": "main",
        "description": f"{name} repository",
        "allow_merge_commit": True,
        "allow_squash_merge": True,
    }


def github_files(*, archived: bool) -> dict:
    """An organization in which dz-pytm is either active or archived.

    dz-pytm's repository-scoped inventory is present either way, as an older
    snapshot of an archived repository might contain it.
    """
    api = repository("api", 10, archived=False)
    pytm = repository("dz-pytm", 966294817, archived=archived)
    files = {
        "organization.json": {"id": 1, "login": "example"},
        "repositories.json": [api, pytm],
        "teams.json": [{"id": 2, "slug": "platform", "name": "Platform"}],
        "teams/platform/repositories.json": [api, pytm],
        "actions/organization-secrets.json": [{"name": "DEPLOY", "visibility": "selected"}],
        "coverage.json": {"api_errors": [], "not_managed": []},
    }
    for repo in (api, pytm):
        name = repo["name"]
        files[f"repositories/{name}/repository.json"] = repo
        files[f"repositories/{name}/webhooks.json"] = [
            {
                "id": repo["id"] + 1,
                "config": {"url": f"https://hook.example.test/{name}"},
                "events": ["push"],
                "active": True,
            }
        ]
        files[f"repositories/{name}/direct-collaborators.json"] = [{"login": "alice"}]
    return files


def model(*, archived: bool):
    return normalize(connection("github").source, github_files(archived=archived))


class NamedRunner(FakeRunner):
    """FakeRunner names every hydrated repository "api"; name each after its import ID."""

    def hydrate(self, provider, plan, workspace):
        resources, schema = super().hydrate(provider, plan, workspace)
        ids = {candidate.flat_address: candidate.import_id for candidate in plan.candidates}
        for resource in resources:
            if resource.resource_type == "github_repository":
                resource.body.attributes["name"] = hcl_literal(ids[resource.address])
        # Synthetic (never hydrated) resource the real provider schema contains.
        schema.resources["github_branch_default"] = {
            "block": {
                "attributes": {
                    "repository": {"required": True, "type": "string"},
                    "branch": {"required": True, "type": "string"},
                    "rename": {"optional": True, "type": "bool"},
                }
            }
        }
        return resources, schema


def generate(output, forge_model, monkeypatch, **options):
    # The secret's allowlist names dz-pytm by numeric ID, as GitHub returns it.
    monkeypatch.setitem(
        BODIES,
        "github_actions_organization_secret_repositories",
        {"secret_name": "DEPLOY", "selected_repository_ids": [966294817]},
    )
    runner = options.pop("runner", None) or NamedRunner()
    Pipeline(
        connection("github"),
        Options(output, validate=False, **options),
        runner=runner,
        forge=FakeForge(forge_model),
        progress=lambda _: None,
    ).run()
    return output


def imports(output):
    return set(re.findall(r"(?m)^\s*to = (.+)$", (output / "imports.tf").read_text()))


def test_archived_repository_is_planned_without_its_contents(tmp_path):
    inventory = tmp_path / "inventory"
    for relative, value in github_files(archived=True).items():
        write_private_json(inventory / relative, value)
    builder = GitHubPlanBuilder("example").build(inventory)
    planned = {import_id for _, import_id in builder.imports}

    assert "dz-pytm" in planned  # the repository itself
    assert "api" in planned
    assert "2:api" in planned and "2:dz-pytm" not in planned  # team grant
    assert "api:alice" in planned and "dz-pytm:alice" not in planned  # collaborator
    assert not any(import_id.startswith("dz-pytm/") for import_id in planned)  # webhook
    assert not any('"dz-pytm' in block for block in builder.manual_resources)


def test_archived_repository_ids_resolve(tmp_path):
    inventory = tmp_path / "inventory"
    for relative, value in github_files(archived=True).items():
        write_private_json(inventory / relative, value)
    identities = load_identity_maps(inventory)
    assert identities["repo_id_to_name"]["966294817"] == "dz-pytm"
    assert identities["repo_node_to_name"]["R_966294817"] == "dz-pytm"


def test_archived_repository_renders_as_archived(tmp_path, monkeypatch):
    output = generate(tmp_path / "out", model(archived=True), monkeypatch)
    planned = imports(output)

    assert ARCHIVED_ADDRESS in planned
    assert not any("dz-pytm" in address and address != ARCHIVED_ADDRESS for address in planned)
    assert 'module.repositories.github_repository.this["api"]' in planned
    assert not (output / "moved.tf").exists()  # a fresh adoption moves nothing

    [data_file] = [p for p in output.glob("repository-*.tf") if "dz-pytm" in p.read_text()]
    text = data_file.read_text()
    assert re.search(r'(?m)^\s*"?archived"?\s*=\s*true\s*$', text)
    # Contents, and settings the provider cannot read for an archived repository.
    for unmanaged in ("default_branch", "webhooks", "collaborators", "allow_merge_commit"):
        assert unmanaged not in text
    [api_file] = [p for p in output.glob("repository-*.tf") if '"api"' in p.read_text()]
    assert "allow_merge_commit" in api_file.read_text()  # still managed when active

    module = (output / "modules/repositories/main.tf").read_text()
    archived_block = module[module.index('resource "github_repository" "archived"') :]
    archived_block = archived_block[: archived_block.index("\n}\n")]
    assert "if try(repository.settings.archived, false)" in module
    assert "if !try(repository.settings.archived, false)" in module
    assert "prevent_destroy = true" in archived_block
    assert re.search(r"ignore_changes = \[[^\]]*allow_merge_commit,", archived_block)
    this_block = module[module.index('resource "github_repository" "this"') :]
    this_block = this_block[: this_block.index("\n}\n")]
    assert "ignore_changes" not in this_block
    assert "github_repository.archived : key => repository.name" in module

    # An allowlist naming the archived repository resolves to its key, not a raw ID
    # that var.repository_ids could not index.
    organization = (output / "github-organization.tf").read_text()
    assert '"dz-pytm"' in organization
    assert "966294817" not in organization


def test_archiving_a_managed_repository_is_not_a_deletion(tmp_path, monkeypatch):
    """The reported failure: archive a repository already in state, then regenerate."""
    first = generate(tmp_path / "before", model(archived=False), monkeypatch)
    in_state = imports(first)
    assert ACTIVE_ADDRESS in in_state

    runner = NamedRunner()
    runner.state_addresses = lambda root: set(in_state)
    second = generate(
        tmp_path / "after", model(archived=True), monkeypatch, state_root=first, runner=runner
    )

    # Still configured, now archived, moved in state rather than imported again.
    [data_file] = [p for p in second.glob("repository-*.tf") if "dz-pytm" in p.read_text()]
    assert re.search(r'(?m)^\s*"?archived"?\s*=\s*true\s*$', data_file.read_text())
    assert imports(second) == set()
    moved = (second / "moved.tf").read_text()
    assert moved == f"moved {{\n  from = {ACTIVE_ADDRESS}\n  to   = {ARCHIVED_ADDRESS}\n}}\n"

    # Only the read-only contents of the archived repository leave the configuration;
    # those are the objects a caller forgets from state.
    report = json.loads((second / ".generation/report.json").read_text())
    assert report["adoption_safety"]["mode"] == "existing_state"
    generated = (second / "github-repositories.tf").read_text() + "".join(
        p.read_text() for p in second.glob("repository-*.tf")
    )
    assert "dz-pytm" in generated


def test_discovery_reads_only_settings_of_archived_repositories(tmp_path):
    files = github_files(archived=True)
    api_repo, pytm_repo = files["repositories.json"]

    class Client:
        def __init__(self):
            self.calls = []

        def get(self, path, **kwargs):
            self.calls.append(path)
            objects = {
                "/orgs/example": files["organization.json"],
                "/repos/example/api": api_repo,
                "/repos/example/dz-pytm": pytm_repo,
            }
            return deepcopy(objects.get(path, {})), {}

        def get_paginated(self, path, **kwargs):
            self.calls.append(path)
            if path == "/orgs/example/repos":
                return deepcopy([api_repo, pytm_repo])
            return []

        def graphql(self, query, variables):
            self.calls.append(f"graphql:{variables['name']}")
            return {
                "repository": {
                    "branchProtectionRules": {"nodes": [], "pageInfo": {"hasNextPage": False}}
                }
            }

    client = Client()
    GitHubInventoryExporter(client, "example", tmp_path, workers=1).run()

    saved = json.loads((tmp_path / "inventory/repositories/dz-pytm/repository.json").read_text())
    assert saved["archived"] is True
    pytm_calls = [call for call in client.calls if "dz-pytm" in call]
    assert pytm_calls == ["/repos/example/dz-pytm"]
    assert any(call.startswith("/repos/example/api/") for call in client.calls)
    coverage = json.loads((tmp_path / "inventory/coverage.json").read_text())
    assert coverage["archived_repositories"] == ["dz-pytm"]


def test_unarchiving_moves_the_repository_back(tmp_path, monkeypatch):
    first = generate(tmp_path / "archived", model(archived=True), monkeypatch)
    in_state = imports(first)
    assert ARCHIVED_ADDRESS in in_state

    runner = NamedRunner()
    runner.state_addresses = lambda root: set(in_state)
    second = generate(
        tmp_path / "active", model(archived=False), monkeypatch, state_root=first, runner=runner
    )
    moved = (second / "moved.tf").read_text()
    assert moved == f"moved {{\n  from = {ARCHIVED_ADDRESS}\n  to   = {ACTIVE_ADDRESS}\n}}\n"
    assert ACTIVE_ADDRESS not in imports(second)
    # Its contents become manageable again and are imported.
    assert 'module.repositories.github_branch_default.this["dz-pytm"]' in imports(second)


def test_state_with_both_addresses_is_refused(tmp_path, monkeypatch):
    first = generate(tmp_path / "first", model(archived=True), monkeypatch)
    runner = NamedRunner()
    runner.state_addresses = lambda root: imports(first) | {ACTIVE_ADDRESS}
    with pytest.raises(GenerationError, match="refusing to guess"):
        generate(tmp_path / "second", model(archived=True), monkeypatch, state_root=first, runner=runner)


def test_organization_without_archived_repositories_keeps_one_block(tmp_path, monkeypatch):
    output = generate(tmp_path / "out", model(archived=False), monkeypatch)
    module = (output / "modules/repositories/main.tf").read_text()
    assert 'resource "github_repository" "archived"' not in module
    assert "instances_github_repository_this = var.repositories\n" in module
    assert "ignore_changes = [\n      allow_auto_merge" not in module
