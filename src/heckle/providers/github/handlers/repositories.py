from __future__ import annotations
from heckle.hcl.render import hcl_literal
from collections import OrderedDict
from typing import Any, MutableMapping
from heckle.errors import GenerationError
from heckle.hcl.render import body_to_value, literal_string
from heckle.hcl.types import Body, Resource
from heckle.providers.github.model.inventory import (
    prepare_long_ruleset_name,
    read_json,
    repository_config_from_inventory,
    repository_pages_config_from_inventory,
)
from heckle.providers.github.model.types import ImportRecord
from heckle.providers.github.model.helpers import (
    get_or_create,
    unique_key,
    strip_attrs,
    add_body_family,
)


class RepositoryHandlers:
    def _repository_settings(
        self,
        resource: Resource,
        import_id: str | None,
        repo: str,
        repository: MutableMapping[str, Any],
    ) -> None:
        generated_config = strip_attrs(resource.body, {"name"})
        config = repository_config_from_inventory(repo, self.inventory, generated_config)
        repository["settings"] = body_to_value(config)
        add_body_family(self.model, resource, config)
        self.imports[resource.address].new_address = (
            f"module.repositories.github_repository.this[{hcl_literal(repo)}]"
        )

        details = read_json(self.inventory / "repositories" / repo / "repository.json", {}) or {}
        default_branch = details.get("default_branch")
        if default_branch:
            branch_config = Body(
                attributes=OrderedDict(
                    [("branch", hcl_literal(default_branch)), ("rename", "false")]
                )
            )
            repository["default_branch"] = body_to_value(branch_config)
            self.model.family("github_branch_default").add(branch_config, None)
            self.model.imports.append(
                ImportRecord(
                    old_address=f"synthetic.github_branch_default.{repo}",
                    import_id=repo,
                    new_address=(
                        "module.repositories.github_branch_default.this" f"[{hcl_literal(repo)}]"
                    ),
                )
            )

        pages_details = read_json(self.inventory / "repositories" / repo / "pages.json", None)
        pages_config = repository_pages_config_from_inventory(pages_details or {})
        if pages_config is not None:
            repository["pages"] = body_to_value(pages_config)
            self.model.family("github_repository_pages").add(pages_config, None)
            self.model.imports.append(
                ImportRecord(
                    old_address=f"synthetic.github_repository_pages.{repo}",
                    import_id=repo,
                    new_address=(
                        "module.repositories.github_repository_pages.this" f"[{hcl_literal(repo)}]"
                    ),
                )
            )

    def _repository_collaborator(self, resource, import_id, repo, repository) -> None:
        username = literal_string(resource.body.attributes.get("username"))
        if not username and import_id and ":" in import_id:
            username = import_id.split(":", 1)[1]
        if not username:
            raise GenerationError(f"cannot identify collaborator {resource.address}")
        collection = get_or_create(repository, "collaborators")
        config = strip_attrs(resource.body, {"repository", "username"})
        collection[username] = body_to_value(config)
        add_body_family(self.model, resource, config)
        key = f"{repo}/{username}"
        self.imports[resource.address].new_address = (
            f"module.repositories.github_repository_collaborator.this[{hcl_literal(key)}]"
        )

    def _repository_branch_protection(self, resource, import_id, repo, repository) -> None:
        pattern = literal_string(resource.body.attributes.get("pattern"))
        if not pattern and import_id and ":" in import_id:
            pattern = import_id.split(":", 1)[1]
        if not pattern:
            raise GenerationError(f"cannot identify branch protection {resource.address}")
        collection = get_or_create(repository, "branch_protections")
        config = strip_attrs(resource.body, {"repository_id", "pattern"})
        collection[pattern] = body_to_value(config)
        add_body_family(self.model, resource, config)
        key = f"{repo}/{pattern}"
        self.imports[resource.address].new_address = (
            f"module.repositories.github_branch_protection.this[{hcl_literal(key)}]"
        )

    def _repository_ruleset(self, resource, import_id, repo, repository) -> None:
        name = literal_string(resource.body.attributes.get("name")) or str(
            import_id or resource.name
        )
        ruleset_id = import_id.split(":")[-1] if import_id else resource.name
        collection = get_or_create(repository, "rulesets")
        key_name = unique_key(collection, name, ruleset_id)
        config = strip_attrs(resource.body, {"repository"})
        config, legacy_name, original_name = prepare_long_ruleset_name(config)
        if legacy_name:
            config.attributes["manage_name"] = "false"
            self.long_rulesets.append(
                {"repository": repo, "name": original_name or name, "key": key_name}
            )
        else:
            config.attributes["manage_name"] = "true"
        collection[key_name] = body_to_value(config)
        provider_config = config.copy()
        provider_config.attributes.pop("observed_name", None)
        provider_config.attributes.pop("manage_name", None)
        add_body_family(self.model, resource, provider_config)
        key = f"{repo}/{key_name}"
        resource_name = "legacy_name" if legacy_name else "this"
        self.imports[resource.address].new_address = (
            f"module.repositories.github_repository_ruleset.{resource_name}[{hcl_literal(key)}]"
        )

    def _repository_deploy_key(self, resource, import_id, repo, repository) -> None:
        keys = get_or_create(repository, "deploy_keys")
        key_id = import_id.split(":", 1)[1] if import_id and ":" in import_id else resource.name
        config = strip_attrs(resource.body, {"repository"})
        keys[key_id] = body_to_value(config)
        add_body_family(self.model, resource, config)
        key = f"{repo}/{key_id}"
        self.imports[resource.address].new_address = (
            f"module.repositories.github_repository_deploy_key.this[{hcl_literal(key)}]"
        )

    def _repository_autolink(self, resource, import_id, repo, repository) -> None:
        links = get_or_create(repository, "autolinks")
        key_prefix = literal_string(resource.body.attributes.get("key_prefix"))
        link_id = import_id.rsplit("/", 1)[-1] if import_id else resource.name
        map_key = unique_key(links, key_prefix or link_id, link_id)
        config = strip_attrs(resource.body, {"repository"})
        links[map_key] = body_to_value(config)
        add_body_family(self.model, resource, config)
        key = f"{repo}/{map_key}"
        self.imports[resource.address].new_address = (
            f"module.repositories.github_repository_autolink_reference.this[{hcl_literal(key)}]"
        )

    def _repository_custom_property(self, resource, import_id, repo, repository) -> None:
        properties = get_or_create(repository, "custom_properties")
        prop = literal_string(resource.body.attributes.get("property_name"))
        if not prop and import_id:
            prop = import_id.rsplit(":", 1)[-1]
        config = strip_attrs(resource.body, {"repository", "property_name"})
        properties[prop or resource.name] = body_to_value(config)
        add_body_family(self.model, resource, config)
        key = f"{repo}/{prop or resource.name}"
        self.imports[resource.address].new_address = (
            f"module.repositories.github_repository_custom_property.this[{hcl_literal(key)}]"
        )
