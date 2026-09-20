from __future__ import annotations
from heckle.hcl.render import hcl_literal
import hashlib
import json
import re
import urllib.parse
from collections import OrderedDict
from typing import Any, Iterable, Mapping, MutableMapping
from heckle.errors import GenerationError
from heckle.hcl.render import literal_string
from heckle.hcl.types import Body, Expr, Resource
from heckle.providers.github.model.types import Model, WebhookVariable

def get_or_create(
    mapping: MutableMapping[str, Any], key: str
) -> MutableMapping[str, Any]:
    value = mapping.get(key)
    if value is None:
        value = OrderedDict()
        mapping[key] = value
    if not isinstance(value, MutableMapping):
        raise GenerationError(f"model collision at {key}")
    return value

def unique_key(mapping: Mapping[str, Any], proposed: str, suffix: str) -> str:
    if proposed not in mapping:
        return proposed
    candidate = f"{proposed}#{suffix}"
    if candidate not in mapping:
        return candidate
    digest = hashlib.sha1(suffix.encode(), usedforsecurity=False).hexdigest()[:8]
    return f"{proposed}#{digest}"

def strip_attrs(body: Body, names: Iterable[str]) -> Body:
    result = body.copy()
    for name in names:
        result.attributes.pop(name, None)
    return result

def webhook_identifier(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()
    return cleaned or "webhook"

def webhook_service(url: str) -> str:
    """Derive a stable provider-neutral label from the webhook hostname."""
    hostname = (urllib.parse.urlparse(url).hostname or "").lower()
    if hostname:
        return webhook_identifier(hostname.removeprefix("www."))
    return "webhook"

def webhook_variable_for(
    model: Model, *, scope: str, hook_id: str, url: str
) -> WebhookVariable:
    """Return a deterministic, organization-agnostic variable for a webhook URL."""
    service = webhook_service(url)
    scope_name = "organization" if scope == "organization" else webhook_identifier(scope)
    name = f"webhook_{scope_name}_{service}_{hook_id}"
    description = (
        f"Sensitive URL for {scope} webhook {hook_id} ({service}). "
        "Supply it through your normal Terraform/OpenTofu secret-input mechanism."
    )

    variable = WebhookVariable(
        name=name,
        description=description,
        scope=scope,
        hook_id=hook_id,
        value=url,
    )
    existing = model.webhook_variables.get(name)
    if existing is not None and existing != variable:
        raise GenerationError(
            f"webhook variable name collision for {name}: "
            f"{existing.scope}/{existing.hook_id} and {scope}/{hook_id}"
        )
    model.webhook_variables[name] = variable
    return variable

def replace_webhook_configuration_url(config: Body, variable_name: str) -> None:
    configurations = [block for block in config.blocks if block.name == "configuration"]
    if not configurations:
        raise GenerationError("webhook resource has no configuration block")
    for configuration in configurations:
        configuration.body.attributes["url"] = f"var.{variable_name}"

def add_body_family(model: Model, resource: Resource, body: Body | None = None) -> None:
    model.family(resource.resource_type).add(
        body or resource.body, resource.lifecycle_raw
    )

def model_string(value: Any) -> str | None:
    if isinstance(value, Expr):
        return literal_string(value.value)
    if isinstance(value, str):
        return value
    return None

def compute_team_levels(model: Model) -> dict[str, int]:
    parents: dict[str, str | None] = {}
    for slug, team in model.teams.items():
        settings = team.get("settings", {}) if isinstance(team, Mapping) else {}
        parent = (
            model_string(settings.get("parent_team_key"))
            if isinstance(settings, Mapping)
            else None
        )
        if parent and parent not in model.teams:
            raise GenerationError(
                f"team {slug!r} refers to unknown parent team {parent!r}"
            )
        parents[slug] = parent

    levels: dict[str, int] = {}
    visiting: list[str] = []

    def visit(slug: str) -> int:
        if slug in levels:
            return levels[slug]
        if slug in visiting:
            cycle = visiting[visiting.index(slug) :] + [slug]
            raise GenerationError("team parent cycle: " + " -> ".join(cycle))
        visiting.append(slug)
        parent = parents[slug]
        level = 0 if parent is None else visit(parent) + 1
        visiting.pop()
        levels[slug] = level
        return level

    for slug in parents:
        visit(slug)
    return levels

def rewrite_team_import_levels(model: Model, levels: Mapping[str, int]) -> None:
    pattern = re.compile(r"^module\.teams\.github_team\.this\[(?P<key>.+)\]$")
    for record in model.imports:
        if not record.new_address:
            continue
        match = pattern.match(record.new_address)
        if not match:
            continue
        try:
            slug = json.loads(match.group("key"))
        except json.JSONDecodeError as exc:
            raise GenerationError(
                f"invalid team import address: {record.new_address}"
            ) from exc
        record.new_address = (
            f"module.teams.github_team.level_{levels[str(slug)]}"
            f"[{hcl_literal(str(slug))}]"
        )
