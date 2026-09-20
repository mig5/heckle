from __future__ import annotations
from heckle.hcl.render import hcl_literal
from heckle.errors import GenerationError
from heckle.hcl.render import body_to_value, literal_list, literal_string
from heckle.compatibility import unmanaged_attributes, quirks_for
from heckle.hcl.types import Resource
from heckle.providers.github.model.inventory import prepare_long_ruleset_name
from heckle.providers.github.model.helpers import (
    get_or_create,
    webhook_variable_for,
    replace_webhook_configuration_url,
    add_body_family,
)


class OrganizationHandlers:
    def _organization_resource(self, resource: Resource, import_id: str | None) -> bool:
        rtype = resource.resource_type
        organization = self.model.organization
        if rtype == "github_organization_settings":
            config = resource.body.copy()
            unmanaged = unmanaged_attributes(self.provider_spec, rtype)
            for name in unmanaged:
                config.attributes.pop(name, None)
            if unmanaged:
                self.model.report.setdefault("compatibility_notes", []).extend(
                    quirk.summary for quirk in quirks_for(self.provider_spec, rtype)
                    if quirk.kind == "unmanaged_attributes"
                )
            organization["settings"] = body_to_value(config)
            add_body_family(self.model, resource, config)
            self.imports[resource.address].new_address = (
                "module.organization.github_organization_settings.this"
            )
            return True

        if rtype in self.ORG_SINGLETON_NAMES:
            key = self.ORG_SINGLETON_NAMES[rtype]
            config = resource.body.copy()
            if rtype == "github_actions_organization_permissions":
                removed_empty_patterns = False
                for block in config.blocks:
                    if block.name != "allowed_actions_config":
                        continue
                    if literal_list(block.body.attributes.get("patterns_allowed")) == []:
                        block.body.attributes.pop("patterns_allowed", None)
                        removed_empty_patterns = True
                if removed_empty_patterns:
                    self.model.report.setdefault("compatibility_notes", []).extend(
                        quirk.summary for quirk in quirks_for(self.provider_spec, rtype)
                        if quirk.id == "github-actions-empty-patterns"
                    )
            organization[key] = body_to_value(config)
            add_body_family(self.model, resource, config)
            self.imports[resource.address].new_address = f"module.organization.{rtype}.this"
            return True

        collection_name = self.ORG_COLLECTION_NAMES.get(rtype)
        if collection_name is None:
            return False
        collection = get_or_create(organization, collection_name)
        config = resource.body.copy()

        if "secret" in rtype:
            key = literal_string(config.attributes.get("secret_name")) or str(import_id or resource.name)
            config.attributes.pop("secret_name", None)
            config.attributes.pop("value", None)
        elif rtype == "github_actions_organization_variable":
            key = literal_string(config.attributes.get("variable_name")) or str(import_id or resource.name)
            config.attributes.pop("variable_name", None)
        elif rtype == "github_organization_webhook":
            key = str(import_id or resource.name)
            configuration_blocks = [block for block in config.blocks if block.name == "configuration"]
            if not configuration_blocks:
                raise GenerationError(f"organization webhook {key} has no configuration block")
            url = literal_string(configuration_blocks[0].body.attributes.get("url"))
            if not url:
                raise GenerationError(
                    f"organization webhook {key} has no literal URL to map to a sensitive input variable"
                )
            variable = webhook_variable_for(
                self.model, scope="organization", hook_id=key, url=url
            )
            replace_webhook_configuration_url(config, variable.name)
        elif rtype == "github_organization_custom_properties":
            key = literal_string(config.attributes.get("property_name")) or str(import_id or resource.name)
            config.attributes.pop("property_name", None)
        else:
            key = literal_string(config.attributes.get("name")) or str(import_id or resource.name)

        if rtype in {
            "github_actions_organization_secret_repositories",
            "github_dependabot_organization_secret_repositories",
        }:
            ids = literal_list(config.attributes.pop("selected_repository_ids", None))
            if ids is not None:
                repo_keys = [
                    self.identities["repo_id_to_name"].get(str(item), str(item))
                    for item in ids
                ]
                config.attributes["repository_keys"] = hcl_literal(repo_keys)

        legacy_ruleset_name = False
        if rtype == "github_organization_ruleset":
            config, legacy_ruleset_name, original_name = prepare_long_ruleset_name(config)
            if legacy_ruleset_name:
                config.attributes["manage_name"] = "false"
                self.long_rulesets.append(
                    {"organization": self.org, "name": original_name or key, "key": key}
                )
            else:
                config.attributes["manage_name"] = "true"

        collection[key] = body_to_value(config)
        provider_config = config.copy()
        for control in ("repository_keys", "observed_name", "manage_name"):
            provider_config.attributes.pop(control, None)
        add_body_family(self.model, resource, provider_config)
        resource_name = (
            "legacy_name"
            if rtype == "github_organization_ruleset" and legacy_ruleset_name
            else "this"
        )
        self.imports[resource.address].new_address = (
            f"module.organization.{rtype}.{resource_name}[{hcl_literal(key)}]"
        )
        return True
