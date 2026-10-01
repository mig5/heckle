"""Synthetic, anonymized fixtures. Not captures of live provider validation."""
from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
import json
from typing import Any

from heckle.config import Connection
from heckle.core.model import Source, Entity, ForgeModel
from heckle.forges.github.normalize import normalize
from heckle.hcl.types import Body, Resource
from heckle.hcl.render import hcl_literal
from heckle.hcl.schema import ProviderSchema
from heckle.opentofu import collect_resources


def connection(forge: str) -> Connection:
    return Connection(Source(forge, "https://api.github.com" if forge == "github" else f"https://{forge}.example.test", "example"), "test-token", workers=2)


def model_for(forge: str) -> ForgeModel:
    source = connection(forge).source
    if forge == "github":
        repository = {"id": 10, "name": "api", "node_id": "R_10", "visibility": "private", "archived": False, "description": "A literal ${value} and %{placeholder}", "allow_merge_commit": True, "allow_squash_merge": False}
        files = {
            "organization.json": {"id": 1, "login": "example"}, "repositories.json": [repository],
            "repositories/api/repository.json": repository,
            "memberships.json": [{"user": {"id": 5, "login": "alice"}, "role": "admin"}],
            "teams.json": [{"id": 2, "slug": "platform", "name": "Platform"}],
            "teams/platform/memberships.json": [{"_username": "alice", "role": "maintainer"}],
            "teams/platform/repositories.json": [repository],
            "repositories/api/webhooks.json": [{"id": 7, "config": {"url": "https://hook.example.test/private-value", "secret": "********"}, "events": ["push"], "active": True}],
            "repositories/api/actions-secrets.json": [{"name": "DEPLOY_KEY"}],
            "coverage.json": {"api_errors": [], "not_managed": []},
        }
        return normalize(source, files)
    model = ForgeModel(source)
    if forge == "gitlab":
        root = model.add(Entity("namespace", "example", "1", "group", {"id": 1, "name": "Example", "path": "example", "full_path": "example"}))
        child = model.add(Entity("namespace", "example/platform", "2", "group", {"id": 2, "name": "Platform", "path": "platform", "full_path": "example/platform", "parent_id": 1}, root.uid))
        project = model.add(Entity("repository", "example/platform/api", "10", "project", {"id": 10, "name": "API", "path": "api", "namespace": {"id": 2}}, child.uid, "example/platform/api"))
        for native, kind, identity, data in [
            ("project_membership", "membership", "5", {"id": 5, "username": "alice", "access_level": 40}),
            ("branch_protection", "branch_protection", "main", {"name": "main"}),
            ("project_hook", "webhook", "7", {"id": 7, "url": "https://hook.example.test/private-value"}),
            ("project_variable", "variable", "TOKEN@*", {"key": "TOKEN", "value": "[REDACTED]"}),
        ]:
            model.add(Entity(kind, project.key + "/" + identity, identity, native, {**data, "_parent_id": "10", "_parent_path": project.key}, project.uid, project.key))
        return model
    org = model.add(Entity("namespace", "example", "1", "organization", {"id": 1, "name": "example"}, owner="example"))
    repo = model.add(Entity("repository", "example/api", "10", "repository", {"id": 10, "name": "api", "owner": {"login": "example"}}, org.uid, "example/api"))
    model.add(Entity("team", "example/platform", "2", "team", {"id": 2, "name": "platform", "_members_complete": True, "_members": ["alice"], "_repositories_complete": True, "_repositories": ["api"]}, org.uid, "example"))
    model.add(Entity("webhook", "example/api/7", "7", "repository_webhook", {"id": 7, "_parent_id": "10", "_parent_path": repo.key}, repo.uid, repo.key))
    model.add(Entity("branch_protection", "example/api/main", "main", "branch_protection", {"branch_name": "main", "_parent_id": "10", "_parent_path": repo.key}, repo.uid, repo.key))
    return model


# Minimal provider-shaped objects used to exercise our compiler. Tests never call
# them genuine provider schema snapshots or evidence of live import success.
BODIES = {
    "github_organization_settings": {"billing_email": "ops@example.test"},
    "github_membership": {"username": "alice", "role": "admin"},
    "github_team": {"name": "Platform", "privacy": "closed"},
    "github_team_membership": {"team_id": "2", "username": "alice", "role": "maintainer"},
    "github_team_repository": {"team_id": "2", "repository": "api", "permission": "push"},
    "github_repository": {"name": "api", "visibility": "private"},
    "gitlab_group": {"name": "Example", "path": "example", "parent_id": None},
    "gitlab_project": {"name": "API", "path": "api", "namespace_id": 2},
    "gitlab_project_membership": {"project": "10", "user_id": 5, "access_level": "maintainer"},
    "gitlab_branch_protection": {"project": "10", "branch": "main", "push_access_level": "maintainer"},
    "gitlab_project_hook": {"project": "10", "url": "https://hook.example.test/private-value", "token": None},
    "gitea_org": {"name": "example"},
    "gitea_repository": {"name": "api", "username": "example", "private": True},
    "gitea_team": {"name": "platform", "organisation": "example", "repositories": ["api"]},
    "gitea_team_members": {"team_id": 2, "members": ["alice"]},
    "gitea_repository_webhook": {"username": "example", "name": "api", "events": ["push"], "type": "gitea", "url": "https://hook.example.test/private-value", "secret": None},
    "forgejo_repository": {"owner": "example", "name": "api", "private": True},
    "forgejo_team": {"organization_id": 1, "name": "platform", "units_map": {"repo.code": "write"}},
    "forgejo_repository_webhook": {"repository_id": 10, "type": "forgejo", "config": {"url": "https://hook.example.test/private-value", "content_type": "json"}, "events": ["push"], "authorization_header": None},
    "forgejo_branch_protection": {"repository_id": 10, "branch_name": "main", "enable_push": False, "enable_push_whitelist": False},
}


class FakeRunner:
    """Offline orchestration double; deliberately not an OpenTofu validator."""
    def __init__(self) -> None:
        self.calls: list[str] = []

    def check_version(self, cwd):
        self.calls.append("version")
        return "1.8.0"

    def hydrate(self, provider, plan, workspace):
        self.calls.append("hydrate")
        workspace.mkdir(parents=True)
        extra = workspace / "extra.tf"
        extra.write_text(provider.bootstrap_extra(plan))
        manual = {resource.address: resource for resource in collect_resources(workspace / "absent", extra)}
        resources = []
        schemas: dict[str, Any] = {}
        for candidate in plan.candidates:
            rtype = candidate.resource_type
            data = BODIES.get(rtype, {})
            if rtype == "gitlab_group":
                data = {**data, "path": candidate.entity.native["path"], "name": candidate.entity.native["name"]}
            resource = manual.get(candidate.flat_address)
            if resource is None:
                resource = Resource(rtype, candidate.flat_address.split(".", 1)[1], Body(OrderedDict((k, hcl_literal(v)) for k, v in data.items())), None, workspace / "generated.tf")
            resources.append(resource)
            def block(body):
                return {"attributes": {**{name: {"optional": True, "type": "string", **({"sensitive": True} if name in {"token", "secret", "authorization_header", "value"} else {})} for name in body.attributes}, "id": {"computed": True, "type": "string"}},
                        "block_types": {b.name: {"nesting_mode": "list", "block": block(b.body)} for b in body.blocks}}
            schemas[rtype] = {"block": block(resource.body)}
        return resources, ProviderSchema(provider.spec.source, schemas)

    def format(self, root):
        self.calls.append("format")

    def validate(self, root, *, disposable=False):
        self.calls.append("validate")

    def adoption_check(self, root, validation_inputs=None, *, disposable=False, known_behaviour=None):
        self.calls.append("adoption check")
        return {"imports": len(list((root / "imports.tf").read_text().split("import {") )) - 1, "no_op_resources": 0, "unsafe_changes": 0}

    def state_only_adoption_check(self, root, imports, validation_inputs=None, *, disposable=False, known_behaviour=None):
        self.calls.append("state-only adoption check")
        return {"imports": len(imports), "no_op_resources": len(imports), "unsafe_changes": 0}

    def state_addresses(self, root):
        self.calls.append("state list")
        return set()


class FakeForge:
    def __init__(self, model):
        self.model = model
    def discover(self, workspace):
        return self.model
