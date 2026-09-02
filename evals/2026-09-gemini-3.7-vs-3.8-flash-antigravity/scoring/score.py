#!/usr/bin/env python3
"""Deterministic scorer. Operates only on stored artifacts.

Kept strictly separate from the runner so that runs can be re-graded — by us or
by anyone else — without re-running the agent. There is no model in this file.

For each run: rebuild a worktree at the task's base commit, apply the run's
diff.patch, apply the hidden test patch, and run the affected tests. Pass or fail.

Usage:
    python3 scoring/score.py --batch runs/pilot
    python3 scoring/score.py --batch runs/pilot --run <run_id>
"""

from __future__ import annotations

import argparse
import csv
import fnmatch
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "harness"))
import config as C  # noqa: E402
from taskbuild import is_test_path  # noqa: E402

DIFF_FILE = re.compile(r"^diff --git a/(.+?) b/(.+)$", re.MULTILINE)
HUNK_LINE = re.compile(r"^[+-](?![+-])", re.MULTILINE)


def files_in_diff(patch: str) -> list[str]:
    return sorted({m.group(2) for m in DIFF_FILE.finditer(patch)})


def lines_changed(patch: str) -> int:
    return len(HUNK_LINE.findall(patch))


def outside_allowed(paths: list[str], allowed: list[str]) -> list[str]:
    out = []
    for p in paths:
        if not any(
            fnmatch.fnmatch(p, g) or fnmatch.fnmatch(p, g.rstrip("*").rstrip("/") + "/*")
            for g in allowed
        ):
            out.append(p)
    return out


def run_cmd(cmd: str, cwd: Path, timeout: int = 900) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd, shell=True, cwd=cwd, capture_output=True, text=True,
            timeout=timeout, env=C.build_env(cwd),
        )
    except subprocess.TimeoutExpired:
        return 124, f"TIMEOUT after {timeout}s"
    return proc.returncode, (proc.stdout + proc.stderr)[-20000:]


def score_run(run_dir: Path) -> dict:
    metrics = json.loads((run_dir / "metrics.json").read_text())
    task_dir = C.TASKS_DIR / "real" / metrics["task_id"]
    lock = json.loads((task_dir / "repo.lock").read_text())
    profile = C.REPO_PROFILES[metrics["repo"]]
    allowed = [
        l.strip() for l in (task_dir / "allowed_paths.txt").read_text().splitlines()
        if l.strip()
    ]

    patch = (run_dir / "diff.patch").read_text() if (run_dir / "diff.patch").exists() else ""
    touched = files_in_diff(patch)
    test_touched = [p for p in touched if is_test_path(p, profile)]

    result = {
        "run_id": metrics["run_id"],
        "task_id": metrics["task_id"],
        "model": metrics["model"],
        "repeat": metrics.get("repeat"),
        "run_status": metrics["status"],
        "files_touched": len(touched),
        "files_outside_allowed": len(outside_allowed(touched, allowed)),
        "outside_allowed_paths": outside_allowed(touched, allowed),
        "test_files_touched": len(test_touched),
        "test_files_touched_paths": test_touched,
        "lines_changed": lines_changed(patch),
        "num_turns": metrics.get("num_turns"),
        "tool_calls": metrics.get("tool_calls"),
        "wall_clock_seconds": metrics.get("wall_clock_seconds"),
        "input_tokens": (metrics.get("usage") or {}).get("input_tokens"),
        "output_tokens": (metrics.get("usage") or {}).get("output_tokens"),
        "thinking_tokens": (metrics.get("usage") or {}).get("thinking_tokens"),
    }

    # A run that never became a genuine attempt is not graded against the tests.
    if metrics["status"] in ("harness_fault",):
        result.update(outcome="void", hidden_tests_pass=None)
        return result
    if metrics["status"] in ("timeout", "question_terminated"):
        result.update(outcome=metrics["status"], hidden_tests_pass=False)
        return result

    repo = C.REPO_CACHE / metrics["repo"].replace("/", "__")
    wt = C.WORKTREES / f"score-{metrics['run_id']}"
    if wt.exists():
        shutil.rmtree(wt, ignore_errors=True)
        C.git("worktree", "prune", cwd=repo, check=False)
    wt.parent.mkdir(parents=True, exist_ok=True)
    C.git("worktree", "add", "--quiet", "--detach", str(wt), metrics["base_sha"],
          cwd=repo)
    try:
        if patch.strip():
            (wt / ".agent.patch").write_text(patch)
            rc, log = run_cmd("git apply --whitespace=nowarn .agent.patch", wt)
            (wt / ".agent.patch").unlink(missing_ok=True)
            if rc != 0:
                result.update(outcome="void", hidden_tests_pass=None,
                              note=f"agent diff did not apply: {log[-500:]}")
                return result

        rc, log = run_cmd(f"git apply {task_dir / 'tests' / 'hidden.patch'}", wt)
        if rc != 0:
            # The agent edited the same test file region the hidden patch touches.
            result.update(outcome="void", hidden_tests_pass=None,
                          note="hidden test patch did not apply over agent diff")
            return result

        for cmd in profile.setup:
            rc, log = run_cmd(cmd, wt, timeout=1800)
            if rc != 0:
                result.update(outcome="void", hidden_tests_pass=None,
                              note=f"setup failed during scoring: {log[-500:]}")
                return result

        cmd = profile.test_cmd.format(tests=" ".join(lock["test_paths"]))
        rc, log = run_cmd(cmd, wt)
        (run_dir / "test_output.txt").write_text(log)
        passed = rc == 0
        result.update(outcome="pass" if passed else "fail", hidden_tests_pass=passed)

        if profile.typecheck_cmd:
            trc, tlog = run_cmd(profile.typecheck_cmd, wt, timeout=900)
            result["typecheck_pass"] = trc == 0
        return result
    finally:
        shutil.rmtree(wt, ignore_errors=True)
        C.git("worktree", "prune", cwd=repo, check=False)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch", type=Path, required=True)
    ap.add_argument("--run", help="score a single run id")
    args = ap.parse_args()

    dirs = sorted(d for d in args.batch.iterdir()
                  if d.is_dir() and (d / "metrics.json").exists())
    if args.run:
        dirs = [d for d in dirs if d.name == args.run]
    if not dirs:
        print("no runs to score", file=sys.stderr)
        return 1

    rows = []
    for d in dirs:
        print(f"[score] {d.name}", flush=True)
        row = score_run(d)
        (d / "score.json").write_text(json.dumps(row, indent=2) + "\n")
        rows.append(row)
        print(f"    {row['outcome']}  files={row['files_touched']} "
              f"outside={row['files_outside_allowed']} tests_touched="
              f"{row['test_files_touched']} lines={row['lines_changed']}", flush=True)

    fields = [k for k in rows[0] if not isinstance(rows[0][k], list)]
    with (args.batch / "scores.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"\n{len(rows)} runs scored -> {args.batch / 'scores.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
