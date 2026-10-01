"""Coverage distinguishes API discovery, resource existence, and import support."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from collections import Counter
from typing import Any


@dataclass
class CoverageItem:
    key: str
    kind: str
    status: str  # import_planned, inventory_only, provider_unsupported, not_importable, skipped
    reason: str = ""
    resource_type: str | None = None


@dataclass
class Coverage:
    items: list[CoverageItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add(
        self, key: str, kind: str, status: str, reason: str = "", resource_type: str | None = None
    ) -> None:
        self.items.append(CoverageItem(key, kind, status, reason, resource_type))

    def to_dict(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(Counter(item.status for item in self.items).items())),
            "items": [
                asdict(item) for item in sorted(self.items, key=lambda item: (item.kind, item.key))
            ],
            "warnings": sorted(set(self.warnings)),
        }
