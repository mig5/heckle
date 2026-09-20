"""Reusable Gitea-family API discovery, with distinct public forge handlers."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import quote

from heckle.core.model import Entity, ForgeModel, Observation
from heckle.errors import GenerationError
from heckle.forges.base import ForgeAdapter


def segment(value: str) -> str:
    return quote(value, safe="")


class GiteaFamilyForge(ForgeAdapter):
    def discover(self, workspace: Path) -> ForgeModel:
        self.resolve_source()
        owner = self.connection.source.scope
        if self.connection.source.namespace_type == "user":
            namespace = self.model.add(Entity("namespace", owner, str(self.user["id"]), "user_namespace", self.user, owner=owner))
            path = "/user/repos" if self.is_self else f"/users/{segment(owner)}/repos"
            rows = self.collection(owner + ":repositories", path, optional=False)
            repositories = self.owned_repositories(rows)
            self.model.observations.append(Observation(owner + ":organization", "not_applicable", detail="Personal namespace: no organization, teams or organization memberships"))
        else:
            namespace = self._organization()
            repositories = self.collection(owner + ":repositories", f"/orgs/{segment(owner)}/repos", optional=False)
        with ThreadPoolExecutor(max_workers=self.connection.workers) as executor:
            futures = [executor.submit(self._repository, namespace, summary) for summary in repositories or []]
            for future in futures:
                future.result()
        version = self.detail("instance version", "/version", optional=True)
        self.model.extensions[self.connection.source.forge] = {"version": (version or {}).get("version")}
        return self.model

    def _organization(self) -> Entity:
        org = self.connection.source.scope
        data = self.detail("organization", f"/orgs/{segment(org)}")
        canonical = data.get("name") or data.get("username") or org
        if str(canonical).casefold() != org.casefold():
            raise GenerationError("Organization returned by the API does not match the requested scope")
        namespace = self.model.add(Entity("namespace", org, str(data["id"]), "organization", data, owner=org))
        members = self.collection(org + ":members", f"/orgs/{segment(org)}/members")
        for member in members or []:
            self._child(namespace, "organization_member", "membership", member, member.get("login") or member["username"])
        teams = self.collection(org + ":teams", f"/orgs/{segment(org)}/teams")
        for team_data in teams or []:
            team = self._child(namespace, "team", "team", team_data, team_data["name"])
            team.native["_members_complete"] = False
            team.native["_repositories_complete"] = False
            team_members = self.collection(team.key + ":members", f"/teams/{team.remote_id}/members")
            if team_members is not None:
                team.native["_members_complete"] = True
                team.native["_members"] = sorted(m.get("login") or m["username"] for m in team_members)
                for member in team_members:
                    self._child(team, "team_member", "membership", member, member.get("login") or member["username"])
            repositories = self.collection(team.key + ":repositories", f"/teams/{team.remote_id}/repos")
            if repositories is not None:
                team.native["_repositories_complete"] = True
                team.native["_repositories"] = sorted(r["name"] for r in repositories)
                for repository in repositories:
                    self._child(team, "team_repository", "grant", repository, repository["name"])
        for endpoint, native_kind, kind in (
            ("hooks", "organization_webhook", "webhook"),
            ("actions/variables", "organization_variable", "variable"),
            ("actions/secrets", "organization_secret", "secret"),
        ):
            rows = self.collection(org + ":" + endpoint, f"/orgs/{segment(org)}/{endpoint}", variable=kind in {"variable", "secret"}, key=endpoint.rsplit("/", 1)[-1] if endpoint.startswith("actions/") else None)
            for item in rows or []:
                self._child(namespace, native_kind, kind, item, item.get("id") or item.get("name"))
        return namespace

    def _child(self, parent: Entity, native_kind: str, kind: str, data: dict[str, Any], identity: Any) -> Entity:
        if identity is None:
            raise GenerationError(f"Missing identity in {native_kind}")
        return self.model.add(Entity(kind, f"{parent.key}/{identity}", str(data.get("id", identity)), native_kind,
                                     {**data, "_parent_id": parent.remote_id, "_parent_path": parent.key},
                                     parent.uid, parent.owner or parent.key))

    def _repository(self, namespace: Entity, summary: dict[str, Any]) -> None:
        owner = namespace.key
        name = summary["name"]
        prefix = f"/repos/{segment(owner)}/{segment(name)}"
        data = summary if summary.get("archived") else self.detail(owner + "/" + name, prefix)
        actual_owner = (data.get("owner") or {}).get("login") or (data.get("owner") or {}).get("username")
        if not actual_owner or actual_owner.casefold() != owner.casefold():
            raise GenerationError("Repository owner changed during discovery")
        repository = self.model.add(Entity("repository", f"{owner}/{name}", str(data["id"]), "repository", data,
                                           namespace.uid, f"{owner}/{name}"))
        if data.get("archived"):
            self.model.observations.append(Observation(repository.key, "skipped", 1, "Archived repository"))
            return
        for endpoint, native_kind, kind in (
            ("branch_protections", "branch_protection", "branch_protection"),
            ("hooks", "repository_webhook", "webhook"),
            ("keys", "deploy_key", "deploy_key"),
            ("collaborators", "collaborator", "grant"),
            ("labels", "repository_label", "label"),
            ("actions/variables", "repository_variable", "variable"),
            ("actions/secrets", "repository_secret", "secret"),
        ):
            rows = self.collection(repository.key + ":" + endpoint, prefix + "/" + endpoint, variable=kind in {"variable", "secret"}, key=endpoint.rsplit("/", 1)[-1] if endpoint.startswith("actions/") else None)
            for item in rows or []:
                identity = item.get("rule_name") or item.get("branch_name") if kind == "branch_protection" else item.get("id") or item.get("name") or item.get("login")
                self._child(repository, native_kind, kind, item, identity)
