"""Only the organization's own roles are managed, not GitHub's or the enterprise's."""

from __future__ import annotations

from heckle.core.security import write_private_json
from heckle.providers.github.plan import GitHubPlanBuilder


def role(role_id, name, source, base_role="read"):
    item = {"id": role_id, "name": name, "base_role": base_role, "permissions": []}
    if source is not None:
        item["source"] = source
    return item


def test_predefined_and_enterprise_roles_are_not_managed(tmp_path):
    inventory = tmp_path / "inventory"
    files = {
        "organization.json": {"id": 1, "login": "example"},
        "repositories.json": [],
        "teams.json": [],
        "coverage.json": {"api_errors": [], "not_managed": []},
        "custom-organization-roles.json": [
            role(101, "Release managers", "Organization"),
            role(102, "Legacy role", None),  # older API: no source field
            role(8132, "all_repo_read", "Predefined"),
            role(8137, "all_repo_moderate", "Predefined", base_role="moderate"),
            role(9001, "Enterprise auditors", "Enterprise"),
        ],
    }
    for relative, value in files.items():
        write_private_json(inventory / relative, value)

    builder = GitHubPlanBuilder("example").build(inventory)
    roles = {
        import_id
        for address, import_id in builder.imports
        if address.startswith("github_organization_role.")
    }
    assert roles == {"101", "102"}
    [note] = [n for n in builder.notes if "organization role(s)" in n]
    assert note.startswith("3 organization role(s) are defined by GitHub or the enterprise")
    assert "all_repo_moderate (Predefined)" in note
    assert "Enterprise auditors (Enterprise)" in note
