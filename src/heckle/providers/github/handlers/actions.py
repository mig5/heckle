from __future__ import annotations
from heckle.hcl.render import hcl_literal
from heckle.hcl.render import body_to_value, literal_string
from heckle.providers.github.model.helpers import get_or_create, strip_attrs, add_body_family


class ActionsHandlers:
    def _repository_actions_permissions(self, resource, import_id, repo, repository) -> None:
        actions = get_or_create(repository, "actions")
        config = strip_attrs(resource.body, {"repository"})
        if literal_string(config.attributes.get("allowed_actions")) == "":
            config.attributes.pop("allowed_actions", None)
        actions["permissions"] = body_to_value(config)
        add_body_family(self.model, resource, config)
        self.imports[resource.address].new_address = (
            f"module.repositories.github_actions_repository_permissions.this[{hcl_literal(repo)}]"
        )

    def _repository_actions_variable(self, resource, import_id, repo, repository) -> None:
        name = literal_string(resource.body.attributes.get("variable_name"))
        if not name and import_id and ":" in import_id:
            name = import_id.split(":", 1)[1]
        variables = get_or_create(get_or_create(repository, "actions"), "variables")
        config = strip_attrs(resource.body, {"repository", "variable_name"})
        variables[name or resource.name] = body_to_value(config)
        add_body_family(self.model, resource, config)
        key = f"{repo}/{name or resource.name}"
        self.imports[resource.address].new_address = (
            f"module.repositories.github_actions_variable.this[{hcl_literal(key)}]"
        )

    def _repository_secret(self, resource, import_id, repo, repository) -> None:
        rtype = resource.resource_type
        name = literal_string(resource.body.attributes.get("secret_name"))
        if not name and import_id and ":" in import_id:
            name = import_id.split(":", 1)[1]
        section = "actions" if rtype == "github_actions_secret" else "dependabot"
        secrets = get_or_create(get_or_create(repository, section), "secrets")
        config = strip_attrs(resource.body, {"repository", "secret_name", "value"})
        secrets[name or resource.name] = body_to_value(config)
        add_body_family(self.model, resource, config)
        key = f"{repo}/{name or resource.name}"
        self.imports[resource.address].new_address = (
            f"module.repositories.{rtype}.this[{hcl_literal(key)}]"
        )

    def _repository_environment(self, resource, import_id, repo, repository) -> None:
        environment = literal_string(resource.body.attributes.get("environment"))
        if not environment and import_id and ":" in import_id:
            environment = import_id.split(":", 1)[1].replace("??", ":")
        env = get_or_create(get_or_create(repository, "environments"), environment or resource.name)
        config = strip_attrs(resource.body, {"repository", "environment"})
        env["settings"] = body_to_value(config)
        add_body_family(self.model, resource, config)
        key = f"{repo}/{environment or resource.name}"
        self.imports[resource.address].new_address = (
            f"module.repositories.github_repository_environment.this[{hcl_literal(key)}]"
        )

    def _repository_environment_item(self, resource, import_id, repo, repository) -> None:
        rtype = resource.resource_type
        environment = literal_string(resource.body.attributes.get("environment"))
        name_attr = "variable_name" if rtype.endswith("_variable") else "secret_name"
        name = literal_string(resource.body.attributes.get(name_attr))
        if import_id:
            parts = import_id.replace("??", "\0").split(":")
            parts = [part.replace("\0", ":") for part in parts]
            if len(parts) >= 3:
                environment = environment or parts[1]
                name = name or parts[2]
        env = get_or_create(get_or_create(repository, "environments"), environment or "unknown")
        section = "variables" if rtype.endswith("_variable") else "secrets"
        collection = get_or_create(env, section)
        remove = {"repository", "environment", name_attr}
        if section == "secrets":
            remove.add("value")
        config = strip_attrs(resource.body, remove)
        collection[name or resource.name] = body_to_value(config)
        add_body_family(self.model, resource, config)
        key = f"{repo}/{environment or 'unknown'}/{name or resource.name}"
        self.imports[resource.address].new_address = (
            f"module.repositories.{rtype}.this[{hcl_literal(key)}]"
        )
