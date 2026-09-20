from __future__ import annotations

from heckle.hcl.render import hcl_literal

import json
from collections import OrderedDict
from pathlib import Path
from typing import Any, Mapping

from heckle.errors import GenerationError
from heckle.hcl.types import Body, NestedBlock, Resource
from heckle.providers.github.model.types import ImportRecord
from heckle.hcl.render import literal_string

REPOSITORY_UNMANAGED_ATTRIBUTES = {
    "private",
    "fork",
    "source_owner",
    "source_repo",
    "auto_init",
    "license_template",
    "gitignore_template",
    "template",
    "archive_on_destroy",
    "vulnerability_alerts",
    "ignore_vulnerability_alerts_during_read",
    "default_branch",
    "has_downloads",
    "full_name",
    "html_url",
    "ssh_clone_url",
    "svn_url",
    "git_clone_url",
    "http_clone_url",
    "etag",
    "primary_language",
    "node_id",
    "repo_id",
}

REPOSITORY_API_ATTRIBUTES = OrderedDict(
    [
        ("description", "description"),
        ("homepage_url", "homepage"),
        ("visibility", "visibility"),
        ("has_issues", "has_issues"),
        ("has_discussions", "has_discussions"),
        ("has_projects", "has_projects"),
        ("has_wiki", "has_wiki"),
        ("is_template", "is_template"),
        ("allow_merge_commit", "allow_merge_commit"),
        ("allow_squash_merge", "allow_squash_merge"),
        ("allow_rebase_merge", "allow_rebase_merge"),
        ("allow_auto_merge", "allow_auto_merge"),
        ("allow_forking", "allow_forking"),
        ("merge_commit_title", "merge_commit_title"),
        ("merge_commit_message", "merge_commit_message"),
        ("squash_merge_commit_title", "squash_merge_commit_title"),
        ("squash_merge_commit_message", "squash_merge_commit_message"),
        ("delete_branch_on_merge", "delete_branch_on_merge"),
        ("web_commit_signoff_required", "web_commit_signoff_required"),
        ("archived", "archived"),
        ("topics", "topics"),
        ("allow_update_branch", "allow_update_branch"),
    ]
)

def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def repository_config_from_inventory(
    repo: str, inventory: Path, generated: Body
) -> Body:
    """Return stable ongoing settings for an already-existing repository.

    Terraform/OpenTofu config generation can omit explicit false/default values and can
    include create-only fork fields. The repository REST response is a better
    source for ordinary mutable settings, while generated nested blocks are
    retained for settings such as security_and_analysis and Pages.
    """
    details = read_json(inventory / "repositories" / repo / "repository.json", {}) or {}
    config = generated.copy()
    for attr in REPOSITORY_UNMANAGED_ATTRIBUTES:
        config.attributes.pop(attr, None)
    # The pages block on github_repository is deprecated. Pages, when enabled,
    # are represented by a separate github_repository_pages resource.
    config.blocks = [block for block in config.blocks if block.name != "pages"]

    for attr, api_key in REPOSITORY_API_ATTRIBUTES.items():
        if api_key in details:
            config.attributes[attr] = hcl_literal(details.get(api_key))

    visibility = details.get("visibility")
    if visibility == "public":
        # This setting only applies to private/internal organization repos.
        config.attributes.pop("allow_forking", None)

    if details.get("allow_merge_commit") is not True:
        config.attributes.pop("merge_commit_title", None)
        config.attributes.pop("merge_commit_message", None)
    if details.get("allow_squash_merge") is not True:
        config.attributes.pop("squash_merge_commit_title", None)
        config.attributes.pop("squash_merge_commit_message", None)

    return config

def repository_pages_config_from_inventory(details: Mapping[str, Any]) -> Body | None:
    """Build non-deprecated github_repository_pages configuration."""
    if not details:
        return None

    attributes: "OrderedDict[str, str]" = OrderedDict()
    blocks: list[NestedBlock] = []

    build_type = details.get("build_type") or "legacy"
    attributes["build_type"] = hcl_literal(build_type)

    cname = details.get("cname")
    if cname:
        attributes["cname"] = hcl_literal(cname)

    if isinstance(details.get("public"), bool):
        attributes["public"] = hcl_literal(details["public"])

    # The provider only accepts https_enforced when cname is configured.
    if cname and isinstance(details.get("https_enforced"), bool):
        attributes["https_enforced"] = hcl_literal(details["https_enforced"])

    source = details.get("source")
    if build_type == "legacy" and isinstance(source, Mapping) and source.get("branch"):
        source_attrs: "OrderedDict[str, str]" = OrderedDict()
        source_attrs["branch"] = hcl_literal(source["branch"])
        source_attrs["path"] = hcl_literal(source.get("path") or "/")
        blocks.append(
            NestedBlock(
                name="source",
                labels=(),
                body=Body(attributes=source_attrs),
                raw="",
            )
        )

    # Legacy Pages requires an explicit source. If GitHub did not return one,
    # avoid generating an invalid resource and record only the API snapshot.
    if build_type == "legacy" and not blocks:
        return None

    return Body(attributes=attributes, blocks=blocks)

def load_identity_maps(inventory: Path) -> dict[str, Any]:
    repos = read_json(inventory / "repositories.json", []) or []
    active = [r for r in repos if not r.get("archived", False)]
    repo_by_name = {str(r["name"]): r for r in active}
    repo_id_to_name: dict[str, str] = {}
    repo_node_to_name: dict[str, str] = {}
    for name, summary in repo_by_name.items():
        details = read_json(
            inventory / "repositories" / name / "repository.json", summary
        )
        for candidate in (summary, details):
            if candidate.get("id") is not None:
                repo_id_to_name[str(candidate["id"])] = name
            if candidate.get("node_id"):
                repo_node_to_name[str(candidate["node_id"])] = name

    teams = read_json(inventory / "teams.json", []) or []
    team_id_to_slug = {str(t["id"]): str(t["slug"]) for t in teams}
    team_slug_to_id = {str(t["slug"]): str(t["id"]) for t in teams}
    team_node_to_slug = {
        str(t["node_id"]): str(t["slug"]) for t in teams if t.get("node_id")
    }
    return {
        "repo_by_name": repo_by_name,
        "repo_id_to_name": repo_id_to_name,
        "repo_node_to_name": repo_node_to_name,
        "team_id_to_slug": team_id_to_slug,
        "team_slug_to_id": team_slug_to_id,
        "team_node_to_slug": team_node_to_slug,
    }

def repository_from_value(
    value: str | None, identities: Mapping[str, Any]
) -> str | None:
    if value is None:
        return None
    if value in identities["repo_by_name"]:
        return value
    if value in identities["repo_id_to_name"]:
        return identities["repo_id_to_name"][value]
    if value in identities["repo_node_to_name"]:
        return identities["repo_node_to_name"][value]
    if "/" in value:
        candidate = value.rsplit("/", 1)[-1]
        if candidate in identities["repo_by_name"]:
            return candidate
    return None

def import_by_address(records: Sequence[ImportRecord]) -> dict[str, ImportRecord]:
    return {record.old_address: record for record in records}

def resource_import_id(
    resource: Resource, imports: Mapping[str, ImportRecord]
) -> str | None:
    record = imports.get(resource.address)
    return record.import_id if record else None

def classify_repository_resource(
    resource: Resource,
    import_id: str | None,
    identities: Mapping[str, Any],
) -> str | None:
    if resource.resource_type == "github_repository":
        return literal_string(resource.body.attributes.get("name")) or import_id
    for attr in ("repository", "repository_id"):
        value = literal_string(resource.body.attributes.get(attr))
        repo = repository_from_value(value, identities)
        if repo:
            return repo
    if import_id:
        first = import_id.split(":", 1)[0].split("/", 1)[0]
        repo = repository_from_value(first, identities)
        if repo:
            return repo
    return None

def prepare_long_ruleset_name(body: Body) -> tuple[Body, bool, str | None]:
    name = literal_string(body.attributes.get("name"))
    if name is None or len(name) <= 100:
        return body, False, None
    shortened = name[:97].rstrip() + "..."
    result = body.copy()
    result.attributes["name"] = hcl_literal(shortened)
    result.attributes["observed_name"] = hcl_literal(name)
    return result, True, name

