from __future__ import annotations

import re
import json
from pathlib import Path

from heckle.errors import GenerationError
from heckle.hcl.types import Body, DomainLexState, NestedBlock, Resource, TopBlock

RESOURCE_HEADER_RE = re.compile(
    r'^\s*resource\s+"(?P<type>[A-Za-z0-9_]+)"\s+"(?P<name>[A-Za-z0-9_]+)"\s*\{',
    re.MULTILINE,
)
TOP_BLOCK_RE = re.compile(
    r"^\s*(resource|data|module|variable|output|locals|terraform|provider|import|moved|check)\b"
)
ITEM_ATTRIBUTE_RE = re.compile(r"^(?P<indent>\s*)(?P<name>[A-Za-z_][A-Za-z0-9_-]*)\s*=")
ITEM_BLOCK_RE = re.compile(
    r"^(?P<indent>\s*)(?P<name>[A-Za-z_][A-Za-z0-9_-]*)"
    r'(?P<labels>(?:\s+"(?:[^"\\]|\\.)*")*)\s*\{\s*(?:#.*)?$'
)
LABEL_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')
DOMAIN_HEREDOC_RE = re.compile(r"<<(-?)([A-Za-z_][A-Za-z0-9_]*)")

def scan_delimiters(
    text: str, state: DomainLexState | None = None
) -> tuple[int, int, int]:
    if state is None:
        state = DomainLexState()
    brace = bracket = paren = 0
    for line in text.splitlines(keepends=True):
        if state.heredoc_delimiter is not None:
            candidate = (
                line.strip() if state.heredoc_allow_indent else line.rstrip("\r\n")
            )
            if candidate == state.heredoc_delimiter:
                state.heredoc_delimiter = None
                state.heredoc_allow_indent = False
            continue

        i = 0
        while i < len(line):
            if state.in_block_comment:
                end = line.find("*/", i)
                if end == -1:
                    break
                state.in_block_comment = False
                i = end + 2
                continue
            if state.in_string:
                if line[i] == "\\":
                    i += 2
                    continue
                if line[i] == '"':
                    state.in_string = False
                i += 1
                continue
            if line.startswith("/*", i):
                state.in_block_comment = True
                i += 2
                continue
            if line.startswith("//", i) or line[i] == "#":
                break
            if line[i] == '"':
                state.in_string = True
                i += 1
                continue
            heredoc = DOMAIN_HEREDOC_RE.match(line, i)
            if heredoc:
                state.heredoc_allow_indent = heredoc.group(1) == "-"
                state.heredoc_delimiter = heredoc.group(2)
                i = heredoc.end()
                continue
            char = line[i]
            if char == "{":
                brace += 1
            elif char == "}":
                brace -= 1
            elif char == "[":
                bracket += 1
            elif char == "]":
                bracket -= 1
            elif char == "(":
                paren += 1
            elif char == ")":
                paren -= 1
            i += 1
        state.in_string = False
    return brace, bracket, paren

def extract_top_blocks(path: Path) -> tuple[list[TopBlock], str]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    blocks: list[TopBlock] = []
    residual: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        match = TOP_BLOCK_RE.match(line)
        if not match:
            residual.append(line)
            i += 1
            continue
        kind = match.group(1)
        start = i
        state = DomainLexState()
        depth = 0
        # Provider formatting normally puts the opening brace on this line.
        # A one-line empty block has zero net brace delta but is still complete.
        saw_open = "{" in line
        while i < len(lines):
            b, _, _ = scan_delimiters(lines[i], state)
            if b > 0:
                saw_open = True
            depth += b
            i += 1
            if saw_open and depth == 0 and state.heredoc_delimiter is None:
                break
        if not saw_open or depth != 0 or state.heredoc_delimiter or state.in_block_comment:
            raise GenerationError(f"Unterminated HCL block at {path}:{start + 1}")
        raw = "".join(lines[start:i]).rstrip()
        resource_type = resource_name = None
        if kind == "resource":
            header = RESOURCE_HEADER_RE.match(raw)
            if not header:
                raise GenerationError(
                    f"cannot parse resource header at {path}:{start + 1}"
                )
            resource_type = header.group("type")
            resource_name = header.group("name")
        blocks.append(
            TopBlock(
                kind=kind,
                raw=raw,
                source=path,
                start_line=start + 1,
                resource_type=resource_type,
                resource_name=resource_name,
            )
        )
    return blocks, "".join(residual)

def strip_outer_block(raw: str) -> str:
    opening = raw.find("{")
    closing = raw.rfind("}")
    if opening < 0 or closing <= opening:
        raise GenerationError("invalid HCL block")
    return raw[opening + 1 : closing]

def split_body_items(body_text: str) -> list[str]:
    lines = body_text.splitlines(keepends=True)
    items: list[str] = []
    i = 0
    while i < len(lines):
        if not lines[i].strip() or lines[i].lstrip().startswith(("#", "//")):
            i += 1
            continue
        attr = ITEM_ATTRIBUTE_RE.match(lines[i])
        block = ITEM_BLOCK_RE.match(lines[i].rstrip("\r\n"))
        if not attr and not block:
            raise GenerationError(f"cannot identify HCL body item: {lines[i].rstrip()}")
        indent = len((attr or block).group("indent"))
        start = i
        if block and not attr:
            state = DomainLexState()
            depth = 0
            saw_open = False
            while i < len(lines):
                b, _, _ = scan_delimiters(lines[i], state)
                if b > 0:
                    saw_open = True
                depth += b
                i += 1
                if saw_open and depth == 0 and state.heredoc_delimiter is None:
                    break
            if not saw_open or depth != 0 or state.heredoc_delimiter or state.in_block_comment:
                raise GenerationError("Unterminated nested HCL block")
            items.append("".join(lines[start:i]).rstrip())
            continue

        state = DomainLexState()
        brace = bracket = paren = 0
        while i < len(lines):
            b, s, p = scan_delimiters(lines[i], state)
            brace += b
            bracket += s
            paren += p
            i += 1
            if state.heredoc_delimiter is not None or brace or bracket or paren:
                continue
            if i >= len(lines) or not lines[i].strip():
                break
            next_indent = len(lines[i]) - len(lines[i].lstrip())
            if next_indent > indent:
                continue
            if ITEM_ATTRIBUTE_RE.match(lines[i]) or ITEM_BLOCK_RE.match(
                lines[i].rstrip("\r\n")
            ):
                break
        if brace or bracket or paren or state.heredoc_delimiter or state.in_block_comment:
            raise GenerationError("Unterminated HCL expression")
        items.append("".join(lines[start:i]).rstrip())
    return items

def parse_body(text: str) -> Body:
    result = Body()
    for raw_item in split_body_items(text):
        first = raw_item.splitlines()[0]
        attr = ITEM_ATTRIBUTE_RE.match(first)
        block = ITEM_BLOCK_RE.match(first)
        if attr:
            equals = raw_item.find("=")
            if attr.group("name") in result.attributes:
                raise GenerationError("Duplicate HCL attribute: " + attr.group("name"))
            result.attributes[attr.group("name")] = raw_item[equals + 1 :].strip()
            continue
        if not block:
            raise GenerationError(f"cannot parse HCL item: {first}")
        name = block.group("name")
        labels = tuple(
            json.loads('"' + value + '"')
            for value in LABEL_RE.findall(block.group("labels") or "")
        )
        nested = parse_body(strip_outer_block(raw_item))
        result.blocks.append(NestedBlock(name, labels, nested, raw_item))
    return result

def parse_resource(block: TopBlock) -> Resource:
    if block.kind != "resource" or not block.resource_type or not block.resource_name:
        raise GenerationError("not a resource block")
    body = parse_body(strip_outer_block(block.raw))
    lifecycle = [b for b in body.blocks if b.name == "lifecycle"]
    if len(lifecycle) > 1:
        raise GenerationError(f"multiple lifecycle blocks in {block.address}")
    body.blocks = [b for b in body.blocks if b.name != "lifecycle"]
    return Resource(
        resource_type=block.resource_type,
        name=block.resource_name,
        body=body,
        lifecycle_raw=lifecycle[0].raw if lifecycle else None,
        source=block.source,
    )


