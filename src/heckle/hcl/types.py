from __future__ import annotations

import dataclasses
from collections import OrderedDict
from pathlib import Path

@dataclasses.dataclass
class DomainLexState:
    in_string: bool = False
    in_block_comment: bool = False
    heredoc_delimiter: str | None = None
    heredoc_allow_indent: bool = False

@dataclasses.dataclass
class TopBlock:
    kind: str
    raw: str
    source: Path
    start_line: int
    resource_type: str | None = None
    resource_name: str | None = None

    @property
    def address(self) -> str | None:
        if self.resource_type and self.resource_name:
            return f"{self.resource_type}.{self.resource_name}"
        return None

@dataclasses.dataclass
class Body:
    attributes: "OrderedDict[str, str]" = dataclasses.field(default_factory=OrderedDict)
    blocks: list["NestedBlock"] = dataclasses.field(default_factory=list)

    def copy(self) -> "Body":
        return Body(
            attributes=OrderedDict(self.attributes),
            blocks=[
                NestedBlock(name=b.name, labels=b.labels, body=b.body.copy(), raw=b.raw)
                for b in self.blocks
            ],
        )

@dataclasses.dataclass
class NestedBlock:
    name: str
    labels: tuple[str, ...]
    body: Body
    raw: str

@dataclasses.dataclass
class Resource:
    resource_type: str
    name: str
    body: Body
    lifecycle_raw: str | None
    source: Path

    @property
    def address(self) -> str:
        return f"{self.resource_type}.{self.name}"

@dataclasses.dataclass(frozen=True)
class Expr:
    value: str

