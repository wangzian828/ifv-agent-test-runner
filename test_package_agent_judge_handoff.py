import gzip
import json
from pathlib import Path

import pytest

from scripts.package_agent_judge_handoff import (
    package_agent_judge_handoff,
    verify_agent_judge_handoff,
)


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def _fixture_run(tmp_path: Path, *, include_error: bool = False) -> Path:
    run_dir = tmp_path / "model-run"
    merged = run_dir / "rollouts" / "test" / "merged"
    target_ids = ["case-a"] + (["case-b"] if include_error else [])
    (run_dir / "target-case-list.txt").parent.mkdir(parents=True, exist_ok=True)
    (run_dir / "target-case-list.txt").write_text(
        "".join(f"{case_id}\n" for case_id in target_ids),
        encoding="utf-8",
    )
    benchmark = tmp_path / "cases.jsonl"
    benchmark.write_text(
        "".join(json.dumps({"case_id": case_id}) + "\n" for case_id in target_ids),
        encoding="utf-8",
    )
    _write_json(
        run_dir / "run-config.json",
        {
            "benchmark": str(benchmark),
            "profile": "test-profile",
            "target_case_count": len(target_ids),
        },
    )
    _write_json(
        run_dir / "summary.json",
        {
            "merged_dir": str(merged),
            "target_case_count": len(target_ids),
            "successful_case_count": 1,
            "engineering_error_count": int(include_error),
        },
    )
    trace = {
        "case_id": "case-a",
        "termination": "success",
        "verdict": "fake",
        "events": [
            {"role": "assistant", "tool_call": {"name": "text_search"}},
            {"role": "tool", "content": {"result": "complete raw response"}},
        ],
    }
    _write_json(merged / "traces" / "case-a.json", trace)
    _write_json(merged / "run_manifest.json", {"git_commit": "abc123"})
    _write_jsonl(
        merged / "trace-provenance.jsonl",
        [
            {
                "case_id": "case-a",
                "source_attempt": "attempt-02",
                "source_trace": "/source/attempt-02/traces/case-a.json",
            }
        ],
    )
    results = [
        {
            "case_id": "case-a",
            "status": "success",
            "trace_path": "traces/case-a.json",
            "verdict": "fake",
            "source_attempt": "attempt-02",
        }
    ]
    if include_error:
        results.append(
            {
                "case_id": "case-b",
                "status": "error",
                "error": "engineering attempts exhausted",
            }
        )
    _write_jsonl(run_dir / "agent-results.jsonl", results)
    # These model large artifact trees that must not be copied into judge handoff.
    (run_dir / "rollouts" / "test" / "attempt-01" / "artifacts").mkdir(
        parents=True
    )
    (run_dir / "rollouts" / "test" / "attempt-01" / "artifacts" / "crop.jpg").write_bytes(
        b"not consumed by judge"
    )
    return run_dir


def test_packages_complete_selected_trace_into_one_file(tmp_path: Path):
    run_dir = _fixture_run(tmp_path)
    output = tmp_path / "model-judge-handoff.jsonl.gz"

    packaged = package_agent_judge_handoff(run_dir=run_dir, output=output)
    verified = verify_agent_judge_handoff(output)

    assert packaged["record_count"] == 2
    assert verified["target_case_count"] == 1
    assert verified["engineering_error_count"] == 0
    assert list(tmp_path.glob("*judge-handoff*")) == [output]
    with gzip.open(output, "rt", encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle]
    assert rows[0]["record_type"] == "manifest"
    assert rows[0]["content_contract"]["test_images_included"] is False
    assert rows[1]["trace"]["events"][1]["content"]["result"] == (
        "complete raw response"
    )
    assert rows[1]["trace_provenance"]["source_attempt"] == "attempt-02"
    assert "crop.jpg" not in output.read_bytes().decode("latin1")


def test_refuses_engineering_errors_by_default(tmp_path: Path):
    run_dir = _fixture_run(tmp_path, include_error=True)
    output = tmp_path / "incomplete.jsonl.gz"

    with pytest.raises(ValueError, match="refusing incomplete judge handoff"):
        package_agent_judge_handoff(run_dir=run_dir, output=output)

    packaged = package_agent_judge_handoff(
        run_dir=run_dir,
        output=output,
        allow_engineering_errors=True,
    )
    assert packaged["engineering_error_count"] == 1
    assert verify_agent_judge_handoff(output)["engineering_error_count"] == 1
