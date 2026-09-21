from copy import deepcopy
import json
from pathlib import Path
import pytest

from heckle.core.registry import backend
from heckle.errors import APIError
from heckle.forges.gitlab.discovery import GitLabForge
from heckle.core.inventory import save_inventory
from fixtures import connection


class API:
    def __init__(self, objects=None, collections=None):
        self.objects = objects or {}
        self.collections = collections or {}
        self.calls = []
    def get(self, path, **kwargs):
        self.calls.append(path)
        if path not in self.objects: raise APIError(404, path)
        value = self.objects[path]
        if isinstance(value, BaseException): raise value
        return deepcopy(value), {}
    def get_paginated(self, path, **kwargs):
        self.calls.append(path)
        value = self.collections.get(path, [])
        if isinstance(value, BaseException): raise value
        return deepcopy(value)


def test_gitlab_recursion_direct_members_and_ci_redaction(tmp_path):
    api = API(objects={
        "/groups/example": {"id": 1, "name": "Example", "path": "example", "full_path": "example"},
        "/groups/2": {"id": 2, "name": "Sub", "path": "sub", "full_path": "example/sub"},
        "/projects/10": {"id": 10, "name": "App", "path": "app", "path_with_namespace": "example/sub/app", "namespace": {"id": 2}},
        "/projects/10/push_rule": None,
    }, collections={
        "/groups/1/subgroups": [{"id": 2}],
        "/groups/2/projects": [{"id": 10, "namespace": {"id": 2}}],
        "/groups/1/variables": [{"key": "SECRET", "value": "do-not-save", "environment_scope": "*"}],
        "/projects/10/variables": [{"key": "OTHER", "value": "do-not-save", "environment_scope": "*"}],
        "/projects/10/members": [{"id": 3, "username": "alice", "access_level": 40}],
        "/projects/10/badges": [{"id": 20, "kind": "group"}],
    })
    model = GitLabForge(connection("gitlab"), api).discover(tmp_path)
    save_inventory(tmp_path / "saved", model)
    assert "do-not-save" not in (tmp_path / "saved/inventory.json").read_text()
    assert "group:example/sub" in model.entities
    assert "project:example/sub/app" in model.entities
    assert not list(model.of_kind("project_badge"))
    assert not any("members/all" in path for path in api.calls)
    assert not any(o.status == "failed" for o in model.observations)


@pytest.mark.parametrize("forge", ["gitea", "forgejo"])
def test_team_completeness_preserves_unreadable_grants(forge, tmp_path):
    api = API(objects={"/orgs/example": {"id": 1, "name": "example"}, "/version": {"version": "test"}}, collections={
        "/orgs/example/teams": [{"id": 2, "name": "team"}],
        "/teams/2/members": APIError(403, "/teams/2/members"),
        "/teams/2/repos": APIError(403, "/teams/2/repos"),
    })
    model = backend(forge).forge(connection(forge), api).discover(tmp_path)
    team = next(model.of_kind("team"))
    assert not team.native["_members_complete"]
    assert not team.native["_repositories_complete"]
    plan = backend(forge).provider().plan(model, tmp_path)
    assert not any(c.resource_type == "gitea_team_members" for c in plan.candidates)
    if forge == "gitea":
        assert not any(c.resource_type == "gitea_team" for c in plan.candidates)
        assert any(
            item.key == team.key and item.status == "inventory_only"
            for item in plan.coverage.items
        )
    assert any(o.status == "permission_denied" for o in model.observations)
