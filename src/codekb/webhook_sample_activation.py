from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

import yaml

from .diagnosis_webhook import DEFAULT_WEBHOOK_MAPPING_PATH, validate_diagnostic_webhook_sample_suite


DEFAULT_REAL_WEBHOOK_SAMPLE_SUITE_PATH = "/data/codekb/state/diagnose-webhook-samples.real.yaml"
WEBHOOK_SAMPLE_ENV_KEYS = (
    "CODEKB_DIAGNOSE_WEBHOOK_SAMPLES",
    "CODEKB_DIAGNOSE_WEBHOOK_REAL_SAMPLES",
)


def activate_diagnostic_webhook_samples(
    *,
    env_file: str,
    samples_path: str = "",
    mapping_path: str = DEFAULT_WEBHOOK_MAPPING_PATH,
    env: Mapping[str, str] | None = None,
    apply: bool = False,
    confirm_real_samples: bool = False,
) -> dict[str, Any]:
    env = os.environ if env is None else env
    normalized_samples = _target_samples_path(samples_path, env)
    summary = validate_diagnostic_webhook_sample_suite(normalized_samples, mapping_path=mapping_path)
    sample_summary = _sample_summary(summary)
    planned_updates = {key: normalized_samples for key in WEBHOOK_SAMPLE_ENV_KEYS}
    if summary["status"] != "passed":
        return {
            "status": "validation_failed",
            "ok": False,
            "applied": False,
            "env_file": str(env_file or ""),
            "updates": planned_updates,
            "sample_suite": sample_summary,
            "restart_required": False,
            "message": "Real webhook sample suite did not pass validation; env file was not changed.",
        }
    if not apply:
        return {
            "status": "planned",
            "ok": True,
            "applied": False,
            "env_file": str(env_file or ""),
            "updates": planned_updates,
            "sample_suite": sample_summary,
            "restart_required": False,
            "message": "Sample suite passed; rerun with --apply --confirm-real-samples to update the env file.",
        }
    if not confirm_real_samples:
        return {
            "status": "confirmation_required",
            "ok": False,
            "applied": False,
            "env_file": str(env_file or ""),
            "updates": planned_updates,
            "sample_suite": sample_summary,
            "restart_required": False,
            "message": "Refusing to update env file until --confirm-real-samples is provided.",
        }
    if not str(env_file or "").strip():
        raise ValueError("env_file is required when applying webhook sample activation")
    _write_env_updates(env_file, planned_updates)
    return {
        "status": "activated",
        "ok": True,
        "applied": True,
        "env_file": str(env_file),
        "updates": planned_updates,
        "sample_suite": sample_summary,
        "restart_required": True,
        "message": "Env file updated; restart API/MCP processes and rerun diagnose-readiness.",
    }


def _target_samples_path(samples_path: str, env: Mapping[str, str]) -> str:
    explicit = str(samples_path or "").strip()
    if explicit:
        return explicit
    configured = str(env.get("CODEKB_DIAGNOSE_WEBHOOK_REAL_SAMPLES", "") or "").strip()
    if configured:
        return configured
    return DEFAULT_REAL_WEBHOOK_SAMPLE_SUITE_PATH


def _sample_summary(summary: dict[str, Any]) -> dict[str, Any]:
    samples = [item for item in summary.get("samples", []) if isinstance(item, dict)]
    sources = sorted({str(item.get("source", "") or "") for item in samples if item.get("source")})
    return {
        "path": str(summary.get("path", "")),
        "mapping_path": str(summary.get("mapping_path", "")),
        "status": str(summary.get("status", "")),
        "total": int(summary.get("total", 0) or 0),
        "passed": int(summary.get("passed", 0) or 0),
        "failed": int(summary.get("failed", 0) or 0),
        "sources": sources,
        "generated_by_import": _generated_by_import_count(str(summary.get("path", ""))),
    }


def _generated_by_import_count(path: str) -> int:
    sample_path = Path(path)
    if not sample_path.exists():
        return 0
    data = yaml.safe_load(sample_path.read_text(encoding="utf-8")) or {}
    samples = data.get("samples", []) if isinstance(data, dict) else []
    if not isinstance(samples, list):
        return 0
    return sum(
        1
        for sample in samples
        if isinstance(sample, dict)
        and isinstance(sample.get("metadata"), dict)
        and sample["metadata"].get("generated_by") == "diagnose-webhook-sample-import"
    )


def _write_env_updates(path: str, updates: dict[str, str]) -> None:
    env_path = Path(path)
    existing_lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    replaced: set[str] = set()
    output_lines: list[str] = []
    for line in existing_lines:
        key = _env_line_key(line)
        if key in updates:
            output_lines.append(f"{key}={updates[key]}")
            replaced.add(key)
        else:
            output_lines.append(line)
    missing = [key for key in updates if key not in replaced]
    if missing and output_lines and output_lines[-1].strip():
        output_lines.append("")
    for key in missing:
        output_lines.append(f"{key}={updates[key]}")
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("\n".join(output_lines).rstrip() + "\n", encoding="utf-8")
    env_path.chmod(0o600)


def _env_line_key(line: str) -> str:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return ""
    if stripped.startswith("export "):
        stripped = stripped[len("export ") :].strip()
    if "=" not in stripped:
        return ""
    key = stripped.split("=", 1)[0].strip()
    if not key or not (key[0].isalpha() or key[0] == "_"):
        return ""
    if not all(char.isalnum() or char == "_" for char in key):
        return ""
    return key
