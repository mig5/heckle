"""Personal namespace discovery never expands to visible org/group repositories."""
from dataclasses import replace
import json

import pytest

from heckle.cli import build_parser
from heckle.core.inventory import load_inventory, save_inventory
from heckle.core.model import FORGES, ForgeModel, Source
from heckle.core.registry import backend
from heckle.errors import APIError, GenerationError, GitHubAPIError
from heckle.pipeline import Options, Pipeline
from fixtures import connection, FakeRunner
from test_discovery import API


def personal_connection(forge, scope="@me"):
    original = connection(forge)
    return replace(original, source=replace(original.source, scope=scope, namespace_type="user"))


class PersonalAPI(API):
    def __init__(self, forge, *, other=False):
        self.forge = forge
        self.parameters = []
        self.selected = {"id": 5, "login": "alice", "username": "alice", "type": "User", "private_profile": True if not other else False}
        current = {"id": 6, "login": "bob", "username": "bob", "type": "User"} if other else self.selected
        self.repo = {"id": 10, "name": "api", "node_id": "R_10", "owner": {"id": 5, "login": "alice"}, "full_name": "alice/api", "private": True, "visibility": "private", "namespace": {"id": 99, "kind": "user", "full_path": "alice"}, "path": "api", "path_with_namespace": "alice/api"}
        alien = {**self.repo, "id": 11, "name": "shared", "owner": {"login": "a-group"}, "namespace": {"id": 5, "kind": "group"}, "full_name": "a-group/shared", "path_with_namespace": "a-group/shared"}
        objects = {"/user": current, "/users/alice": self.selected, "/version": {"version": "test"},
                   "/repos/alice/api": self.repo, "/projects/10": self.repo,
                   "/namespaces/alice": {"id": 99, "kind": "user", "full_path": "alice", "path": "alice", "name": "Alice"}}
        collections = {"/user/repos": [self.repo, alien], "/users/alice/repos": [self.repo], "/projects": [self.repo, alien],
                       "/users/5/projects": [self.repo, alien], "/users": [self.selected]}
        super().__init__(objects, collections)

    def get(self, path, **kwargs):
        try:
            return super().get(path, **kwargs)
        except APIError as exc:
            if self.forge == "github":
                raise GitHubAPIError("GET", path, exc.status, str(exc)) from exc
            raise

    def get_paginated(self, path, **kwargs):
        self.parameters.append((path, kwargs))
        return super().get_paginated(path, **kwargs)

    def graphql(self, *args):
        self.calls.append("graphql")
        return {"repository": {"branchProtectionRules": {"nodes": [], "pageInfo": {"hasNextPage": False}}}}


@pytest.mark.parametrize("forge", FORGES)
@pytest.mark.parametrize("selector", ["--me", "--user"])
@pytest.mark.parametrize("action", ["generate", "inventory"])
def test_cli_allows_personal_scope(forge, selector, action):
    command = [action, forge, selector] + (["alice"] if selector == "--user" else [])
    parsed = build_parser().parse_args(command)
    assert parsed.me if selector == "--me" else parsed.user == "alice"


@pytest.mark.parametrize("forge", FORGES)
def test_selectors_are_required_and_exclusive(forge):
    namespace = "--group" if forge == "gitlab" else "--org"
    for args in [[], ["--me", "--user", "alice"], [namespace, "a", "--user", "alice"], [namespace, "a", "--me"]]:
        with pytest.raises(SystemExit):
            build_parser().parse_args(["generate", forge, *args])


@pytest.mark.parametrize("forge", FORGES)
@pytest.mark.parametrize("scope", ["@me", "alice", "ALICE"])
def test_personal_discovery_owns_only_selected_repositories(forge, scope, tmp_path):
    api = PersonalAPI(forge)
    handler = backend(forge).forge(personal_connection(forge, scope), api)
    model = handler.discover(tmp_path)
    assert model.source.scope == "alice"
    assert model.source.namespace_type == "user"
    assert {e.key for e in model.entities.values() if e.kind == "repository"} == {"alice/api"}
    assert not list(model.of_kind("group")) and not list(model.of_kind("organization"))
    assert not any(p.startswith(("/orgs/", "/groups/", "/teams/")) for p in api.calls)
    assert any(o.status == "not_applicable" for o in model.observations)
    assert api.calls.count("/user") == 1
    if forge == "gitlab":
        namespace = next(model.of_kind("user_namespace"))
        assert namespace.remote_id == "99"  # The account's ID is 5, not 99.
        assert "/users/5/projects" not in api.calls
        assert ("/projects", {"params": {"order_by": "id", "sort": "asc", "owned": "true"}, "key": None}) in api.parameters
    if forge == "github":
        assert not any("properties/values" in p for p in api.calls)
        assert any(p == "/user/repos" and kw["params"]["affiliation"] == "owner" for p, kw in api.parameters)
    save_inventory(tmp_path / "snapshot", model)
    assert load_inventory(tmp_path / "snapshot").source == model.source


@pytest.mark.parametrize("forge", FORGES)
def test_another_user_retains_namespace_and_never_uses_their_organization(forge, tmp_path):
    api = PersonalAPI(forge, other=True)
    handler = backend(forge).forge(personal_connection(forge, "alice"), api)
    model = handler.discover(tmp_path)
    assert not handler.is_self
    assert model.source.scope == "alice"
    assert {e.key for e in model.entities.values() if e.kind == "repository"} == {"alice/api"}
    if forge == "github":
        assert "/users/alice/repos" in api.calls and "/user/repos" in api.calls
    if forge == "gitlab":
        assert "/users" in api.calls and "/users/5/projects" in api.calls
    if forge in {"gitea", "forgejo"}:
        assert "/users/alice/repos" in api.calls and "/user/repos" not in api.calls


@pytest.mark.parametrize("forge", FORGES)
def test_invalid_auth_never_falls_back_to_a_public_empty_export(forge, tmp_path):
    api = PersonalAPI(forge)
    api.objects["/user"] = APIError(401, "/user")
    with pytest.raises((APIError, GenerationError, GitHubAPIError)):
        backend(forge).forge(personal_connection(forge), api).discover(tmp_path)
    assert not any("repos" in p or "projects" in p for p in api.calls)


@pytest.mark.parametrize("forge", FORGES)
def test_repository_moved_during_discovery_is_not_silently_imported(forge, tmp_path):
    api = PersonalAPI(forge)
    key = "/projects/10" if forge == "gitlab" else "/repos/alice/api"
    api.objects[key] = {**api.repo, "owner": {"login": "new-owner"}, "namespace": {"id": 100}}
    if forge == "github":
        model = backend(forge).forge(personal_connection(forge), api).discover(tmp_path)
        assert any(o.status == "failed" for o in model.observations)
    else:
        with pytest.raises(GenerationError, match="moved|owner"):
            backend(forge).forge(personal_connection(forge), api).discover(tmp_path)


def test_gitlab_private_other_profile_is_not_treated_as_empty(tmp_path):
    api = PersonalAPI("gitlab", other=True)
    api.collections["/users"][0]["private_profile"] = True
    with pytest.raises(GenerationError, match="private profile"):
        backend("gitlab").forge(personal_connection("gitlab", "alice"), api).discover(tmp_path)


@pytest.mark.parametrize("forge", FORGES)
def test_personal_pipeline_and_snapshot_replay_do_not_create_user_resources(forge, tmp_path):
    conn = personal_connection(forge)
    api = PersonalAPI(forge)
    handler = backend(forge).forge(conn, api)
    output = tmp_path / "generated"
    Pipeline(conn, Options(output, keep_inventory=True), forge=handler, runner=FakeRunner(), progress=lambda _: None).run()
    text = "\n".join(p.read_text() for p in output.rglob("*.tf"))
    assert "import {" in text
    for rtype in ("github_organization_settings", "github_membership", "github_team", "gitlab_group", "gitlab_user", "forgejo_organization", "forgejo_user", "gitea_org", "gitea_user"):
        assert f'resource "{rtype}"' not in text
    assert "@me" not in text
    if forge == "gitlab":
        assert '"namespace_id" = 99' in text
    if forge == "github":
        assert not (output / "github-organization.tf").exists()
        assert not (output / "modules/teams").exists()
        assert 'owner = "alice"' in (output / "provider.tf").read_text()
    identity = json.loads((output / ".generation/source.json").read_text())
    assert identity["scope"] == "alice" and identity["namespace_type"] == "user"
    # --user replay needs no identity API calls; --me replay needs only /user.
    for scope in ["alice", "@me"]:
        replay_api = PersonalAPI(forge)
        new_connection = personal_connection(forge, scope)
        Pipeline(new_connection, Options(tmp_path / f"replay-{scope}", from_inventory=output / ".generation/inventory"),
                 forge=backend(forge).forge(new_connection, replay_api), runner=FakeRunner(), progress=lambda _: None).run()
        assert replay_api.calls == (["/user"] if scope == "@me" else [])


@pytest.mark.parametrize("forge", FORGES)
def test_empty_personal_account_is_valid(forge, tmp_path):
    api = PersonalAPI(forge)
    for path in ("/user/repos", "/projects"):
        api.collections[path] = []
    output = tmp_path / "empty"
    conn = personal_connection(forge)
    Pipeline(conn, Options(output), forge=backend(forge).forge(conn, api), runner=FakeRunner(), progress=lambda _: None).run()
    assert not (output / "imports.tf").read_text().strip()


@pytest.mark.parametrize("forge", FORGES)
def test_me_replay_rejects_a_different_authenticated_user(forge, tmp_path):
    original_api = PersonalAPI(forge)
    conn = personal_connection(forge)
    snapshot = tmp_path / "saved"
    model = backend(forge).forge(conn, original_api).discover(tmp_path / "discovery")
    save_inventory(snapshot, model)
    api = PersonalAPI(forge, other=True)  # /user now resolves to bob, not alice.
    pipeline = Pipeline(conn, Options(tmp_path / "wrong-account", from_inventory=snapshot),
                        forge=backend(forge).forge(conn, api), runner=FakeRunner(), progress=lambda _: None)
    with pytest.raises(GenerationError, match="Snapshot"):
        pipeline.run()
    assert api.calls == ["/user"]
    assert not (tmp_path / "wrong-account").exists()
