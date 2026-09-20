from __future__ import annotations

import hashlib
import json
import re
from collections import OrderedDict, defaultdict
from typing import Any, Iterable, Mapping, Sequence

from heckle.hcl.types import Body, Expr, NestedBlock
from heckle.core.compilation import Family

def hcl_label(kind: str, source: str) -> str:
    readable = re.sub(r"[^A-Za-z0-9_]+", "_", source).strip("_").lower()
    readable = readable[:48] or "item"
    if readable[0].isdigit():
        readable = f"item_{readable}"
    digest = hashlib.sha1(f"{kind}\0{source}".encode("utf-8"), usedforsecurity=False).hexdigest()[:10]
    return f"{readable}_{digest}"

def hcl_string(value: Any) -> str:
    return json.dumps(str(value), ensure_ascii=False).replace("${", "$${").replace("%{", "%%{")

def hcl_bool(value: bool) -> str:
    return "true" if value else "false"

def hcl_list(values: Iterable[Any]) -> str:
    return "[" + ", ".join(hcl_string(value) for value in values) + "]"


def literal_string(expression: str | None) -> str | None:
    if expression is None:
        return None
    try:
        parsed = json.loads(expression.strip())
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed.replace("$${", "${").replace("%%{", "%{") if isinstance(parsed, str) else None

def literal_int(expression: str | None) -> int | None:
    if expression is None:
        return None
    try:
        parsed = json.loads(expression.strip())
    except (json.JSONDecodeError, TypeError):
        return None
    if isinstance(parsed, int) and not isinstance(parsed, bool):
        return parsed
    if isinstance(parsed, str) and parsed.isdigit():
        return int(parsed)
    return None

def literal_list(expression: str | None) -> list[Any] | None:
    """Read literal scalar lists, including HCL's optional trailing comma.

    Do not evaluate expressions, and do not run regex replacements inside strings.
    This deliberately rejects nonliteral provider configuration.
    """
    if expression is None:
        return None
    text = expression.strip()
    if not text.startswith("["):
        return None
    decoder = json.JSONDecoder()
    index = 1
    values: list[Any] = []
    while index < len(text):
        while index < len(text) and text[index].isspace():
            index += 1
        if index < len(text) and text[index] == "]":
            return values if not text[index + 1:].strip() else None
        try:
            value, end = decoder.raw_decode(text, index)
        except (ValueError, TypeError):
            return None
        if isinstance(value, str):
            value = value.replace("$${", "${").replace("%%{", "%{")
        values.append(value)
        index = end
        while index < len(text) and text[index].isspace():
            index += 1
        if index < len(text) and text[index] == ",":
            index += 1
        elif index >= len(text) or text[index] != "]":
            return None
    return None



def body_to_value(body: Body) -> "OrderedDict[str, Any]":
    result: "OrderedDict[str, Any]" = OrderedDict()
    for key, expression in body.attributes.items():
        result[key] = Expr(expression)
    if body.blocks:
        grouped: "OrderedDict[str, list[Any]]" = OrderedDict()
        for block in body.blocks:
            grouped.setdefault(block.name, []).append(body_to_value(block.body))
        result["__blocks"] = grouped
    return result

def hcl_key(key: str) -> str:
    return hcl_string(key)

def render_expr(expr: str, indent: int) -> list[str]:
    lines = expr.splitlines() or [""]
    output: list[str] = []
    delimiter: str | None = None
    for index, line in enumerate(lines):
        if delimiter is not None:
            output.append(line)
            if line.strip() == delimiter:
                delimiter = None
        else:
            output.append(line if index == 0 else " " * indent + line)
            match = re.search(r"<<-?([A-Za-z_][A-Za-z0-9_]*)\s*$", line)
            if match:
                delimiter = match.group(1)
    return output


def render_value(value: Any, indent: int = 0) -> list[str]:
    prefix = " " * indent
    if isinstance(value, Expr):
        return render_expr(value.value, indent)
    if isinstance(value, Mapping):
        lines = ["{"]
        for key, child in value.items():
            child_lines = render_value(child, indent + 2)
            lines.append(f"{' ' * (indent + 2)}{hcl_key(str(key))} = {child_lines[0]}")
            lines.extend(child_lines[1:])
        lines.append(prefix + "}")
        return lines
    if isinstance(value, list):
        if not value:
            return ["[]"]
        lines = ["["]
        for child in value:
            child_lines = render_value(child, indent + 2)
            lines.append(" " * (indent + 2) + child_lines[0])
            lines.extend(child_lines[1:])
            lines[-1] += ","
        lines.append(prefix + "]")
        return lines
    if value is None:
        return ["null"]
    if isinstance(value, bool):
        return ["true" if value else "false"]
    if isinstance(value, (int, float)):
        return [str(value)]
    return [hcl_string(value)]

def body_schema(
    bodies: Sequence[Body],
) -> tuple[list[str], dict[str, list[Body]], set[str]]:
    attrs: list[str] = []
    seen: set[str] = set()
    presence: dict[str, int] = defaultdict(int)
    blocks: dict[str, list[Body]] = defaultdict(list)
    for body in bodies:
        for attr in body.attributes:
            if attr not in seen:
                attrs.append(attr)
                seen.add(attr)
            presence[attr] += 1
        grouped: dict[str, list[NestedBlock]] = defaultdict(list)
        for block in body.blocks:
            grouped[block.name].append(block)
        for name, nested in grouped.items():
            blocks[name].extend(item.body for item in nested)
    required = {name for name, count in presence.items() if count == len(bodies)}
    return attrs, blocks, required


def body_presence_signature(body: Body) -> tuple[object, ...]:
    """Return the exact configured shape of a provider resource body.

    Presence is deliberately distinct from value. Two bodies with the same
    values but different omitted attributes must not share a resource block:
    provider defaults can make `attribute = null` behave differently from an
    absent argument during import/adoption.
    """
    return (
        tuple(sorted(body.attributes)),
        tuple((block.name, body_presence_signature(block.body)) for block in body.blocks),
    )


def body_presence_weight(body: Body) -> int:
    """Count configured attributes/blocks for deterministic profile selection."""
    return len(body.attributes) + sum(1 + body_presence_weight(block.body) for block in body.blocks)

def safe_identifier(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_").lower() or "item"
    if value[0].isdigit():
        value = "item_" + value
    return value

def dynamic_iterator(path: tuple[str, ...]) -> str:
    base = safe_identifier("_".join(path))
    digest = hashlib.sha1("/".join(path).encode(), usedforsecurity=False).hexdigest()[:5]
    return f"{base}_{digest}"

def emit_dynamic_block(
    block_name: str,
    bodies: Sequence[Body],
    parent_expr: str,
    path: tuple[str, ...],
    indent: int,
    *,
    strict_presence: bool = False,
) -> list[str]:
    prefix = " " * indent
    iterator = dynamic_iterator(path + (block_name,))
    attrs, child_blocks, required = body_schema(bodies)
    if strict_presence and set(attrs) != required:
        missing = sorted(set(attrs) - required)
        raise ValueError(
            "unsafe heterogeneous nested provider block; split by configuration shape: "
            + ", ".join(missing)
        )
    lines = [
        f'{prefix}dynamic "{block_name}" {{',
        f"{prefix}  for_each = try({parent_expr}.__blocks.{block_name}, [])",
        f"{prefix}  iterator = {iterator}",
        f"{prefix}  content {{",
    ]
    for attr in attrs:
        expr = f"{iterator}.value.{attr}"
        if attr not in required:
            expr = f"try({expr}, null)"
        lines.append(f"{prefix}    {attr} = {expr}")
    for child_name, child_bodies in child_blocks.items():
        lines.extend(
            emit_dynamic_block(
                child_name,
                child_bodies,
                f"{iterator}.value",
                path + (block_name,),
                indent + 4,
                strict_presence=strict_presence,
            )
        )
    lines.extend([f"{prefix}  }}", f"{prefix}}}"])
    return lines

def emit_config_fields(
    family: Family,
    config_expr: str,
    indent: int = 2,
    exclude_attrs: set[str] | None = None,
    *,
    strict_presence: bool = False,
) -> list[str]:
    exclude_attrs = exclude_attrs or set()
    attrs, blocks, required = body_schema(family.samples)
    if strict_presence and set(attrs) != required:
        missing = sorted(set(attrs) - required)
        raise ValueError(
            "unsafe heterogeneous provider resource body; split by configuration shape: "
            + ", ".join(missing)
        )
    prefix = " " * indent
    lines: list[str] = []
    for attr in attrs:
        if attr in exclude_attrs:
            continue
        expr = f"{config_expr}.{attr}"
        if attr not in required:
            expr = f"try({expr}, null)"
        lines.append(f"{prefix}{attr} = {expr}")
    for block_name, bodies in blocks.items():
        if lines:
            lines.append("")
        lines.extend(
            emit_dynamic_block(
                block_name,
                bodies,
                config_expr,
                (family.resource_type,),
                indent,
                strict_presence=strict_presence,
            )
        )
    return lines

def reindent_block(raw: str, indent: int) -> list[str]:
    raw_lines = raw.strip().splitlines()
    minimum = min(
        (len(line) - len(line.lstrip()) for line in raw_lines if line.strip()),
        default=0,
    )
    prefix = " " * indent
    return [prefix + line[minimum:] for line in raw_lines]



def hcl_literal(value: Any) -> str:
    """Encode a data value, escaping HCL template introducers in every string."""
    return "\n".join(render_value(value))
