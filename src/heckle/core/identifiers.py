from __future__ import annotations

import hashlib
import re

def escape_colons(value: str) -> str:
    return value.replace(":", "??")

def filesystem_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")[:100] or "item"
    if cleaned != value or cleaned.casefold() != cleaned:
        cleaned += "_" + hashlib.sha256(value.encode()).hexdigest()[:12]
    return cleaned
