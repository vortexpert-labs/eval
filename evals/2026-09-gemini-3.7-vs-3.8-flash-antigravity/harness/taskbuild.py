#!/usr/bin/env python3
"""Build and verify eval tasks from merged pull requests.

A task is constructed by checking out a PR's base commit, keeping the source
untouched, and holding the PR's test changes back as a *hidden* patch. The agent
receives only a problem statement. Scoring later applies the hidden tests.

A task is only accepted if all three of these hold:

  1. the affected tests PASS at base without the hidden patch  (environment sane)
  2. the affected tests FAIL at base with the hidden patch     (task is real)
  3. the affected tests PASS with the PR's own source changes  (task is solvable)

Anything else is dropped and the reason recorded. There are no workarounds.

Usage:
    python3 harness/taskbuild.py build  --repo pallets/click --pr 3805
    python3 harness/taskbuild.py build  --spec harness/candidates.json
    python3 harness/taskbuild.py report
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as C  # noqa: E402

SOURCE_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx"}

#: A pull request body is written *after* the fix, by the person who wrote it, and
#: routinely explains the solution. Using it as a problem statement leaks the answer
#: unevenly across tasks. Prefer the linked issue, which predates the fix.
ISSUE_REF = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s*#(\d+)", re.IGNORECASE
)

#: Phrases that indicate an author describing their own change rather than the
#: symptom. A statement matching any of these is rejected as solution leakage.
LEAK_PATTERNS = [
    (re.compile(r"^#{1,6}\s*(the\s+)?fix\b", re.IGNORECASE | re.MULTILINE), "fix-section"),
    (re.compile(r"^#{1,6}\s*(the\s+)?(cause|solution|approach|changes?)\b",
                re.IGNORECASE | re.MULTILINE), "solution-section"),
    (re.compile(r"\bthe fix\b", re.IGNORECASE), "the-fix-phrase"),
    (re.compile(r"\b(?:I|we)\s+(?:changed|fixed|added|patched|refactored)\b",
                re.IGNORECASE), "author-narrative"),
    (re.compile(r"```diff", re.IGNORECASE), "diff-block"),
    (re.compile(r"^#{1,6}\s*tests?\b", re.IGNORECASE | re.MULTILINE), "tests-section"),
    (re.compile(r"\b(?:added|adds)\s+(?:a\s+)?(?:new\s+)?tests?\b", re.IGNORECASE),
     "describes-added-tests"),
]


def leak_scan(text: str) -> list[str]:
    """Flags indicating the statement describes the solution rather than the bug."""
    return sorted({name for pattern, name in LEAK_PATTERNS if pattern.search(text)})


def linked_issue(slug: str, pr: dict) -> dict | None:
    """The issue this PR closes, if any. Written before the fix, so far safer."""
    match = ISSUE_REF.search(pr.get("body") or "")
    if not match:
        return None
    try:
        issue = gh_json(f"repos/{slug}/issues/{match.group(1)}")
    except RuntimeError:
        return None
    if "pull_request" in issue:  # the reference pointed at another PR
        return None
    return issue


# ---------------------------------------------------------------- classification


def is_test_path(path: str, profile: C.RepoProfile) -> bool:
    name = Path(path).name
    if name.startswith("test_") or name.endswith("_test.py"):
        return True
    for suffix in (".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx", ".test.js"):
        if name.endswith(suffix):
            return True
    for glob in profile.test_globs:
        if fnmatch.fnmatch(path, glob) or fnmatch.fnmatch(path, glob.replace("**/", "")):
            return True
    return path.startswith("tests/") or "/tests/" in path


def is_runnable_test(path: str, profile: C.RepoProfile) -> bool:
    """Can this path be handed to the project's test runner?

    Distinct from `is_test_path`. A repository's test tree also holds fixtures —
    expected-output `.txt` files, sample source scripts, mypy typing stubs — which
    belong in the hidden patch (the test cannot pass without them) but are not
    themselves runnable targets. Passing one to pytest or vitest fails collection.
    """
    name = Path(path).name
    if Path(path).suffix == ".py":
        return name.startswith("test_") or name.endswith("_test.py")
    for suffix in (".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx", ".test.js"):
        if name.endswith(suffix):
            return True
    return False


def is_source_path(path: str, profile: C.RepoProfile) -> bool:
    if is_test_path(path, profile):
        return False
    return Path(path).suffix in SOURCE_EXTENSIONS


# ---------------------------------------------------------------------- git


def ensure_clone(slug: str) -> Path:
    dest = C.REPO_CACHE / slug.replace("/", "__")
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        print(f"    cloning {slug} ...", flush=True)
        C.git("clone", "--quiet", f"https://github.com/{slug}.git", str(dest))
    else:
        C.git("fetch", "--quiet", "origin", cwd=dest, check=False)
    return dest


def fetch_commit(repo: Path, sha: str) -> None:
    if C.git("cat-file", "-t", sha, cwd=repo, check=False).strip() == "commit":
        return
    C.git("fetch", "--quiet", "origin", sha, cwd=repo, check=False)
    if C.git("cat-file", "-t", sha, cwd=repo, check=False).strip() != "commit":
        raise RuntimeError(f"cannot fetch commit {sha} in {repo.name}")


def gh_json(endpoint: str) -> dict:
    proc = subprocess.run(
        ["gh", "api", endpoint], capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise RuntimeError(f"gh api {endpoint} failed: {proc.stderr.strip()}")
    return json.loads(proc.stdout)


# ------------------------------------------------------------------ execution


def run(cmd: str, cwd: Path, timeout: int = 900) -> tuple[int, str]:
    """Run a shell command in a worktree with the pinned environment."""
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=C.build_env(cwd),
        )
    except subprocess.TimeoutExpired:
        return 124, f"TIMEOUT after {timeout}s: {cmd}"
    return proc.returncode, (proc.stdout + proc.stderr)[-20000:]


# ---------------------------------------------------------------------- build


def build_task(slug: str, pr_number: int, verify: bool = True) -> dict:
    profile = C.REPO_PROFILES.get(slug)
    if profile is None:
        return {"status": "rejected", "reason": f"no repo profile for {slug}"}

    task_id = f"{slug.split('/')[-1]}-{pr_number}"
    result: dict = {"task_id": task_id, "repo": slug, "pr": pr_number}

    pr = gh_json(f"repos/{slug}/pulls/{pr_number}")
    if not pr.get("merged_at"):
        return {**result, "status": "rejected", "reason": "PR is not merged"}

    merged_at = datetime.fromisoformat(pr["merged_at"].replace("Z", "+00:00"))
    result["merged_at"] = pr["merged_at"]
    if merged_at.date() <= C.CONTAMINATION_CUTOFF:
        return {
            **result,
            "status": "rejected",
            "reason": (
                f"merged {merged_at.date()} on or before contamination cutoff "
                f"{C.CONTAMINATION_CUTOFF}"
            ),
        }

    base_sha = pr["base"]["sha"]
    head_sha = pr["head"]["sha"]
    result.update(base_sha=base_sha, head_sha=head_sha, title=pr["title"])

    files = gh_json(f"repos/{slug}/pulls/{pr_number}/files?per_page=100")
    paths = [f["filename"] for f in files]
    test_paths = [p for p in paths if is_test_path(p, profile)]
    src_paths = [p for p in paths if is_source_path(p, profile)]
    runnable = [p for p in test_paths if is_runnable_test(p, profile)]
    result.update(
        test_paths=test_paths,
        source_paths=src_paths,
        runnable_test_paths=runnable,
    )

    if not test_paths:
        return {**result, "status": "rejected", "reason": "PR changes no test files"}
    if not runnable:
        return {
            **result,
            "status": "rejected",
            "reason": "PR changes only test fixtures, no runnable test file",
        }
    excluded = [
        p for p in runnable
        if any(fnmatch.fnmatch(Path(p).name, pat)
               for pat in profile.excluded_test_patterns)
    ]
    if excluded:
        return {
            **result,
            "status": "rejected",
            "reason": (
                f"test file excluded from the project's own test configuration: "
                f"{', '.join(excluded)}"
            ),
        }
    if not src_paths:
        return {**result, "status": "rejected", "reason": "PR changes no source files"}
    if len(src_paths) > 3:
        return {
            **result,
            "status": "rejected",
            "reason": f"{len(src_paths)} source files changed; limit is 3",
        }

    repo = ensure_clone(slug)
    fetch_commit(repo, base_sha)
    fetch_commit(repo, head_sha)

    tree = C.git("ls-tree", "-r", "--name-only", base_sha, cwd=repo)
    if any(Path(p).name == "hooks.json" for p in tree.splitlines()):
        return {
            **result,
            "status": "rejected",
            "reason": "repository ships hooks.json (arbitrary shell execution)",
        }

    test_patch = C.git("diff", base_sha, head_sha, "--", *test_paths, cwd=repo)
    ref_patch = C.git("diff", base_sha, head_sha, "--", *src_paths, cwd=repo)
    if not test_patch.strip():
        return {**result, "status": "rejected", "reason": "empty test patch"}

    issue = linked_issue(slug, pr)
    statement, source = problem_statement(pr, issue)
    flags = leak_scan(statement)
    result.update(statement_source=source, leak_flags=flags)
    if flags:
        return {
            **result,
            "status": "rejected",
            "reason": (
                f"problem statement describes the solution ({', '.join(flags)}); "
                f"source={source}"
            ),
        }

    task_dir = C.TASKS_DIR / "real" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "tests").mkdir(exist_ok=True)
    (task_dir / "tests" / "hidden.patch").write_text(test_patch)
    (task_dir / "tests" / "reference_solution.patch").write_text(ref_patch)
    (task_dir / "allowed_paths.txt").write_text(
        "\n".join(profile.allowed_globs) + "\n"
    )
    (task_dir / "prompt.md").write_text(render_prompt(statement, profile))

    if verify:
        result["verification"] = verify_task(
            slug, profile, repo, base_sha, task_dir, runnable
        )
        ok = result["verification"]["accepted"]
        result["status"] = "accepted" if ok else "rejected"
        if not ok:
            result["reason"] = result["verification"]["reason"]
    else:
        result["status"] = "built-unverified"

    (task_dir / "repo.lock").write_text(json.dumps(result, indent=2) + "\n")
    return result


def clean(text: str) -> str:
    text = re.sub(r"<!--.*?-->", "", text or "", flags=re.DOTALL)
    text = re.sub(r"^\s*- \[[ xX]\].*$", "", text, flags=re.MULTILINE)  # checklists
    return text.strip()


def problem_statement(pr: dict, issue: dict | None) -> tuple[str, str]:
    """Return (statement, source).

    Prefers the linked issue: it is written before the fix exists, so it describes
    the symptom rather than the change. Falls back to the pull request title alone,
    which is symptom-level, rather than the body, which is not.
    """
    if issue is not None:
        body = clean(issue.get("body", ""))
        if body:
            if len(body) > 4000:
                body = body[:4000].rsplit("\n", 1)[0] + "\n\n[truncated]"
            return f"## {issue['title']}\n\n{body}", f"issue#{issue['number']}"
    return f"## {pr['title']}", "pr-title-only"


def render_prompt(problem: str, profile: C.RepoProfile) -> str:
    """The problem statement the agent sees. Never mentions the hidden tests."""
    return f"""# Task

You are working in a checkout of `{profile.slug}`.

{problem}

## What to do

Fix the behaviour described above by editing the project's source code. Work
autonomously and finish the change; there is nobody available to answer
questions.

Do not modify, delete, or disable any existing test. Do not add new
dependencies. Do not change build, CI, or packaging configuration.

When you are done, leave the working tree containing your change.
"""


# --------------------------------------------------------------------- verify


def verify_task(
    slug: str,
    profile: C.RepoProfile,
    repo: Path,
    base_sha: str,
    task_dir: Path,
    test_paths: list[str],
) -> dict:
    """Three-stage verification. See module docstring."""
    wt = C.WORKTREES / f"verify-{task_dir.name}"
    if wt.exists():
        shutil.rmtree(wt, ignore_errors=True)
        C.git("worktree", "prune", cwd=repo, check=False)
    wt.parent.mkdir(parents=True, exist_ok=True)
    C.git("worktree", "add", "--quiet", "--detach", str(wt), base_sha, cwd=repo)

    out: dict = {"accepted": False, "reason": "", "stages": {}}
    tests_arg = " ".join(test_paths)

    # Stage 1 can only exercise tests that exist at base. A PR that adds a new
    # test file has nothing to run there, which is not a defect in the task.
    at_base = set(
        C.git("ls-tree", "-r", "--name-only", base_sha, cwd=repo).splitlines()
    )
    preexisting = [p for p in test_paths if p in at_base]
    out["preexisting_tests"] = preexisting
    try:
        for cmd in profile.setup:
            rc, log = run(cmd, wt)
            if rc != 0:
                out["reason"] = f"setup failed: {cmd}"
                out["stages"]["setup"] = {"rc": rc, "tail": log[-2000:]}
                return out
        out["stages"]["setup"] = {"rc": 0}

        cmd = profile.test_cmd.format(tests=tests_arg)

        if preexisting:
            base_cmd = profile.test_cmd.format(tests=" ".join(preexisting))
            rc_base, log_base = run(base_cmd, wt)
            out["stages"]["base_without_hidden"] = {
                "rc": rc_base, "tail": log_base[-1500:]
            }
            if rc_base != 0:
                out["reason"] = (
                    "pre-existing tests in the affected files already fail at base"
                )
                return out
        else:
            out["stages"]["base_without_hidden"] = {
                "skipped": "all affected test files are new in this PR"
            }

        rc, log = run(
            f"git apply {task_dir / 'tests' / 'hidden.patch'}", wt
        )
        if rc != 0:
            out["reason"] = "hidden test patch does not apply at base"
            out["stages"]["apply_hidden"] = {"rc": rc, "tail": log[-1500:]}
            return out

        rc_hidden, log_hidden = run(cmd, wt)
        out["stages"]["base_with_hidden"] = {"rc": rc_hidden, "tail": log_hidden[-1500:]}
        if rc_hidden == 0:
            out["reason"] = "hidden tests PASS at base — task is not a real failure"
            return out
        if rc_hidden == 124:
            out["reason"] = "hidden tests timed out at base"
            return out

        rc, log = run(
            f"git apply {task_dir / 'tests' / 'reference_solution.patch'}", wt
        )
        if rc != 0:
            out["reason"] = "reference solution patch does not apply"
            out["stages"]["apply_reference"] = {"rc": rc, "tail": log[-1500:]}
            return out

        rc_ref, log_ref = run(cmd, wt)
        out["stages"]["reference_solution"] = {"rc": rc_ref, "tail": log_ref[-1500:]}
        if rc_ref != 0:
            out["reason"] = "reference solution does not make the hidden tests pass"
            return out

        out["accepted"] = True
        out["test_command"] = cmd
        return out
    finally:
        shutil.rmtree(wt, ignore_errors=True)
        C.git("worktree", "prune", cwd=repo, check=False)


# ----------------------------------------------------------------------- cli


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="build one task or a whole spec file")
    b.add_argument("--repo")
    b.add_argument("--pr", type=int)
    b.add_argument("--spec", type=Path)
    b.add_argument("--no-verify", action="store_true")

    sub.add_parser("report", help="summarise built tasks")

    args = ap.parse_args()

    if args.cmd == "report":
        return report()

    problems = [p for p in C.preflight() if "pristine" not in p and "binary" not in p]
    if problems:
        for p in problems:
            print(f"  preflight: {p}", file=sys.stderr)
        return 1

    if args.spec:
        spec = json.loads(args.spec.read_text())
    elif args.repo and args.pr:
        spec = [{"repo": args.repo, "pr": args.pr}]
    else:
        print("need --spec, or --repo and --pr", file=sys.stderr)
        return 2

    accepted, rejected = [], []
    for entry in spec:
        print(f"[build] {entry['repo']}#{entry['pr']}", flush=True)
        try:
            res = build_task(entry["repo"], entry["pr"], verify=not args.no_verify)
        except Exception as exc:  # noqa: BLE001 - report, never abort the batch
            res = {
                "task_id": f"{entry['repo'].split('/')[-1]}-{entry['pr']}",
                "repo": entry["repo"],
                "pr": entry["pr"],
                "status": "rejected",
                "reason": f"{type(exc).__name__}: {exc}",
            }
        (accepted if res.get("status") == "accepted" else rejected).append(res)
        mark = "OK  " if res.get("status") == "accepted" else "DROP"
        print(f"  {mark} {res.get('reason', res.get('status', ''))}", flush=True)

    C.TASKS_DIR.mkdir(parents=True, exist_ok=True)
    (C.TASKS_DIR / "build_report.json").write_text(
        json.dumps({"accepted": accepted, "rejected": rejected}, indent=2) + "\n"
    )
    print(f"\naccepted {len(accepted)}   dropped {len(rejected)}")
    print(f"report written to {C.TASKS_DIR / 'build_report.json'}")
    return 0


def report() -> int:
    path = C.TASKS_DIR / "build_report.json"
    if not path.exists():
        print("no build report yet", file=sys.stderr)
        return 1
    data = json.loads(path.read_text())
    for res in data["accepted"]:
        print(f"  OK    {res['task_id']:<24} {res['title'][:60]}")
    for res in data["rejected"]:
        print(f"  DROP  {res['task_id']:<24} {res.get('reason', '')[:60]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
