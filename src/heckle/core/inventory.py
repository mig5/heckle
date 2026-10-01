from __future__ import annotations

import json
from pathlib import Path

from heckle.core.model import ForgeModel, SCHEMA_VERSION
from heckle.core.security import scrub, write_private_json
from heckle.errors import GenerationError


def save_inventory(root: Path, model: ForgeModel) -> None:
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    write_private_json(root / "inventory.json", scrub(model.to_dict()))
    (root / ".heckle-generated").write_text(
        f"Heckle inventory schema {SCHEMA_VERSION}\n", encoding="utf-8"
    )


def load_inventory(path: Path) -> ForgeModel:
    if path.is_dir():
        path = path / "inventory.json"
    try:
        return ForgeModel.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        raise GenerationError(f"Cannot load inventory {path}: {exc}") from exc
