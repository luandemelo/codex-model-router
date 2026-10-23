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


def fixture_gate_evidence(evidence, runtime, ontologies, brief_bytes, *,
                          source_phase="controller", source_raw=None, source_thread_id=None):
    """Synthetic structural fixture, never evidence of live semantic judgment."""
    compiler = load_module("cmr_compiler")
    gates = load_module("cmr_gate_evidence")
    context = compiler.gate_evidence_context(evidence, runtime, source_phase=source_phase,
        source_raw=source_raw, source_thread_id=source_thread_id)
    semantic = {field: evidence[field] for field in ("fact_ids", "uncertainty_ids", "action_intents")}
    value = {
        "schema_version": "cmr-gate-evidence-v1", "task_id": evidence["task_id"],
        "task_phase": runtime["task_phase"], "brief": evidence["brief"],
        "source": {"phase": source_phase, "raw": evidence["raw"] if source_raw is None else source_raw,
                   "thread_id": evidence["occurrence"]["thread_id"] if source_thread_id is None else source_thread_id,
                   "semantic_sha256": sha256(canonical_json_bytes(semantic))},
        "claims": [{"fact_id": fact, "basis": "requested_change", "start_byte": 0,
                    "end_byte": len(brief_bytes), "quote": brief_bytes.decode("utf-8"),
                    "explanation": "Synthetic classification fixture; semantic applicability is independently adjudicated."}
                   for fact in gates.required_gate_ids(evidence["fact_ids"], ontologies)],
    }
    return gates.bind_gate_evidence(value, context=context, brief_bytes=brief_bytes, ontologies=ontologies)


def compile_fixture_route(evidence, runtime, ontologies, *,
                          brief_bytes=b"Task T-002 approved implementation brief\n"):
    compiler = load_module("cmr_compiler")
    # Malformed-envelope tests still reach the production validator's original
    # precedence; fixture construction must not hide that error under its own.
    if compiler.validate_route_evidence(evidence, ontologies) or compiler.validate_runtime_input(runtime, ontologies):
        return compiler.compile_route(evidence, runtime, ontologies, gate_evidence=None, brief_bytes=brief_bytes)
    bound = fixture_gate_evidence(evidence, runtime, ontologies, brief_bytes)
    return compiler.compile_route(evidence, runtime, ontologies, gate_evidence=bound, brief_bytes=brief_bytes)
