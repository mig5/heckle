#!/usr/bin/env python3
"""One-time repair of a Heckle 0.1.0-alpha3 Forgejo repository module.

Preview: python tools/repair_forgejo_alpha3.py /path/to/generated/project
Apply:   python tools/repair_forgejo_alpha3.py /path/to/generated/project --write

Does not run OpenTofu, contact Forgejo, or read/change state. Keep the project
backup until its new plan has been reviewed. This is not a runtime dependency.
"""
from __future__ import annotations

import argparse
import difflib
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

# Works from this source archive without installation, or with Heckle installed.
checkout = Path(__file__).resolve().parents[1] / "src"
if (checkout / "heckle").is_dir():
    sys.path.insert(0, str(checkout))

from heckle.errors import GenerationError
from heckle.hcl.parser import extract_top_blocks, parse_body, parse_resource, strip_outer_block
from heckle.hcl.render import reindent_block, render_expr
from heckle.providers.forgejo.repository import (
    CONDITIONAL_FIELDS,
    IGNORED_FIELDS,
    PERSONAL_CREATION_WARNING,
)


def compact(expression: str) -> str:
    return re.sub(r"\s+", "", expression)


def guarded(field: str, enabled: str) -> str:
    return (
        f"try(var.items[each.key].{enabled}, null) == false ? null : "
        f"try(var.items[each.key].{field}, null)"
    )


def check_generated(field: str, value: str, enabled: str | None = None) -> None:
    """Do not silently remove somebody's hand-written module expressions."""
    reference = f"var.items[each.key].{field}"
    allowed = {compact(reference), compact(f"try({reference}, null)")}
    if enabled:
        allowed.add(compact(guarded(field, enabled)))
    if compact(value) not in allowed:
        raise GenerationError(f"{field} has a custom expression; review/edit this module manually")


def rewrite_module(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    blocks, _ = extract_top_blocks(path)
    matches = [
        b for b in blocks if b.resource_type == "forgejo_repository" and b.resource_name == "this"
    ]
    if len(matches) != 1:
        raise GenerationError("Expected exactly one generated forgejo_repository.this resource")
    block = matches[0]
    resource = parse_resource(block)
    if compact(resource.body.attributes.get("for_each", "")) != "var.keys":
        raise GenerationError(
            "Not a Heckle 0.1.0-alpha3 var.keys repository module; refusing to rewrite it"
        )
    if resource.body.blocks:
        raise GenerationError(
            "Repository has custom nested blocks; review/edit this module manually"
        )
    changed = False
    for name in IGNORED_FIELDS & resource.body.attributes.keys():
        check_generated(name, resource.body.attributes[name])
        del resource.body.attributes[name]
        changed = True
    for enabled, fields in CONDITIONAL_FIELDS.items():
        for field in fields:
            if field in resource.body.attributes:
                check_generated(field, resource.body.attributes[field], enabled)
                changed |= compact(resource.body.attributes[field]) != compact(
                    guarded(field, enabled)
                )
                resource.body.attributes[field] = guarded(field, enabled)

    lifecycle = (
        parse_body(strip_outer_block(resource.lifecycle_raw)) if resource.lifecycle_raw else None
    )
    prior = lifecycle.attributes.get("ignore_changes", "[]") if lifecycle else "[]"
    if prior.strip() == "all":
        ignored = "all"
    else:
        # The generated module uses a plain list of attribute names. Stop rather
        # than guessing at hand-written index expressions or unusual lifecycle HCL.
        if not re.fullmatch(r"\[\s*(?:[A-Za-z_]\w*\s*,?\s*)*\]", prior):
            raise GenerationError("Custom ignore_changes expression; review/edit it manually")
        previous_names = set(re.findall(r"[A-Za-z_]\w*", prior))
        changed |= not IGNORED_FIELDS <= previous_names
        names = previous_names | set(IGNORED_FIELDS)
        ignored = "[" + ", ".join(sorted(names)) + "]"

    if not changed and lifecycle is not None:
        return text

    lines = ['resource "forgejo_repository" "this" {']
    for name, value in resource.body.attributes.items():
        rendered = render_expr(value, 2)
        lines.extend([f"  {name} = {rendered[0]}", *rendered[1:]])
    lines.extend(["", "  lifecycle {"])
    if lifecycle:
        for name, value in lifecycle.attributes.items():
            if name != "ignore_changes":
                rendered = render_expr(value, 4)
                lines.extend([f"    {name} = {rendered[0]}", *rendered[1:]])
    lines.extend(
        [
            "",
            "    # Creation options and unreadable provider defaults are not imported settings.",
            f"    ignore_changes = {ignored}",
        ]
    )
    if lifecycle:
        for nested in lifecycle.blocks:
            lines.extend(reindent_block(nested.raw, 4))
    lines.extend(["  }", "}"])
    start = sum(len(line) for line in text.splitlines(keepends=True)[: block.start_line - 1])
    return text[:start] + "\n".join(lines) + text[start + len(block.raw) :]


def save_with_backup(path: Path, content: str) -> None:
    """Never overwrite a previous backup or an unrelated symlink."""
    backup = path.with_name(path.name + ".before-0.1.0-alpha4")
    if path.is_symlink() or backup.exists() or backup.is_symlink():
        raise GenerationError(f"Refusing symlink or existing backup: {backup}")
    original = path.read_bytes()
    descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(original)
    descriptor, temporary = tempfile.mkstemp(prefix=".heckle-repair-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
        os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    print(f"Updated {path}; original saved as {backup.name}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write changes after saving originals; default only prints a diff",
    )
    args = parser.parse_args(argv)
    root = args.project.expanduser().resolve()
    try:
        versions = (root / "versions.tf").read_text(encoding="utf-8")
        if not (
            re.search(r'source\s*=\s*"svalabs/forgejo"', versions)
            and re.search(r'version\s*=\s*"=\s*1\.6\.0"', versions)
        ):
            raise GenerationError(
                "This repair targets Heckle output pinned to svalabs/forgejo 1.6.0 only"
            )
        path = root / "modules/forgejo_repository/main.tf"
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise GenerationError("Refusing a repository module linked outside this project")
        edits = [(path, rewrite_module(path))]
        readme = root / "README.md"
        if readme.is_file() and not readme.is_symlink():
            text = readme.read_text(encoding="utf-8")
            replacement = re.sub(
                r"(?ms)^\*\*Forgejo creation caveat:\*\*.*?(?=\n\s*\n|\Z)",
                lambda _: PERSONAL_CREATION_WARNING,
                text,
            )
            if replacement != text:
                edits.append((readme, replacement))
        changed = [
            (path, content)
            for path, content in edits
            if path.read_text(encoding="utf-8") != content
        ]
        # Check all backups before changing either file.
        if args.write:
            for path, _ in changed:
                backup = path.with_name(path.name + ".before-0.1.0-alpha4")
                if backup.exists() or backup.is_symlink():
                    raise GenerationError(f"Backup already exists: {backup}; nothing changed")
        for path, content in changed:
            if args.write:
                save_with_backup(path, content)
            else:
                sys.stdout.writelines(
                    difflib.unified_diff(
                        path.read_text(encoding="utf-8").splitlines(keepends=True),
                        content.splitlines(keepends=True),
                        fromfile=str(path),
                        tofile=str(path) + " (repaired)",
                    )
                )
        if not changed:
            print("Already repaired; no files changed.")
        elif not args.write:
            print("\nPreview only. Rerun with --write to save these changes.")
        else:
            print("No state, import addresses, backend settings or credentials were changed.")
            print("Next: tofu fmt -recursive && tofu plan -out=import.tfplan")
        return 0
    except (OSError, GenerationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
