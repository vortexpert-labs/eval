#!/usr/bin/env python3
"""Execute the interleaved run queue against the frozen Antigravity binary.

One run = one task, one model, one repeat index. Each run gets a fresh git
worktree at the task's base commit and a freshly restored eval HOME, so no state
carries between runs. Dependencies are installed *before* the agent starts, so
wall-clock time measures the agent rather than npm.

Artifacts captured per run: transcript.jsonl (stream-json), stderr.log,
diff.patch, metrics.json. Scoring is a separate program operating only on these.

Usage:
    python3 harness/runner.py queue --repeats 3 --out runs/pilot/queue.json
    python3 harness/runner.py run   --queue runs/pilot/queue.json
    python3 harness/runner.py run   --queue runs/pilot/queue.json --calibrate
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as C  # noqa: E402


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------- queue


def load_tasks() -> list[dict]:
    tasks = []
    root = C.TASKS_DIR / "real"
    if not root.exists():
        return tasks
    for lock in sorted(root.glob("*/repo.lock")):
        data = json.loads(lock.read_text())
        if data.get("status") == "accepted":
            tasks.append(data)
    return tasks


def build_queue(repeats: int, models: dict[str, str]) -> list[dict]:
    """Interleaved A/B/B/A with pair adjacency.

    The two runs of a (task, repeat) pair are always adjacent. Successive pairs
    alternate which model goes first. Adjacency is what decorrelates model from
    serving conditions; the queue may span days without weakening that.
    """
    tasks = load_tasks()
    if not tasks:
        raise SystemExit("no accepted tasks; run taskbuild.py first")
    queue: list[dict] = []
    pair_index = 0
    for repeat in range(1, repeats + 1):
        for task in tasks:
            order = ["A", "B"] if pair_index % 2 == 0 else ["B", "A"]
            for arm in order:
                queue.append(
                    {
                        "run_id": f"{task['task_id']}__{models[arm]}__r{repeat}",
                        "task_id": task["task_id"],
                        "repo": task["repo"],
                        "base_sha": task["base_sha"],
                        "model": models[arm],
                        "arm": arm,
                        "repeat": repeat,
                        "pair_index": pair_index,
                    }
                )
            pair_index += 1
    return queue


# --------------------------------------------------------------- execution


def reset_eval_home() -> None:
    """Restore the eval HOME from the pristine authenticated snapshot.

    Without this the agent home accumulates conversation, brain and implicit
    memory across runs, so run 100 would execute against a different scaffold
    than run 1.
    """
    if not C.PRISTINE_HOME.exists():
        raise SystemExit(f"pristine snapshot missing: {C.PRISTINE_HOME}")
    if C.EVAL_HOME.exists():
        shutil.rmtree(C.EVAL_HOME)
    shutil.copytree(C.PRISTINE_HOME, C.EVAL_HOME, symlinks=True)
    token = C.EVAL_HOME / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
    if token.exists():
        token.chmod(0o600)


def make_worktree(repo_slug: str, base_sha: str, run_id: str) -> Path:
    repo = C.REPO_CACHE / repo_slug.replace("/", "__")
    wt = C.WORKTREES / run_id
    if wt.exists():
        shutil.rmtree(wt, ignore_errors=True)
        C.git("worktree", "prune", cwd=repo, check=False)
    wt.parent.mkdir(parents=True, exist_ok=True)
    C.git("worktree", "add", "--quiet", "--detach", str(wt), base_sha, cwd=repo)
    return wt


def prepare_worktree(wt: Path, profile: C.RepoProfile) -> tuple[bool, str]:
    for cmd in profile.setup:
        proc = subprocess.run(
            cmd, shell=True, cwd=wt, capture_output=True, text=True,
            timeout=1800, env=C.build_env(wt),
        )
        if proc.returncode != 0:
            return False, f"setup failed: {cmd}\n{(proc.stdout + proc.stderr)[-3000:]}"
    return True, ""


def capture_diff(wt: Path) -> str:
    """Working-tree diff including untracked files, excluding build artifacts."""
    C.git("add", "-A", "--", ".", cwd=wt, check=False)
    return C.git(
        "diff", "--cached", "--",
        ".", ":(exclude).eval-venv", ":(exclude)node_modules", ":(exclude)dist",
        cwd=wt, check=False,
    )


def parse_stream(transcript: Path) -> dict:
    """Pull the result envelope and tool-call count out of a stream-json log."""
    out = {
        "status": None, "response": "", "num_turns": None,
        "duration_seconds": None, "usage": {}, "tool_calls": 0,
        "asked_question": False, "conversation_id": None,
    }
    if not transcript.exists():
        return out
    for line in transcript.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        etype = event.get("type") or ("result" if "status" in event else None)
        blob = json.dumps(event)
        if '"ask_question"' in blob:
            out["asked_question"] = True
        if etype == "step_update" or "tool" in blob.lower():
            out["tool_calls"] += blob.count('"tool_name"') or 0
        if "status" in event and "usage" in event:
            out["status"] = event.get("status")
            out["response"] = event.get("response") or ""
            out["num_turns"] = event.get("num_turns")
            out["duration_seconds"] = event.get("duration_seconds")
            out["usage"] = event.get("usage") or {}
            out["conversation_id"] = event.get("conversation_id")
    return out


def classify(parsed: dict, stderr: str, diff: str, timed_out: bool) -> str:
    """Registered status taxonomy. Misclassification here corrupts the metric."""
    if timed_out:
        return "timeout"
    if "jetski:" in stderr and "auto-denied" in stderr:
        return "harness_fault"
    if parsed["status"] in (None, "ERROR", "INVALID", "CANCELED", "INTERRUPTED"):
        return "harness_fault"
    if not diff.strip():
        if parsed["asked_question"]:
            return "question_terminated"
        if not parsed["response"].strip():
            return "harness_fault"
    return "attempted"


def execute(entry: dict, out_dir: Path, model_override: str | None = None) -> dict:
    C.verify_binary()
    model = model_override or entry["model"]
    profile = C.REPO_PROFILES[entry["repo"]]
    task_dir = C.TASKS_DIR / "real" / entry["task_id"]
    prompt = (task_dir / "prompt.md").read_text()

    out_dir.mkdir(parents=True, exist_ok=True)
    transcript = out_dir / "transcript.jsonl"
    stderr_log = out_dir / "stderr.log"

    reset_eval_home()
    wt = make_worktree(entry["repo"], entry["base_sha"], entry["run_id"])
    ok, why = prepare_worktree(wt, profile)
    if not ok:
        (out_dir / "metrics.json").write_text(
            json.dumps({**entry, "status": "harness_fault", "reason": why}, indent=2)
        )
        shutil.rmtree(wt, ignore_errors=True)
        return {**entry, "status": "harness_fault"}

    cmd = [
        str(C.AGY_BIN), "-p", prompt,
        "--model", model,
        "--output-format", "stream-json",
        "--dangerously-skip-permissions",
        "--print-timeout", f"{C.RUN_TIMEOUT_SECONDS // 60}m",
        "--new-project",
        "--mode", "accept-edits",
        "--disable-slash-commands",
    ]

    started, t0, timed_out = now(), time.monotonic(), False
    with transcript.open("w") as so, stderr_log.open("w") as se:
        proc = subprocess.Popen(cmd, cwd=wt, stdout=so, stderr=se, env=C.agent_env())
        try:
            proc.wait(timeout=C.RUN_TIMEOUT_SECONDS + C.WATCHDOG_MARGIN_SECONDS)
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.kill()
            proc.wait(timeout=30)
    wall = time.monotonic() - t0
    ended = now()

    diff = capture_diff(wt)
    (out_dir / "diff.patch").write_text(diff)
    parsed = parse_stream(transcript)
    stderr_text = stderr_log.read_text(errors="replace")
    status = classify(parsed, stderr_text, diff, timed_out)

    tokens = parsed["usage"].get("input_tokens")
    drift = (
        abs(tokens - C.BASELINE_INPUT_TOKENS) > C.BASELINE_TOLERANCE
        if isinstance(tokens, int) else None
    )

    metrics = {
        **entry, "model": model, "status": status,
        "started_utc": started, "ended_utc": ended,
        "wall_clock_seconds": round(wall, 3),
        "agy_duration_seconds": parsed["duration_seconds"],
        "num_turns": parsed["num_turns"], "tool_calls": parsed["tool_calls"],
        "usage": parsed["usage"], "scaffold_drift": drift,
        "asked_question": parsed["asked_question"],
        "envelope_status": parsed["status"],
        "response_empty": not parsed["response"].strip(),
        "diff_empty": not diff.strip(),
        "conversation_id": parsed["conversation_id"],
        "agy_sha256": C.AGY_SHA256,
    }
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")

    shutil.rmtree(wt, ignore_errors=True)
    C.git("worktree", "prune", cwd=C.REPO_CACHE / entry["repo"].replace("/", "__"),
          check=False)
    C.verify_binary()
    return metrics


def write_env_lock(batch: Path) -> None:
    batch.mkdir(parents=True, exist_ok=True)
    def ver(cmd: list[str]) -> str:
        try:
            return subprocess.run(
                cmd, capture_output=True, text=True, env=C.agent_env(), timeout=60
            ).stdout.strip().splitlines()[0]
        except Exception:  # noqa: BLE001
            return "unavailable"
    (batch / "env.lock").write_text(json.dumps({
        "recorded_utc": now(),
        "agy_binary": str(C.AGY_BIN),
        "agy_sha256": C.AGY_SHA256,
        "agy_version": ver([str(C.AGY_BIN), "--version"]),
        "eval_home": str(C.EVAL_HOME),
        "pristine_home": str(C.PRISTINE_HOME),
        "pinned_path": C.PINNED_PATH,
        "python": ver(["python3", "--version"]),
        "node": ver(["node", "--version"]),
        "bun": ver(["bun", "--version"]),
        "git": ver(["git", "--version"]),
        "uv": ver(["uv", "--version"]),
        "macos": ver(["sw_vers", "-productVersion"]),
        "baseline_input_tokens": C.BASELINE_INPUT_TOKENS,
        "models": C.MODELS,
        "run_timeout_seconds": C.RUN_TIMEOUT_SECONDS,
    }, indent=2) + "\n")


def append_runlog(metrics: dict) -> None:
    C.RECORDING_DIR.mkdir(parents=True, exist_ok=True)
    path = C.RECORDING_DIR / "runlog.csv"
    new = not path.exists()
    fields = ["run_id", "task_id", "model", "repeat", "status",
              "started_utc", "ended_utc", "wall_clock_seconds", "num_turns"]
    with path.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow(metrics)


# ----------------------------------------------------------------------- cli


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    q = sub.add_parser("queue")
    q.add_argument("--repeats", type=int, default=3)
    q.add_argument("--out", type=Path, required=True)

    r = sub.add_parser("run")
    r.add_argument("--queue", type=Path, required=True)
    r.add_argument("--limit", type=int, help="stop after N runs (smoke testing)")
    r.add_argument("--calibrate", action="store_true",
                   help="run every task once with the calibrator model instead")

    args = ap.parse_args()

    if args.cmd == "queue":
        queue = build_queue(args.repeats, C.MODELS)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(queue, indent=2) + "\n")
        print(f"{len(queue)} runs -> {args.out}")
        return 0

    problems = C.preflight()
    if problems:
        for p in problems:
            print(f"  preflight: {p}", file=sys.stderr)
        return 1

    queue = json.loads(args.queue.read_text())
    batch = args.queue.parent
    write_env_lock(batch)

    if args.calibrate:
        seen, cal = set(), []
        for e in queue:
            if e["task_id"] not in seen:
                seen.add(e["task_id"])
                cal.append({**e, "run_id": f"{e['task_id']}__calibrate",
                            "model": C.CALIBRATOR})
        queue = cal

    done = skipped = 0
    for i, entry in enumerate(queue, 1):
        if args.limit and done >= args.limit:
            break
        out_dir = batch / entry["run_id"]
        if (out_dir / "metrics.json").exists():
            skipped += 1
            continue
        print(f"[{i}/{len(queue)}] {entry['run_id']}", flush=True)
        metrics = execute(entry, out_dir)
        append_runlog(metrics)
        print(f"    {metrics['status']}  {metrics.get('wall_clock_seconds')}s  "
              f"turns={metrics.get('num_turns')}", flush=True)
        if metrics.get("scaffold_drift"):
            print("    WARNING scaffold drift: input_tokens outside tolerance",
                  file=sys.stderr)
        done += 1

    print(f"\ncompleted {done}, skipped {skipped} already-present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
