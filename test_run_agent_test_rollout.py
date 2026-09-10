import json
from pathlib import Path

import pytest

from scripts.agent_test_retry import _merge_successful_attempts
from scripts.run_agent_test_rollout import _build_agent_results


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_build_agent_results_includes_success_and_engineering_error(tmp_path: Path):
    merged = tmp_path / "merged"
    trace_path = merged / "traces" / "case-a.json"
    _write_json(
        trace_path,
        {
            "case_id": "case-a",
            "termination": "success",
            "verdict": "fake",
            "confidence": 0.9,
        },
    )
    _write_jsonl(
        merged / "trace-provenance.jsonl",
        [
            {
                "case_id": "case-a",
                "source_trace": "/source/attempt-01/traces/case-a.json",
                "source_attempt": "attempt-01",
            }
        ],
    )

    rows = _build_agent_results(
        merged_dir=merged,
        target_case_ids=["case-a", "case-b"],
    )

    assert rows[0]["status"] == "success"
    assert rows[0]["trace_path"] == "traces/case-a.json"
    assert rows[0]["training_prohibited"] is True
    assert rows[1] == {
        "case_id": "case-b",
        "status": "error",
        "error": "engineering attempts exhausted without terminal success",
        "training_prohibited": True,
    }


def test_build_agent_results_rejects_non_terminal_merged_trace(tmp_path: Path):
    merged = tmp_path / "merged"
    _write_json(
        merged / "traces" / "case-a.json",
        {"case_id": "case-a", "termination": "timeout", "verdict": ""},
    )
    _write_jsonl(
        merged / "trace-provenance.jsonl",
        [{"case_id": "case-a", "source_trace": "/source/case-a.json"}],
    )

    with pytest.raises(ValueError, match="not terminal success"):
        _build_agent_results(merged_dir=merged, target_case_ids=["case-a"])


def test_retry_merge_adds_new_success_without_replacing_existing(tmp_path: Path):
    group = tmp_path / "rollouts" / "test"
    for attempt, case_id in (("attempt-01", "case-a"), ("attempt-02", "case-b")):
        trace_path = group / attempt / "traces" / f"{case_id}.json"
        _write_json(
            trace_path,
            {
                "case_id": case_id,
                "image_id": f"episode-{case_id}",
                "termination": "success",
                "verdict": "real",
            },
        )
        _write_json(
            group / attempt / "run_manifest.json",
            {"git_commit": "test", "benchmark": "cases.jsonl"},
        )
        if case_id == "case-a":
            merged, _ = _merge_successful_attempts(
                group_dir=group,
                group_name="test",
                target_ids=["case-a", "case-b"],
            )
            assert len(_read_jsonl(merged / "trace-provenance.jsonl")) == 1

    merged, manifest = _merge_successful_attempts(
        group_dir=group,
        group_name="test",
        target_ids=["case-a", "case-b"],
    )

    assert manifest["result"]["num_errors"] == 0
    assert len(_read_jsonl(merged / "trace-provenance.jsonl")) == 2
