"""Portable GitHub identities plus lossless, namespaced native API snapshots."""
from __future__ import annotations

from typing import Any
from heckle.core.model import Entity, ForgeModel, Observation, Source


def normalize(source: Source, files: dict[str, Any]) -> ForgeModel:
    model = ForgeModel(source)
    kind = "user_namespace" if source.namespace_type == "user" else "organization"
    profile = files["user.json" if source.namespace_type == "user" else "organization.json"]
    namespace = model.add(Entity("namespace", source.scope, str(profile["id"]), kind, profile, owner=source.scope))
    for repo in files.get("repositories.json", []):
        key = f"{source.scope}/{repo['name']}"
        details = files.get(f"repositories/{repo['name']}/repository.json", repo)
        model.add(Entity("repository", key, str(repo["id"]), "repository", details, namespace.uid, key))
    for team in files.get("teams.json", []):
        model.add(Entity("team", f"{source.scope}/{team['slug']}", str(team["id"]), "team", team, namespace.uid, source.scope))
    for membership in files.get("memberships.json", []):
        user = membership.get("user") or {}
        username = user.get("login") or membership.get("_username")
        if username:
            model.add(Entity("membership", username, str(user.get("id", username)), "membership", membership, namespace.uid, source.scope))
    for error in files.get("coverage.json", {}).get("api_errors", []):
        status = error.get("http_status")
        model.observations.append(Observation(error["scope"], "permission_denied" if status in {401, 403} else "unavailable" if status == 404 else "failed", detail=error["error"], http_status=status))
    model.extensions["github"] = {"files": files}
    return model
