"""The optional repair tool changes local HCL only; tests never contact a forge."""
import importlib.util
from pathlib import Path

import pytest

from heckle.core.compilation import CompiledProject, CompiledResource, Family
from heckle.providers.forgejo.repository import IGNORED_FIELDS, PERSONAL_CREATION_WARNING
from heckle.hcl.render import emit_config_fields
from test_forgejo_repository import compile_repositories

TOOL_PATH = Path(__file__).resolve().parents[1] / "tools/repair_forgejo_alpha3.py"
spec = importlib.util.spec_from_file_location("repair_forgejo_alpha3", TOOL_PATH)
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)


@pytest.fixture
def generated(tmp_path):
    provider, compiled, resources, _ = compile_repositories(tmp_path, [
        {"has_pull_requests": False, "has_wiki": False}, {},
    ])
    # Reproduce the old compiler: only this incomplete set was ignored. Everything
    # else (including invalid import defaults) was forwarded to the module.
    legacy_ignored = {"auto_init", "clone_addr", "gitignores", "license", "readme"}
    old = CompiledProject(compiled.plan)
    for candidate, resource in zip(compiled.plan.candidates, resources):
        body = resource.body.copy()
        for name in legacy_ignored:
            body.attributes.pop(name, None)
        old.resources.append(CompiledResource(candidate, body, legacy_ignored))
    root = tmp_path / "generated project"
    provider.render(old, root, prevent_destroy=True, split_teams=False)
    # Replace the current presence-aware module with the exact alpha3-style
    # single var.keys resource this one-time repair utility targets.
    family = Family("forgejo_repository")
    for item in old.resources:
        family.add(item.body, None)
    lines = [
        'variable "items" {', '  type = any', '}', '',
        'variable "keys" {', '  type = set(string)', '}', '',
        'resource "forgejo_repository" "this" {', '  for_each = var.keys', '',
    ]
    lines.extend(emit_config_fields(family, "var.items[each.key]", 2))
    lines.extend([
        '', '  lifecycle {', '    prevent_destroy = true',
        '    ignore_changes = [auto_init, clone_addr, gitignores, license, readme]',
        '  }', '}', '',
    ])
    (root / "modules/forgejo_repository/main.tf").write_text("\n".join(lines))
    (root / "README.md").write_text(
        "# Existing project\n\n**Forgejo creation caveat:** the pinned svalabs/forgejo "
        "1.6.0 provider uses AdminCreateRepo for an explicitly named personal owner.\n\n"
        "Keep this unrelated documentation.\n"
    )
    (root / "backend.tf").write_text('terraform { backend "local" {} }\n')
    (root / "private.tfvars").write_text('# Private fixture; must not be read or rewritten.\n')
    (root / "terraform.tfstate").write_text('test-state-sentinel')
    return root


def test_preview_does_not_write(generated, capsys):
    before = {p: p.read_bytes() for p in generated.rglob("*") if p.is_file()}
    assert repair.main([str(generated)]) == 0
    assert before == {p: p.read_bytes() for p in generated.rglob("*") if p.is_file()}
    output = capsys.readouterr().out
    assert "Preview only" in output
    assert "mirror_interval" in output and "server-admin" in output


def test_repairs_module_and_warning_without_state_changes(generated, capsys):
    before = {p.relative_to(generated): p.read_bytes() for p in generated.rglob("*") if p.is_file()}
    assert repair.main([str(generated), "--write"]) == 0
    main = generated / "modules/forgejo_repository/main.tf"
    text = main.read_text()
    # Only the top-level attribute assignment is removed; ignoring the imported
    # field is intentional and is NOT equivalent to configuring false or "".
    for name in IGNORED_FIELDS:
        assert not any(line.strip().startswith(name + " =") for line in text.splitlines())
    assert "try(var.items[each.key].has_pull_requests, null) == false ? null" in text
    assert "prevent_destroy = true" in text
    assert "mirror_interval" in text.split("ignore_changes =", 1)[1]
    assert main.with_name("main.tf.before-0.1.0-alpha4").read_bytes() == before[main.relative_to(generated)]
    assert PERSONAL_CREATION_WARNING in (generated / "README.md").read_text()
    assert "Keep this unrelated documentation." in (generated / "README.md").read_text()
    for relative, contents in before.items():
        if str(relative) not in {"modules/forgejo_repository/main.tf", "README.md"}:
            assert (generated / relative).read_bytes() == contents
    # Running twice never clobbers the original backup.
    assert repair.main([str(generated), "--write"]) == 0
    assert "Already repaired" in capsys.readouterr().out


def test_existing_backup_blocks_all_writes(generated):
    backup = generated / "README.md.before-0.1.0-alpha4"
    backup.write_text("original backup")
    main = generated / "modules/forgejo_repository/main.tf"
    before = main.read_bytes()
    assert repair.main([str(generated), "--write"]) == 1
    assert main.read_bytes() == before
    assert backup.read_text() == "original backup"


def test_other_provider_version_is_rejected(generated):
    versions = generated / "versions.tf"
    versions.write_text(versions.read_text().replace("1.6.0", "2.0.0"))
    assert repair.main([str(generated), "--write"]) == 1
    assert not list(generated.rglob("*.before-0.1.0-alpha4"))


def test_custom_creation_expression_is_not_silently_removed(generated):
    path = generated / "modules/forgejo_repository/main.tf"
    path.write_text(path.read_text().replace("labels = var.items[each.key].labels", "labels = true"))
    assert repair.main([str(generated), "--write"]) == 1
    assert not list(generated.rglob("*.before-0.1.0-alpha4"))


def test_linked_module_is_not_modified(generated, tmp_path):
    path = generated / "modules/forgejo_repository/main.tf"
    external = tmp_path / "external.tf"
    path.rename(external)
    before = external.read_bytes()
    path.symlink_to(external)
    assert repair.main([str(generated), "--write"]) == 1
    assert external.read_bytes() == before


def test_multiline_ignore_list_is_supported(generated):
    path = generated / "modules/forgejo_repository/main.tf"
    path.write_text(path.read_text().replace(
        "ignore_changes = [auto_init, clone_addr, gitignores, license, readme]",
        "ignore_changes = [\n      auto_init,\n      clone_addr,\n      gitignores,\n      license,\n      readme,\n    ]",
    ))
    assert repair.main([str(generated), "--write"]) == 0


def test_keeps_existing_all_ignore_policy(generated):
    path = generated / "modules/forgejo_repository/main.tf"
    path.write_text(path.read_text().replace(
        "ignore_changes = [auto_init, clone_addr, gitignores, license, readme]", "ignore_changes = all",
    ))
    assert repair.main([str(generated), "--write"]) == 0
    assert "ignore_changes = all" in path.read_text()
