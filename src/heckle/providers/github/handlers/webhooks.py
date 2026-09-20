from __future__ import annotations
from heckle.hcl.render import hcl_literal
from heckle.errors import GenerationError
from heckle.hcl.render import body_to_value, literal_string
from heckle.providers.github.model.helpers import (
    get_or_create,
    strip_attrs,
    webhook_variable_for,
    replace_webhook_configuration_url,
    add_body_family,
)


class WebhookHandlers:
    def _repository_webhook(self, resource, import_id, repo, repository) -> None:
        hooks = get_or_create(repository, "webhooks")
        hook_id = import_id.rsplit("/", 1)[-1] if import_id else resource.name
        config = strip_attrs(resource.body, {"repository"})
        configuration_blocks = [block for block in config.blocks if block.name == "configuration"]
        if not configuration_blocks:
            raise GenerationError(f"repository webhook {repo}/{hook_id} has no configuration block")
        url = literal_string(configuration_blocks[0].body.attributes.get("url"))
        if not url:
            raise GenerationError(
                f"repository webhook {repo}/{hook_id} has no literal URL to map to a sensitive input variable"
            )
        variable = webhook_variable_for(self.model, scope=repo, hook_id=hook_id, url=url)
        replace_webhook_configuration_url(config, variable.name)
        hooks[hook_id] = body_to_value(config)
        add_body_family(self.model, resource, config)
        key = f"{repo}/{hook_id}"
        self.imports[resource.address].new_address = (
            f"module.repositories.github_repository_webhook.this[{hcl_literal(key)}]"
        )
