from __future__ import annotations

import json
from pathlib import Path

from heckle.config import Connection
from heckle.core.model import ForgeModel, Observation
from heckle.errors import GenerationError
from heckle.forges.base import ForgeAdapter
from .api import GitHubClient
from .discovery import GitHubInventoryExporter
from .normalize import normalize


class GitHubForge(ForgeAdapter):
    def __init__(self, connection: Connection, client=None) -> None:
        super().__init__(connection, client or GitHubClient(connection))

    def discover(self, workspace: Path) -> ForgeModel:
        self.resolve_source()
        source = self.connection.source
        root = workspace / "github-native"
        personal = source.namespace_type == "user"
        repositories = None
        if personal:
            params = {"sort": "full_name", "direction": "asc"}
            if self.is_self:
                repositories = self.client.get_paginated(
                    "/user/repos", params={**params, "affiliation": "owner", "visibility": "all"}
                )
            else:
                # /users/{name}/repos only lists public repositories. Merge private
                # collaborations from the authenticated endpoint, then filter owners.
                from .api import quote

                public = self.client.get_paginated(
                    f"/users/{quote(source.scope)}/repos", params=params
                )
                shared = self.client.get_paginated(
                    "/user/repos",
                    params={**params, "affiliation": "collaborator", "visibility": "all"},
                )
                repositories = public + shared
            repositories = self.owned_repositories(repositories)
        exporter = GitHubInventoryExporter(
            self.client,
            source.scope,
            root,
            workers=self.connection.workers,
            personal=personal,
            user=self.user,
            repositories=repositories,
        )
        exporter.run()
        inventory = root / "inventory"
        files = {
            str(path.relative_to(inventory)): json.loads(path.read_text(encoding="utf-8"))
            for path in inventory.rglob("*.json")
        }
        profile = files["user.json" if personal else "organization.json"]
        if profile.get("login", "").casefold() != source.scope.casefold():
            raise GenerationError("GitHub returned a different namespace than requested")
        observations = self.model.observations
        self.model = normalize(source, files)
        self.model.observations.extend(observations)
        if personal:
            self.model.observations.append(
                Observation(
                    source.scope + ":organization",
                    "not_applicable",
                    detail="Personal namespace: no organization, teams, organization memberships or organization custom properties",
                )
            )
        return self.model
