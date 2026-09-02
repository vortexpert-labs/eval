#!/usr/bin/env python3
"""Pre-registered analysis. No metric is computed here that is not in
PREREGISTRATION.md section 8.

Primary: task-level pass rate, tasks scored by majority of 3 repeats, compared
paired, with a 95% CI from a paired bootstrap over tasks (10,000 resamples).
If the interval contains zero the conclusion is "no detectable difference".

Secondary metrics are reported with paired bootstrap CIs and are explicitly NOT
corrected for multiplicity, so they are descriptive and hypothesis-generating.

Usage:
    python3 analysis/analyze.py --batch runs/pilot
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "harness"))
import config as C  # noqa: E402

BOOTSTRAP_RESAMPLES = 10_000
SEED = 20260902  # fixed so the published numbers are reproducible

SECONDARY = [
    ("lines_changed", "Lines changed (slop proxy)"),
    ("files_outside_allowed", "Files outside allowed paths (scope creep)"),
    ("test_files_touched", "Test files touched (reward hacking)"),
    ("num_turns", "Turns to completion"),
    ("tool_calls", "Tool calls"),
    ("wall_clock_seconds", "Wall clock seconds"),
    ("output_tokens", "Output tokens"),
    ("thinking_tokens", "Thinking tokens"),
]


def load(batch: Path) -> list[dict]:
    path = batch / "scores.csv"
    if not path.exists():
        raise SystemExit(f"no scores.csv in {batch}; run scoring/score.py first")
    with path.open() as fh:
        return list(csv.DictReader(fh))


def num(value: str | None) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def paired_bootstrap(pairs: list[tuple[float, float]], rng: random.Random) -> tuple:
    """CI for mean(B) - mean(A), resampling *pairs* with replacement."""
    if not pairs:
        return (None, None, None)
    observed = statistics.fmean(b - a for a, b in pairs)
    diffs = []
    n = len(pairs)
    for _ in range(BOOTSTRAP_RESAMPLES):
        sample = [pairs[rng.randrange(n)] for _ in range(n)]
        diffs.append(statistics.fmean(b - a for a, b in sample))
    diffs.sort()
    lo = diffs[int(0.025 * BOOTSTRAP_RESAMPLES)]
    hi = diffs[int(0.975 * BOOTSTRAP_RESAMPLES) - 1]
    return (observed, lo, hi)


def analyse(batch: Path) -> dict:
    rows = load(batch)
    arm_a, arm_b = C.MODELS["A"], C.MODELS["B"]
    rng = random.Random(SEED)

    voided = [r for r in rows if r["outcome"] == "void"]
    graded = [r for r in rows if r["outcome"] != "void"]

    # ---- pair-level integrity: a task/repeat is usable only if BOTH arms are.
    by_pair: dict[tuple[str, str], dict[str, dict]] = defaultdict(dict)
    for r in graded:
        by_pair[(r["task_id"], r["repeat"])][r["model"]] = r
    complete = {k: v for k, v in by_pair.items() if arm_a in v and arm_b in v}
    dropped_pairs = len(by_pair) - len(complete)

    # ---- primary: majority of 3 repeats per task, per model
    per_task: dict[str, dict[str, list[bool]]] = defaultdict(lambda: {arm_a: [], arm_b: []})
    for (task_id, _), arms in complete.items():
        for model, row in arms.items():
            per_task[task_id][model].append(row["hidden_tests_pass"] == "True")

    task_pairs, instability = [], []
    for task_id, arms in sorted(per_task.items()):
        if not arms[arm_a] or not arms[arm_b]:
            continue
        a_maj = sum(arms[arm_a]) * 2 > len(arms[arm_a])
        b_maj = sum(arms[arm_b]) * 2 > len(arms[arm_b])
        task_pairs.append((float(a_maj), float(b_maj)))
        for model in (arm_a, arm_b):
            reps = arms[model]
            if len(reps) > 1 and len(set(reps)) > 1:
                instability.append((task_id, model))

    obs, lo, hi = paired_bootstrap(task_pairs, rng)

    run_counts = {
        m: {
            "pass": sum(1 for r in graded if r["model"] == m and r["outcome"] == "pass"),
            "fail": sum(1 for r in graded if r["model"] == m and r["outcome"] == "fail"),
            "timeout": sum(1 for r in graded if r["model"] == m and r["outcome"] == "timeout"),
            "question_terminated": sum(
                1 for r in graded if r["model"] == m and r["outcome"] == "question_terminated"
            ),
            "void": sum(1 for r in voided if r["model"] == m),
        }
        for m in (arm_a, arm_b)
    }

    # ---- secondary metrics, paired per (task, repeat)
    secondary = {}
    for key, label in SECONDARY:
        pairs = []
        for arms in complete.values():
            a, b = num(arms[arm_a].get(key)), num(arms[arm_b].get(key))
            if a is not None and b is not None:
                pairs.append((a, b))
        o, l, h = paired_bootstrap(pairs, rng)
        secondary[key] = {
            "label": label, "n_pairs": len(pairs),
            "median_a": statistics.median([p[0] for p in pairs]) if pairs else None,
            "median_b": statistics.median([p[1] for p in pairs]) if pairs else None,
            "mean_difference_b_minus_a": o, "ci95_low": l, "ci95_high": h,
        }

    detectable = obs is not None and lo is not None and (lo > 0 or hi < 0)
    return {
        "batch": str(batch),
        "arm_a": arm_a, "arm_b": arm_b,
        "n_tasks": len(task_pairs),
        "n_runs_graded": len(graded),
        "n_runs_void": len(voided),
        "dropped_incomplete_pairs": dropped_pairs,
        "primary": {
            "metric": "task-level hidden-test pass rate (majority of repeats)",
            "pass_rate_a": statistics.fmean([p[0] for p in task_pairs]) if task_pairs else None,
            "pass_rate_b": statistics.fmean([p[1] for p in task_pairs]) if task_pairs else None,
            "difference_b_minus_a": obs,
            "ci95_low": lo, "ci95_high": hi,
            "conclusion": (
                "difference detected" if detectable else "no detectable difference"
            ),
        },
        "repeat_instability": {
            "n_task_model_cells_unstable": len(instability),
            "cells": instability,
        },
        "run_level_counts": run_counts,
        "secondary_metrics_uncorrected_for_multiplicity": secondary,
    }


def render(res: dict) -> str:
    p = res["primary"]
    lines = [
        f"# Analysis — {res['batch']}",
        "",
        f"Arm A: {res['arm_a']}    Arm B: {res['arm_b']}",
        f"Tasks: {res['n_tasks']}   graded runs: {res['n_runs_graded']}   "
        f"void: {res['n_runs_void']}   dropped incomplete pairs: "
        f"{res['dropped_incomplete_pairs']}",
        "",
        "## Primary — task-level hidden-test pass rate",
        "",
    ]
    if p["pass_rate_a"] is None:
        lines.append("No complete task pairs; primary comparison not computable.")
    else:
        lines += [
            f"  {res['arm_a']}: {p['pass_rate_a']:.3f}",
            f"  {res['arm_b']}: {p['pass_rate_b']:.3f}",
            f"  difference (B - A): {p['difference_b_minus_a']:+.3f}  "
            f"95% CI [{p['ci95_low']:+.3f}, {p['ci95_high']:+.3f}]",
            "",
            f"  **{p['conclusion'].upper()}**",
        ]
        if res["n_tasks"] < 10:
            lines += [
                "",
                f"  At n={res['n_tasks']} tasks this comparison has essentially no",
                "  power and is NOT reported as a finding.",
            ]
    lines += ["", "## Run-level counts", ""]
    for model, counts in res["run_level_counts"].items():
        lines.append(f"  {model}: " + "  ".join(f"{k}={v}" for k, v in counts.items()))
    lines += [
        "",
        f"## Repeat instability",
        "",
        f"  task/model cells whose repeats disagree: "
        f"{res['repeat_instability']['n_task_model_cells_unstable']}",
        "",
        "## Secondary metrics",
        "",
        "  Not corrected for multiplicity across "
        f"{len(res['secondary_metrics_uncorrected_for_multiplicity'])} measures.",
        "  Descriptive and hypothesis-generating; no significance is claimed.",
        "",
        f"  {'metric':<34}{'median A':>12}{'median B':>12}{'diff':>10}"
        f"{'95% CI':>24}",
    ]
    for key, s in res["secondary_metrics_uncorrected_for_multiplicity"].items():
        if s["median_a"] is None:
            continue
        ci = f"[{s['ci95_low']:+.2f}, {s['ci95_high']:+.2f}]"
        lines.append(
            f"  {s['label'][:33]:<34}{s['median_a']:>12.2f}{s['median_b']:>12.2f}"
            f"{s['mean_difference_b_minus_a']:>+10.2f}{ci:>24}"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch", type=Path, required=True)
    args = ap.parse_args()
    res = analyse(args.batch)
    (args.batch / "analysis.json").write_text(json.dumps(res, indent=2) + "\n")
    text = render(res)
    (args.batch / "analysis.md").write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
