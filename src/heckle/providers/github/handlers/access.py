from __future__ import annotations
from heckle.hcl.render import hcl_literal
from heckle.errors import GenerationError
from heckle.hcl.render import body_to_value, literal_int, literal_string, safe_identifier
from heckle.hcl.types import Resource
from heckle.providers.github.model.helpers import get_or_create, strip_attrs, add_body_family


class AccessHandlers:
    def _membership(self, resource: Resource, import_id: str | None) -> None:
        username = literal_string(resource.body.attributes.get("username"))
        if not username and import_id and ":" in import_id:
            username = import_id.split(":", 1)[1]
        if not username:
            raise GenerationError(f"cannot identify username for {resource.address}")
        config = strip_attrs(resource.body, {"username"})
        self.model.members[username] = body_to_value(config)
        add_body_family(self.model, resource, config)
        self.imports[resource.address].new_address = (
            f"module.members.github_membership.this[{hcl_literal(username)}]"
        )

    def _team_resource(self, resource: Resource, import_id: str | None) -> None:
        rtype = resource.resource_type
        if rtype == "github_team":
            team_id = import_id or str(literal_int(resource.body.attributes.get("id")) or "")
            slug = self.identities["team_id_to_slug"].get(team_id)
            if not slug:
                slug = safe_identifier(
                    literal_string(resource.body.attributes.get("name")) or resource.name
                )
            team = get_or_create(self.model.teams, slug)
            config = resource.body.copy()
            parent_id = literal_int(config.attributes.pop("parent_team_id", None))
            if parent_id is not None:
                parent_slug = self.identities["team_id_to_slug"].get(str(parent_id))
                if parent_slug:
                    config.attributes["parent_team_key"] = hcl_literal(parent_slug)
            team["settings"] = body_to_value(config)
            add_body_family(self.model, resource, config)
            self.imports[resource.address].new_address = (
                f"module.teams.github_team.this[{hcl_literal(slug)}]"
            )
            return

        if rtype == "github_team_membership":
            team_id = literal_string(resource.body.attributes.get("team_id"))
            username = literal_string(resource.body.attributes.get("username"))
            if import_id and ":" in import_id:
                imported_team, imported_user = import_id.split(":", 1)
                team_id = team_id or imported_team
                username = username or imported_user
            if not team_id or not username:
                raise GenerationError(f"cannot identify team membership {resource.address}")
            slug = self.identities["team_id_to_slug"].get(str(team_id), str(team_id))
            members = get_or_create(get_or_create(self.model.teams, slug), "members")
            config = strip_attrs(resource.body, {"team_id", "username"})
            members[username] = body_to_value(config)
            add_body_family(self.model, resource, config)
            key = f"{slug}/{username}"
            self.imports[resource.address].new_address = (
                f"module.teams.github_team_membership.this[{hcl_literal(key)}]"
            )
            return

        team_id = literal_string(resource.body.attributes.get("team_id"))
        repository = literal_string(resource.body.attributes.get("repository"))
        if import_id and ":" in import_id:
            imported_team, imported_repo = import_id.split(":", 1)
            team_id = team_id or imported_team
            repository = repository or imported_repo
        if not team_id or not repository:
            raise GenerationError(f"cannot identify team repository grant {resource.address}")
        slug = self.identities["team_id_to_slug"].get(str(team_id), str(team_id))
        repositories = get_or_create(get_or_create(self.model.teams, slug), "repositories")
        config = strip_attrs(resource.body, {"team_id", "repository"})
        repositories[repository] = body_to_value(config)
        add_body_family(self.model, resource, config)
        key = f"{slug}/{repository}"
        self.imports[resource.address].new_address = (
            f"module.access.github_team_repository.this[{hcl_literal(key)}]"
        )
