from __future__ import annotations

import hashlib
import importlib
import json
import shutil
import sys
import os
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

TEST_ROOT = Path(__file__).resolve().parent
SKILL_ROOT = TEST_ROOT.parent
SCRIPTS_ROOT = SKILL_ROOT / "scripts"
FIXTURES_ROOT = TEST_ROOT / "fixtures"

if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))


def load_module(name: str):
    return importlib.import_module(name)


def load_fixture(name: str) -> dict[str, Any]:
    value = json.loads((FIXTURES_ROOT / name).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"fixture {name} must contain one JSON object")
    return value


def load_accounting_fixture() -> dict[str, Any]:
    return load_fixture("accounting-cases.json")


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def write_frozen_bytes(
    path: Path, payload: bytes, *, relative_to: Path | None = None
) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    try:
        remaining = memoryview(payload)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("short write")
            remaining = remaining[written:]
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o444)
    finally:
        os.close(descriptor)
    return {
        "path": (
            path.relative_to(relative_to).as_posix()
            if relative_to is not None
            else path.as_posix()
        ),
        "sha256": sha256(payload),
        "size_bytes": len(payload),
    }


def copy_references(destination: Path) -> Path:
    root = Path(destination) / "skill"
    shutil.copytree(SKILL_ROOT / "references", root / "references")
    return root


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
