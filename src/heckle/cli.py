"""Argparse front end; all execution paths call the same Pipeline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from heckle import __version__
from heckle.config import Connection
from heckle.core.identifiers import filesystem_name
from heckle.core.model import FORGES
from heckle.compatibility import compatibility_summary, format_compatibility
from heckle.core.registry import backend
from heckle.errors import GenerationError, GitHubAPIError
from heckle.pipeline import Options, Pipeline
from heckle.reporting import format_report, read_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="heckle",
        description="Turn existing software forges into maintainable Terraform/OpenTofu configuration. Never runs apply.",
    )
    parser.add_argument("--version", action="version", version=f"heckle {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("generate", "inventory"):
        action = commands.add_parser(
            command,
            help=(
                "Create a standalone HCL project"
                if command == "generate"
                else "Save a private, versioned inventory and coverage report"
            ),
        )
        forges = action.add_subparsers(dest="forge", required=True)
        for forge in FORGES:
            sub = forges.add_parser(forge)
            selectors = sub.add_mutually_exclusive_group(required=True)
            selectors.add_argument(
                "--group" if forge == "gitlab" else "--org",
                dest="scope",
                help=(
                    "Group path (including subgroups)"
                    if forge == "gitlab"
                    else "Organization login"
                ),
            )
            selectors.add_argument(
                "--user", help="Personal account username; include only its owned repositories"
            )
            selectors.add_argument(
                "--me",
                action="store_true",
                help="Resolve the authenticated user and include only their personal repositories",
            )
            sub.add_argument(
                "--url", help="Forge base URL; GitHub Enterprise uses its /api/v3 base URL"
            )
            sub.add_argument("--out", type=Path)
            sub.add_argument(
                "--force",
                action="store_true",
                help="Replace a marked, non-adopted Heckle output only",
            )
            sub.add_argument("--workers", type=int, default=4)
            sub.add_argument("--timeout", type=float, default=60)
            sub.add_argument("--retries", type=int, default=4)
            sub.add_argument(
                "--ca-file", help="Trusted CA bundle; TLS verification remains enabled"
            )
            sub.add_argument(
                "--allow-http",
                action="store_true",
                help="Permit unencrypted HTTP for trusted local test instances",
            )
            sub.add_argument(
                "--provider-version",
                help="Override the exact provider pin (experimental unless this Heckle release has audited it)",
            )
            sub.add_argument(
                "--allow-untested-provider",
                action="store_true",
                help="Allow an unaudited provider version; compatibility knowledge may be incomplete",
            )
            if command == "generate":
                sub.add_argument(
                    "--from-inventory",
                    type=Path,
                    help="Replay discovery; provider hydration still requires authenticated API access",
                )
                sub.add_argument(
                    "--allow-partial",
                    action="store_true",
                    help="Permit explicitly reported discovery permission/API gaps",
                )
                sub.add_argument("--keep-inventory", action="store_true")
                sub.add_argument(
                    "--keep-workdir",
                    action="store_true",
                    help="Retain the private temporary build workspace if generation fails, for diagnostics",
                )
                sub.add_argument(
                    "--no-validate",
                    action="store_true",
                    help="Skip final validation, not provider hydration or formatting",
                )
                sub.add_argument(
                    "--allow-destroy",
                    action="store_true",
                    help="Omit default prevent_destroy lifecycle guards",
                )
                sub.add_argument(
                    "--split-teams", action="store_true", help="GitHub: emit one team file per team"
                )
                sub.add_argument(
                    "--state-root",
                    type=Path,
                    help="Read an explicitly identified Heckle project's state to filter imports",
                )
                sub.add_argument(
                    "--tf",
                    help="Terraform or OpenTofu executable; defaults to HECKLE_TF_BIN, then tofu",
                )
    coverage = commands.add_parser(
        "coverage", help="Read a saved coverage report without network access"
    )
    coverage.add_argument("path", type=Path)
    coverage.add_argument("--json", action="store_true")
    compatibility = commands.add_parser(
        "compatibility", help="Show audited provider pins and known upstream quirks"
    )
    compatibility.add_argument("forge", nargs="?", choices=FORGES)
    compatibility.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "coverage":
            report = read_report(args.path)
            print(
                json.dumps(report, indent=2, sort_keys=True) if args.json else format_report(report)
            )
            return 0
        if args.command == "compatibility":
            names = [args.forge] if args.forge else list(FORGES)
            specs = [backend(name).provider().spec for name in names]
            if args.json:
                print(
                    json.dumps(
                        [compatibility_summary(spec) for spec in specs], indent=2, sort_keys=True
                    )
                )
            else:
                print(format_compatibility(specs))
            return 0
        personal = bool(args.user or args.me)
        scope = "@me" if args.me else args.user or args.scope
        connection = Connection.from_environment(
            args.forge,
            scope,
            args.url,
            namespace_type="user" if personal else None,
            workers=args.workers,
            timeout=args.timeout,
            retries=args.retries,
            ca_file=args.ca_file,
            allow_http=args.allow_http,
        )
        inventory = args.command == "inventory"
        output = args.out or Path(
            f"heckle-{args.forge}-{filesystem_name('me' if args.me else scope)}"
            + ("-inventory" if inventory else "")
        )
        options = Options(
            output=output,
            force=args.force,
            provider_version=args.provider_version,
            allow_partial=getattr(args, "allow_partial", False),
            prevent_destroy=not getattr(args, "allow_destroy", False),
            split_teams=getattr(args, "split_teams", False),
            keep_inventory=getattr(args, "keep_inventory", False),
            validate=not getattr(args, "no_validate", False),
            tf=getattr(args, "tf", None),
            from_inventory=getattr(args, "from_inventory", None),
            state_root=getattr(args, "state_root", None),
            allow_untested_provider=getattr(args, "allow_untested_provider", False),
            keep_workdir=getattr(args, "keep_workdir", False),
        )
        destination = Pipeline(
            connection, options, progress=lambda message: print(message, file=sys.stderr)
        ).run(inventory_only=inventory)
        print(
            f"Wrote {'inventory' if inventory else 'standalone Terraform/OpenTofu project'}: {destination}"
        )
        print(f"Review coverage: heckle coverage {destination}")
        print("No plan was applied and no persistent managed resource state was created by Heckle.")
        return 0
    except (GenerationError, GitHubAPIError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        return 130
