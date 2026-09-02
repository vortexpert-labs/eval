#!/usr/bin/env python3
"""Self-tests for the correctness-critical pure logic in the harness.

These cover the parts where a silent bug would corrupt published results:
queue interleaving, run classification, diff accounting, and the bootstrap.
No network, no agy, no quota. Run before any batch.

    python3 harness/selftest.py
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "scoring"))
sys.path.insert(0, str(HERE.parent / "analysis"))

import config as C  # noqa: E402
import runner  # noqa: E402
import score  # noqa: E402
from analyze import paired_bootstrap  # noqa: E402

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


# ------------------------------------------------------------------ queue

def test_queue() -> None:
    print("queue interleaving")
    tasks = [
        {"task_id": f"t{i}", "repo": "pallets/click", "base_sha": "deadbeef"}
        for i in range(3)
    ]
    original = runner.load_tasks
    runner.load_tasks = lambda: tasks  # type: ignore[assignment]
    try:
        q = runner.build_queue(repeats=3, models=C.MODELS)
    finally:
        runner.load_tasks = original  # type: ignore[assignment]

    check("queue length is tasks x repeats x 2", len(q) == 3 * 3 * 2, str(len(q)))

    pairs_adjacent = all(
        q[i]["task_id"] == q[i + 1]["task_id"]
        and q[i]["repeat"] == q[i + 1]["repeat"]
        and q[i]["model"] != q[i + 1]["model"]
        for i in range(0, len(q), 2)
    )
    check("both arms of a pair are adjacent and differ", pairs_adjacent)

    firsts = [q[i]["arm"] for i in range(0, len(q), 2)]
    check("leading arm alternates A/B/B/A across pairs",
          all(firsts[i] != firsts[i + 1] for i in range(len(firsts) - 1)),
          "".join(firsts))

    counts = {m: sum(1 for e in q if e["model"] == m) for m in C.MODELS.values()}
    check("both models run equally often", len(set(counts.values())) == 1, str(counts))

    check("run ids are unique", len({e["run_id"] for e in q}) == len(q))


# ------------------------------------------------------------- classify

def test_classify() -> None:
    print("run status classification")
    ok = {"status": "SUCCESS", "response": "done", "asked_question": False}

    check("timeout wins over everything",
          runner.classify(ok, "", "diff --git a/x b/x", True) == "timeout")

    check("auto-denied tools are a harness fault, not a model failure",
          runner.classify(
              ok, "jetski: no output produced ... it was auto-denied", "", False
          ) == "harness_fault")

    check("SUCCESS with empty response and empty diff is a harness fault",
          runner.classify(
              {"status": "SUCCESS", "response": "", "asked_question": False},
              "", "", False,
          ) == "harness_fault")

    check("asking a question and stopping is question_terminated",
          runner.classify(
              {"status": "SUCCESS", "response": "which file?", "asked_question": True},
              "", "", False,
          ) == "question_terminated")

    check("envelope ERROR is a harness fault",
          runner.classify(
              {"status": "ERROR", "response": "", "asked_question": False},
              "", "", False,
          ) == "harness_fault")

    check("a real edit is an attempt",
          runner.classify(ok, "", "diff --git a/src/x.py b/src/x.py", False)
          == "attempted")

    check("empty response but real diff still counts as an attempt",
          runner.classify(
              {"status": "SUCCESS", "response": "", "asked_question": False},
              "", "diff --git a/src/x.py b/src/x.py", False,
          ) == "attempted")


# ----------------------------------------------------------------- diff

PATCH = """diff --git a/src/click/core.py b/src/click/core.py
index 111..222 100644
--- a/src/click/core.py
+++ b/src/click/core.py
@@ -1,3 +1,4 @@
 import os
-old_line
+new_line
+another_new
diff --git a/tests/test_core.py b/tests/test_core.py
index 333..444 100644
--- a/tests/test_core.py
+++ b/tests/test_core.py
@@ -1 +1 @@
-assert False
+assert True
diff --git a/setup.cfg b/setup.cfg
index 555..666 100644
--- a/setup.cfg
+++ b/setup.cfg
@@ -1 +1 @@
-a
+b
"""


def test_diff() -> None:
    print("diff accounting")
    files = score.files_in_diff(PATCH)
    check("all changed files detected", files == [
        "setup.cfg", "src/click/core.py", "tests/test_core.py"], str(files))

    check("+/- lines counted, headers excluded",
          score.lines_changed(PATCH) == 7, str(score.lines_changed(PATCH)))

    outside = score.outside_allowed(files, ["src/click/**"])
    check("files outside allowed paths flagged",
          outside == ["setup.cfg", "tests/test_core.py"], str(outside))

    profile = C.REPO_PROFILES["pallets/click"]
    tests_touched = [f for f in files if score.is_test_path(f, profile)]
    check("test tampering detected", tests_touched == ["tests/test_core.py"],
          str(tests_touched))


# ------------------------------------------------------------ bootstrap

def test_bootstrap() -> None:
    print("paired bootstrap")
    rng = random.Random(1)
    obs, lo, hi = paired_bootstrap([(0.0, 0.0)] * 10, rng)
    check("identical arms give zero difference and a zero-width CI",
          obs == 0.0 and lo == 0.0 and hi == 0.0, f"{obs} [{lo},{hi}]")

    rng = random.Random(2)
    obs, lo, hi = paired_bootstrap([(0.0, 1.0)] * 12, rng)
    check("uniform +1 difference is detected", obs == 1.0 and lo == 1.0)

    rng = random.Random(3)
    pairs = [(0.0, 1.0)] * 6 + [(1.0, 0.0)] * 6
    obs, lo, hi = paired_bootstrap(pairs, rng)
    check("symmetric disagreement gives ~0 with an interval spanning zero",
          abs(obs) < 1e-9 and lo < 0 < hi, f"{obs} [{lo},{hi}]")

    check("empty input is handled", paired_bootstrap([], rng) == (None, None, None))


def main() -> int:
    for fn in (test_queue, test_classify, test_diff, test_bootstrap):
        fn()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURES")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("all self-tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
