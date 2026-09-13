#!/usr/bin/env python3
"""Build one compact, self-contained judge handoff stream for one Agent run.

The runtime rollout directory remains untouched for recovery and engineering
audit.  This exporter sends only the data consumed by the downstream judge:
one result row and the selected complete terminal trace for each test case.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Iterable, Mapping


SCHEMA_VERSION = "ifv-agent-judge-handoff-v1"


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number} must be a JSON object")
            rows.append(value)
    return rows


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_json(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _case_ids(path: Path) -> list[str]:
    values = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    case_ids = [value for value in values if value]
    if not case_ids:
        raise ValueError(f"empty target case list: {path}")
    if len(case_ids) != len(set(case_ids)):
        raise ValueError(f"duplicate target case IDs: {path}")
    return case_ids


def _resolve_merged_dir(run_dir: Path, summary: Mapping[str, Any]) -> Path:
    configured = str(summary.get("merged_dir") or "").strip()
    if configured:
        candidate = Path(configured).expanduser()
        if not candidate.is_absolute():
            candidate = run_dir / candidate
        if candidate.is_dir():
            return candidate.resolve()
    fallback = run_dir / "rollouts" / "test" / "merged"
    if fallback.is_dir():
        return fallback.resolve()
    raise FileNotFoundError("cannot locate rollouts/test/merged")


def _provenance_index(merged_dir: Path) -> dict[str, dict[str, Any]]:
    path = merged_dir / "trace-provenance.jsonl"
    if not path.is_file():
        return {}
    rows = _read_jsonl(path)
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        case_id = str(row.get("case_id") or "").strip()
        if not case_id:
            raise ValueError(f"trace provenance row lacks case_id: {path}")
        if case_id in indexed:
            raise ValueError(f"duplicate trace provenance for {case_id}")
        indexed[case_id] = row
    return indexed


def _benchmark_descriptor(run_config: Mapping[str, Any]) -> dict[str, Any]:
    raw_path = str(run_config.get("benchmark") or "").strip()
    descriptor: dict[str, Any] = {"source_path": raw_path or None}
    if raw_path:
        path = Path(raw_path).expanduser()
        if path.is_file():
            descriptor.update(
                {
                    "sha256": _sha256_file(path),
                    "size_bytes": path.stat().st_size,
                }
            )
    return descriptor


def _write_line(handle: BinaryIO, value: Mapping[str, Any]) -> None:
    handle.write(
        (
            json.dumps(
                dict(value),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
    )


def _result_index(
    rows: Iterable[dict[str, Any]],
    target_case_ids: list[str],
) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        case_id = str(row.get("case_id") or "").strip()
        if not case_id:
            raise ValueError("agent result row lacks case_id")
        if case_id in indexed:
            raise ValueError(f"duplicate agent result for {case_id}")
        indexed[case_id] = row
    target = set(target_case_ids)
    unexpected = sorted(set(indexed) - target)
    missing = sorted(target - set(indexed))
    if unexpected:
        raise ValueError(f"unexpected agent result case: {unexpected[0]}")
    if missing:
        raise ValueError(f"missing agent result case: {missing[0]}")
    return indexed


def package_agent_judge_handoff(
    *,
    run_dir: Path,
    output: Path,
    allow_engineering_errors: bool = False,
) -> dict[str, Any]:
    """Write one gzip JSONL handoff without copying rollout artifact trees."""

    run_dir = run_dir.expanduser().resolve()
    output = output.expanduser().resolve()
    summary_path = run_dir / "summary.json"
    config_path = run_dir / "run-config.json"
    results_path = run_dir / "agent-results.jsonl"
    target_path = run_dir / "target-case-list.txt"
    for path in (summary_path, config_path, results_path, target_path):
        if not path.is_file():
            raise FileNotFoundError(path)

    summary = _read_json(summary_path)
    run_config = _read_json(config_path)
    target_case_ids = _case_ids(target_path)
    results = _result_index(_read_jsonl(results_path), target_case_ids)
    merged_dir = _resolve_merged_dir(run_dir, summary)
    merged_manifest_path = merged_dir / "run_manifest.json"
    merged_manifest = (
        _read_json(merged_manifest_path) if merged_manifest_path.is_file() else {}
    )
    provenance = _provenance_index(merged_dir)

    successful = sum(row.get("status") == "success" for row in results.values())
    engineering_errors = len(target_case_ids) - successful
    if engineering_errors and not allow_engineering_errors:
        raise ValueError(
            f"refusing incomplete judge handoff: {engineering_errors} engineering errors; "
            "rerun them or pass --allow-engineering-errors explicitly"
        )
    if int(summary.get("target_case_count", -1)) != len(target_case_ids):
        raise ValueError("summary target_case_count does not match target case list")
    if int(summary.get("successful_case_count", -1)) != successful:
        raise ValueError("summary successful_case_count does not match agent results")
    if int(summary.get("engineering_error_count", -1)) != engineering_errors:
        raise ValueError("summary engineering_error_count does not match agent results")

    output.parent.mkdir(parents=True, exist_ok=True)
    pending = output.with_name(f".{output.name}.tmp")
    manifest_record = {
        "record_type": "manifest",
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "post-hoc private-gold judge input; contains no private gold",
        "source_run_dir": str(run_dir),
        "target_case_count": len(target_case_ids),
        "successful_case_count": successful,
        "engineering_error_count": engineering_errors,
        "case_order_sha256": hashlib.sha256(
            "".join(f"{case_id}\n" for case_id in target_case_ids).encode("utf-8")
        ).hexdigest(),
        "benchmark": _benchmark_descriptor(run_config),
        "run_config": run_config,
        "summary": summary,
        "merged_run_manifest": merged_manifest,
        "content_contract": {
            "one_case_record_per_target": True,
            "successful_case_contains_complete_selected_terminal_trace": True,
            "source_attempt_provenance_included": True,
            "private_gold_included": False,
            "test_images_included": False,
            "test_image_join_key": "case_id",
            "excluded_as_not_consumed_by_judge": [
                "non-selected engineering attempts",
                "tool artifact binary files",
                "snapshot files",
                "request-context side files",
                "credentials and env files",
            ],
        },
    }

    try:
        with pending.open("wb") as raw_handle:
            with gzip.GzipFile(
                filename="",
                mode="wb",
                fileobj=raw_handle,
                compresslevel=6,
                mtime=0,
            ) as gzip_handle:
                _write_line(gzip_handle, manifest_record)
                for case_id in target_case_ids:
                    result = results[case_id]
                    case_record: dict[str, Any] = {
                        "record_type": "case",
                        "schema_version": SCHEMA_VERSION,
                        "case_id": case_id,
                        "result": result,
                        "trace_provenance": provenance.get(case_id),
                    }
                    if result.get("status") == "success":
                        relative_trace = str(result.get("trace_path") or "").strip()
                        if not relative_trace:
                            raise ValueError(f"successful result lacks trace_path: {case_id}")
                        trace_path = (merged_dir / relative_trace).resolve()
                        if not trace_path.is_relative_to(merged_dir):
                            raise ValueError(f"trace path escapes merged directory: {case_id}")
                        if not trace_path.is_file():
                            raise FileNotFoundError(trace_path)
                        trace = _read_json(trace_path)
                        trace_case_id = str(trace.get("case_id") or "").strip()
                        if trace_case_id != case_id:
                            raise ValueError(
                                f"trace case mismatch: expected={case_id!r}, got={trace_case_id!r}"
                            )
                        case_record.update(
                            {
                                "trace": trace,
                                "trace_sha256": _sha256_json(trace),
                                "source_trace_size_bytes": trace_path.stat().st_size,
                            }
                        )
                    _write_line(gzip_handle, case_record)
        pending.replace(output)
    except Exception:
        pending.unlink(missing_ok=True)
        raise

    return {
        "schema_version": SCHEMA_VERSION,
        "output": str(output),
        "output_size_bytes": output.stat().st_size,
        "output_sha256": _sha256_file(output),
        "record_count": len(target_case_ids) + 1,
        "target_case_count": len(target_case_ids),
        "successful_case_count": successful,
        "engineering_error_count": engineering_errors,
    }


def verify_agent_judge_handoff(path: Path) -> dict[str, Any]:
    """Stream-verify record count, case uniqueness, and embedded trace hashes."""

    path = path.expanduser().resolve()
    manifest: dict[str, Any] | None = None
    seen: set[str] = set()
    success_count = 0
    error_count = 0
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"line {line_number} must be a JSON object")
            if line_number == 1:
                if row.get("record_type") != "manifest":
                    raise ValueError("first record must be manifest")
                if row.get("schema_version") != SCHEMA_VERSION:
                    raise ValueError("unsupported handoff schema")
                manifest = row
                continue
            if row.get("record_type") != "case":
                raise ValueError(f"line {line_number} must be a case record")
            case_id = str(row.get("case_id") or "").strip()
            if not case_id or case_id in seen:
                raise ValueError(f"invalid or duplicate case_id at line {line_number}")
            seen.add(case_id)
            result = row.get("result")
            if not isinstance(result, dict):
                raise ValueError(f"case record lacks result at line {line_number}")
            if result.get("status") == "success":
                trace = row.get("trace")
                if not isinstance(trace, dict):
                    raise ValueError(f"successful case lacks trace at line {line_number}")
                if row.get("trace_sha256") != _sha256_json(trace):
                    raise ValueError(f"trace hash mismatch at line {line_number}")
                success_count += 1
            else:
                if row.get("trace") is not None:
                    raise ValueError(f"error case unexpectedly contains trace at line {line_number}")
                error_count += 1
    if manifest is None:
        raise ValueError("handoff is empty")
    if int(manifest.get("target_case_count", -1)) != len(seen):
        raise ValueError("manifest target_case_count mismatch")
    if int(manifest.get("successful_case_count", -1)) != success_count:
        raise ValueError("manifest successful_case_count mismatch")
    if int(manifest.get("engineering_error_count", -1)) != error_count:
        raise ValueError("manifest engineering_error_count mismatch")
    return {
        "schema_version": SCHEMA_VERSION,
        "path": str(path),
        "sha256": _sha256_file(path),
        "target_case_count": len(seen),
        "successful_case_count": success_count,
        "engineering_error_count": error_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Package or verify a compact one-file Agent judge handoff."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--run-dir", help="Completed Agent model result directory.")
    mode.add_argument("--verify", help="Existing .jsonl.gz handoff to verify.")
    parser.add_argument("--output", help="Output .jsonl.gz path for --run-dir.")
    parser.add_argument(
        "--allow-engineering-errors",
        action="store_true",
        help="Package explicit error records instead of requiring zero errors.",
    )
    args = parser.parse_args()
    if args.verify:
        result = verify_agent_judge_handoff(Path(args.verify))
    else:
        if not args.output:
            parser.error("--output is required with --run-dir")
        result = package_agent_judge_handoff(
            run_dir=Path(args.run_dir),
            output=Path(args.output),
            allow_engineering_errors=args.allow_engineering_errors,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
