#!/usr/bin/env python3
"""Recoverable engineering retries for evaluator-only Agent test runs."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "ifv-agent-test-retry-v1"
VALID_VERDICTS = frozenset({"real", "fake"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _runtime_command(*arguments: str) -> list[str]:
    configured = os.environ.get("IFV_SERVER_RUNNER", "").strip()
    runner = (
        Path(configured).expanduser().resolve()
        if configured
        else REPO_ROOT / "scripts" / "server" / "run_ifv.sh"
    )
    return [str(runner), sys.executable, *arguments]


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


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(f".{path.name}.tmp")
    pending.write_text(
        json.dumps(dict(value), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    pending.replace(path)


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(f".{path.name}.tmp")
    with pending.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True))
            handle.write("\n")
    pending.replace(path)


def _write_case_list(path: Path, case_ids: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(f"{case_id}\n" for case_id in case_ids),
        encoding="utf-8",
    )


def _trace_case_id(trace: Mapping[str, Any]) -> str:
    state = trace.get("state")
    runtime_case = state.get("runtime_case") if isinstance(state, Mapping) else None
    case_id = runtime_case.get("case_id") if isinstance(runtime_case, Mapping) else ""
    case_id = str(case_id or trace.get("case_id") or "").strip()
    if not case_id:
        raise ValueError("trace lacks state.runtime_case.case_id")
    return case_id


def _trace_episode_id(trace: Mapping[str, Any]) -> str:
    state = trace.get("state")
    episode_id = str(
        trace.get("image_id")
        or (state.get("image_id") if isinstance(state, Mapping) else "")
        or ""
    ).strip()
    if not episode_id:
        raise ValueError("trace lacks image_id")
    return episode_id


def _terminal_success(trace: Mapping[str, Any]) -> bool:
    return (
        str(trace.get("termination") or "") == "success"
        and str(trace.get("verdict") or "").lower() in VALID_VERDICTS
    )


def _trace_paths(run_dir: Path) -> list[Path]:
    return sorted((run_dir / "traces").glob("*.json"))


def _trace_summary(trace: Mapping[str, Any]) -> dict[str, str]:
    return {
        "case_id": _trace_case_id(trace),
        "episode_id": _trace_episode_id(trace),
        "termination": str(trace.get("termination") or ""),
        "verdict": str(trace.get("verdict") or "").lower(),
    }


def _attempt_dirs(group_dir: Path) -> list[Path]:
    candidates: list[tuple[int, Path]] = []
    for path in group_dir.glob("attempt-*"):
        if not path.is_dir():
            continue
        try:
            number = int(path.name.removeprefix("attempt-"))
        except ValueError:
            continue
        candidates.append((number, path))
    return [path for _, path in sorted(candidates)]


def _successful_trace_sources(
    attempt_dirs: Sequence[Path],
) -> dict[str, tuple[Path, Path, dict[str, str]]]:
    selected: dict[str, tuple[Path, Path, dict[str, str]]] = {}
    for attempt_dir in attempt_dirs:
        seen_in_attempt: set[str] = set()
        for trace_path in _trace_paths(attempt_dir):
            trace = _read_json(trace_path)
            if not _terminal_success(trace):
                continue
            summary = _trace_summary(trace)
            case_id = summary["case_id"]
            if case_id in seen_in_attempt:
                raise ValueError(f"duplicate case trace in {attempt_dir}: {case_id}")
            seen_in_attempt.add(case_id)
            selected.setdefault(case_id, (attempt_dir, trace_path, summary))
    return selected


def _copy_or_link(source: Path, destination: Path) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
        return "hardlink"
    except OSError:
        shutil.copy2(source, destination)
        return "copy"


def _inherited_run_manifest(attempts: Sequence[Path]) -> dict[str, Any]:
    for attempt_dir in attempts:
        manifest_path = attempt_dir / "run_manifest.json"
        if manifest_path.is_file():
            return _read_json(manifest_path)
    return {}


def _merge_successful_attempts(
    *,
    group_dir: Path,
    group_name: str,
    target_ids: Sequence[str],
) -> tuple[Path, dict[str, Any]]:
    merged_dir = group_dir / "merged"
    manifest_path = merged_dir / "run_manifest.json"
    if manifest_path.is_file():
        return merged_dir, _read_json(manifest_path)
    if merged_dir.exists() and any(merged_dir.iterdir()):
        raise FileExistsError(f"refusing to overwrite partial merged run: {merged_dir}")

    attempts = _attempt_dirs(group_dir)
    selected = _successful_trace_sources(attempts)
    target_set = set(target_ids)
    unexpected = sorted(set(selected) - target_set)
    if unexpected:
        raise ValueError(f"{group_name} traces include unexpected case IDs: {unexpected[:3]}")

    unresolved = sorted(target_set - set(selected))
    trace_root = merged_dir / "traces"
    trace_root.mkdir(parents=True, exist_ok=True)
    materialization = {"hardlink": 0, "copy": 0}
    provenance: list[dict[str, Any]] = []
    for case_id in sorted(selected):
        attempt_dir, source_path, summary = selected[case_id]
        destination = trace_root / source_path.name
        if destination.exists():
            raise FileExistsError(f"duplicate merged trace path: {destination}")
        method = _copy_or_link(source_path, destination)
        materialization[method] += 1
        provenance.append(
            {
                "case_id": case_id,
                "episode_id": summary["episode_id"],
                "source_attempt": attempt_dir.name,
                "source_run": str(attempt_dir),
                "source_trace": str(source_path),
                "verdict": summary["verdict"],
            }
        )

    inherited_manifest = _inherited_run_manifest(attempts)
    manifest = {
        "schema_version": "ifv-merged-agent-test-rollout-v1",
        "run_id": merged_dir.name,
        "status": "completed" if not unresolved else "completed_with_errors",
        "started_at": _now(),
        "completed_at": _now(),
        "git_commit": inherited_manifest.get("git_commit"),
        "benchmark": inherited_manifest.get("benchmark"),
        "agent": inherited_manifest.get("agent"),
        "source_access_policy": inherited_manifest.get(
            "source_access_policy",
            {"active": False},
        ),
        "metadata": {
            "kind": "terminal-success-merge",
            "group": group_name,
            "target_case_count": len(target_ids),
            "merged_success_count": len(selected),
            "attempt_dirs": [str(path) for path in attempts],
            "trace_materialization": materialization,
        },
        "result": {
            "num_cases": len(target_ids),
            "num_episodes": len(selected),
            "num_errors": len(unresolved),
            "status_distribution": {
                "success": len(selected),
                "error": len(unresolved),
            },
        },
        "artifacts": {
            "traces": "traces/",
            "trace_provenance": "trace-provenance.jsonl",
            "unresolved_engineering": "unresolved-engineering-case-list.txt",
        },
    }
    _write_jsonl(merged_dir / "trace-provenance.jsonl", provenance)
    _write_case_list(merged_dir / "unresolved-engineering-case-list.txt", unresolved)
    _write_json(manifest_path, manifest)
    return merged_dir, manifest


def _attempt_command_log_path(group_dir: Path, attempt_number: int) -> Path:
    return group_dir / "logs" / f"attempt-{attempt_number:02d}.log"


def _run_command(
    command: Sequence[str],
    *,
    cwd: Path,
    log_path: Path,
    env: Mapping[str, str],
) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8", newline="\n") as log:
        log.write(f"\n[{_now()}] command={json.dumps(list(command))}\n")
        log.flush()
        completed = subprocess.run(
            list(command),
            cwd=str(cwd),
            env=dict(env),
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        )
        log.write(f"[{_now()}] returncode={completed.returncode}\n")
    return int(completed.returncode)


def _run_engineering_retries(
    *,
    group_name: str,
    benchmark: Path,
    pipeline_dir: Path,
    target_ids: Sequence[str],
    profile: str,
    rollout_concurrency: int,
    base_seed: int,
    timeout: float,
    maximum_attempts: int,
    candidates_per_case: int = 1,
) -> tuple[Path, dict[str, Any]]:
    if maximum_attempts < 1:
        raise ValueError("maximum engineering attempts must be at least 1")
    if candidates_per_case != 1:
        raise ValueError("Agent test runner supports exactly one candidate per case")

    group_dir = pipeline_dir / "rollouts" / group_name
    group_dir.mkdir(parents=True, exist_ok=True)
    target_ids = list(target_ids)
    if len(target_ids) != len(set(target_ids)):
        raise ValueError(f"{group_name} target case IDs must be unique")
    _write_case_list(group_dir / "target-case-list.txt", target_ids)

    attempt_dirs = _attempt_dirs(group_dir)
    next_attempt = (
        max(int(path.name.removeprefix("attempt-")) for path in attempt_dirs) + 1
        if attempt_dirs
        else 1
    )
    for _ in range(maximum_attempts):
        selected = _successful_trace_sources(_attempt_dirs(group_dir))
        pending = [case_id for case_id in target_ids if case_id not in selected]
        _write_case_list(group_dir / "pending-case-list.txt", pending)
        if not pending:
            break

        attempt_number = next_attempt
        next_attempt += 1
        attempt_dir = group_dir / f"attempt-{attempt_number:02d}"
        case_list_path = group_dir / f"attempt-{attempt_number:02d}-case-list.txt"
        _write_case_list(case_list_path, pending)
        command = _runtime_command(
            "-m",
            "src.eval.run_cases",
            "--benchmark",
            str(benchmark),
            "--output-dir",
            str(attempt_dir),
            "--profile",
            profile,
            "--concurrency",
            str(rollout_concurrency),
            "--rollouts-per-case",
            "1",
            "--episode-namespace",
            f"{group_name}-a{attempt_number:02d}",
            "--base-sampling-seed",
            str(base_seed + attempt_number - 1),
            "--timeout",
            str(timeout),
            "--skip-preflight-image-hash-verification",
            "--case-list",
            str(case_list_path),
        )
        env = os.environ.copy()
        env.update(
            {
                "OMP_NUM_THREADS": "1",
                "GEMINI_EVAL_MAX_CONCURRENCY": str(rollout_concurrency),
                "GEMINI_MAX_INFLIGHT_REQUESTS": str(rollout_concurrency),
                "PYTHONUNBUFFERED": "1",
            }
        )
        returncode = _run_command(
            command,
            cwd=REPO_ROOT,
            log_path=_attempt_command_log_path(group_dir, attempt_number),
            env=env,
        )
        _write_json(
            attempt_dir / "agent-test-attempt.json",
            {
                "schema_version": SCHEMA_VERSION,
                "group": group_name,
                "attempt": attempt_number,
                "started_case_count": len(pending),
                "base_sampling_seed": base_seed + attempt_number - 1,
                "returncode": returncode,
                "completed_at": _now(),
                "summary_exists": (attempt_dir / "summary.json").is_file(),
            },
        )

    selected = _successful_trace_sources(_attempt_dirs(group_dir))
    unresolved = [case_id for case_id in target_ids if case_id not in selected]
    retry_state = {
        "schema_version": SCHEMA_VERSION,
        "group": group_name,
        "target_case_count": len(target_ids),
        "successful_case_count": len(target_ids) - len(unresolved),
        "unresolved_engineering_case_count": len(unresolved),
        "maximum_attempts": maximum_attempts,
        "attempt_dirs": [str(path) for path in _attempt_dirs(group_dir)],
        "completed_at": _now(),
    }
    _write_json(group_dir / "engineering-retry-state.json", retry_state)
    _write_case_list(group_dir / "unresolved-engineering-case-list.txt", unresolved)
    return _merge_successful_attempts(
        group_dir=group_dir,
        group_name=group_name,
        target_ids=target_ids,
    )
