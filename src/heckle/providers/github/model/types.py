from __future__ import annotations

import dataclasses
from collections import OrderedDict
from typing import Any

from heckle.core.compilation import Family, ImportRecord


@dataclasses.dataclass(frozen=True)
class WebhookVariable:
    name: str
    description: str
    scope: str
    hook_id: str
    value: str


@dataclasses.dataclass
class Model:
    org: str
    organization: "OrderedDict[str, Any]" = dataclasses.field(default_factory=OrderedDict)
    members: "OrderedDict[str, Any]" = dataclasses.field(default_factory=OrderedDict)
    teams: "OrderedDict[str, Any]" = dataclasses.field(default_factory=OrderedDict)
    repositories: "OrderedDict[str, Any]" = dataclasses.field(default_factory=OrderedDict)
    families: dict[str, Family] = dataclasses.field(default_factory=dict)
    imports: list[ImportRecord] = dataclasses.field(default_factory=list)
    webhook_variables: "OrderedDict[str, WebhookVariable]" = dataclasses.field(
        default_factory=OrderedDict
    )
    report: dict[str, Any] = dataclasses.field(default_factory=dict)
    # Repositories rendered as github_repository.archived rather than .this.
    archived_repositories: set[str] = dataclasses.field(default_factory=set)
    # Current address -> the address the same repository has in the other block.
    # Regenerating over an existing state turns these into moved blocks.
    previous_addresses: dict[str, str] = dataclasses.field(default_factory=dict)

    def family(self, resource_type: str) -> Family:
        return self.families.setdefault(resource_type, Family(resource_type))
