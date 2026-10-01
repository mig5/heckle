"""Report discovery separately from provider mapping and configuration generation."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict
import json
from pathlib import Path
from typing import Any

from heckle import __version__
from heckle.core.compilation import CompiledProject, ImportPlan
from heckle.errors import GenerationError
from heckle.providers.base import ProviderSpec
from heckle.compatibility import compatibility_summary


def generation_report(
    plan: ImportPlan,
    spec: ProviderSpec,
    *,
    generated: bool = False,
    validated: bool = False,
    imported_addresses: int = 0,
    project: CompiledProject | None = None,
) -> dict[str, Any]:
    provider = plan.coverage.to_dict()
    if generated:
        for item in provider["items"]:
            if item["status"] == "import_planned":
                item["status"] = "configuration_generated"
        provider["counts"] = dict(
            sorted(Counter(item["status"] for item in provider["items"]).items())
        )
    report = {
        "report_version": 1,
        "heckle_version": __version__,
        "source": asdict(plan.model.source),
        "provider": {"source": spec.source, "version": spec.version},
        "provider_compatibility": compatibility_summary(spec),
        "inventory_counts": dict(
            sorted(Counter(entity.native_kind for entity in plan.model.ordered()).items())
        ),
        "discovery": [
            asdict(observation)
            for observation in sorted(plan.model.observations, key=lambda o: (o.scope, o.status))
        ],
        "coverage": provider,
        "validation": "passed" if validated else "not_run",
        "state_changes_performed": False,
        "imports_already_in_state": imported_addresses,
        "scope_note": "Coverage is limited to explicitly queried endpoints and implemented mappings, not every feature offered by the forge.",
    }
    if project is not None:
        report["required_private_inputs"] = sorted(project.variables)
        if project.native is not None:
            report["native_report"] = project.native.report
            report["required_private_inputs"] = sorted(project.native.webhook_variables)
        report["ignored_attributes"] = {
            item.address: sorted(item.ignored) for item in project.resources if item.ignored
        }
    return report


def read_report(path: Path) -> dict[str, Any]:
    candidates = (
        [path] if path.is_file() else [path / ".generation" / "report.json", path / "report.json"]
    )
    for candidate in candidates:
        if candidate.is_file():
            try:
                value = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise GenerationError("Cannot read coverage report") from exc
            if not isinstance(value, dict) or value.get("report_version") != 1:
                raise GenerationError("Not a Heckle coverage report")
            return value
    raise GenerationError(f"No Heckle report found under {path}")


def format_report(report: dict[str, Any]) -> str:
    source = report["source"]
    lines = [f"{source['forge']}: {source['scope']} ({source['url']})", "", "Inventory"]
    lines.extend(f"  {name}: {count}" for name, count in report["inventory_counts"].items())
    lines.extend(["", "Provider coverage"])
    lines.extend(f"  {name}: {count}" for name, count in report["coverage"]["counts"].items())
    warnings = report["coverage"].get("warnings", [])
    if warnings:
        lines.extend(["", "Notes", *(f"  {warning}" for warning in warnings)])
    failures = [o for o in report["discovery"] if o["status"] not in {"collected", "skipped"}]
    if failures:
        lines.extend(["", "Discovery gaps"])
        lines.extend(f"  {o['scope']}: {o['status']}" for o in failures)
    compatibility = report.get("provider_compatibility", {})
    if compatibility:
        status = "audited" if compatibility.get("audited") else "UNTESTED"
        lines.extend(["", f"Provider compatibility: {status}"])
        for quirk in compatibility.get("quirks", []):
            lines.append(f"  {quirk['id']}: {quirk['summary']}")
    lines.extend(
        [
            "",
            f"Validation: {report['validation']}",
            "Heckle never applies a plan or persists managed state; any state-only imports used for adoption safety checks are disposable.",
            report["scope_note"],
        ]
    )
    return "\n".join(lines)
