"""Shared configuration for the Antigravity model-comparison harness.

Every path, version and command that defines the experiment lives here so that
`env.lock` can be generated from a single source and the whole configuration can
be published verbatim.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

# --------------------------------------------------------------------------
# Locations. Everything the agent can reach lives OUTSIDE this repository.
# The eval HOME holds a plaintext OAuth token and must never be committed.
# --------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
TASKS_DIR = REPO_ROOT / "tasks"
RUNS_DIR = REPO_ROOT / "runs"
RECORDING_DIR = REPO_ROOT / "recording"

TEMP_ROOT = Path.home() / "Developer" / "temp"
AGY_BIN = TEMP_ROOT / "agyeval-bin" / "agy-1.1.24"
EVAL_HOME = TEMP_ROOT / "agyeval-home"
PRISTINE_HOME = TEMP_ROOT / "agyeval-home.pristine"
WORK_ROOT = TEMP_ROOT / "agyeval-work"
REPO_CACHE = WORK_ROOT / "repos"
WORKTREES = WORK_ROOT / "wt"

# --------------------------------------------------------------------------
# Pinned identities. Verified before and after every batch.
# --------------------------------------------------------------------------

AGY_SHA256 = "294dbe8814c1d8846326b0d2c45212b0e1e06b3e72662856968e6e0a65d04d34"
AGY_VERSION = "1.1.24"

MISE = Path.home() / ".local" / "share" / "mise" / "installs"
PINNED_PATH = os.pathsep.join(
    [
        str(MISE / "python" / "3.12" / "bin"),
        str(MISE / "node" / "lts" / "bin"),
        str(Path.home() / ".local" / "bin"),
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ]
)

# --------------------------------------------------------------------------
# Experiment constants.
# --------------------------------------------------------------------------

MODELS = {"A": "gemini-3.7-flash-high", "B": "gemini-3.8-flash-high"}
CALIBRATOR = "gemini-3.6-flash-high"

#: Gemini 3.7 Flash launch date. Tasks must derive from PRs merged strictly after
#: this, so neither model under test can have trained on the solution.
CONTAMINATION_CUTOFF = date(2026, 8, 13)

RUN_TIMEOUT_SECONDS = 15 * 60
#: Outer watchdog margin. macOS ships no `timeout(1)`, so the runner enforces this
#: itself via subprocess; the margin catches a process that ignores --print-timeout.
WATCHDOG_MARGIN_SECONDS = 120

#: Expected stock-scaffold prompt size. Drift upward means scaffold state leaked in.
BASELINE_INPUT_TOKENS = 13826
BASELINE_TOLERANCE = 400


# --------------------------------------------------------------------------
# Per-repository build and test profiles.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RepoProfile:
    slug: str
    language: str
    #: Commands run once per worktree to make the test suite runnable.
    setup: list[str]
    #: Formatted with {tests} -> space-joined test paths.
    test_cmd: str
    #: Directory globs the agent may legitimately edit. Used for scope-creep scoring.
    allowed_globs: list[str]
    #: Paths treated as test files for the tampering metric.
    test_globs: list[str] = field(default_factory=list)
    #: Test files the project's own runner deliberately excludes. A task whose
    #: tests match one of these cannot be graded and is rejected at build time.
    excluded_test_patterns: list[str] = field(default_factory=list)
    #: Optional extra signal recorded but not used for pass/fail.
    typecheck_cmd: str | None = None


REPO_PROFILES: dict[str, RepoProfile] = {
    "pallets/click": RepoProfile(
        slug="pallets/click",
        language="python",
        setup=[
            "uv venv --python 3.12 .eval-venv",
            "uv pip install --python .eval-venv/bin/python -e .",
            "uv pip install --python .eval-venv/bin/python pytest",
        ],
        test_cmd=".eval-venv/bin/python -m pytest -p no:randomly -q {tests}",
        allowed_globs=["src/click/**"],
        test_globs=["tests/**"],
    ),
    "Delgan/loguru": RepoProfile(
        slug="Delgan/loguru",
        language="python",
        setup=[
            "uv venv --python 3.12 .eval-venv",
            "uv pip install --python .eval-venv/bin/python -e .",
            # loguru's [dev] extra drags in tox and mypy; install only what the
            # test suite actually imports, at the versions its pyproject pins.
            "uv pip install --python .eval-venv/bin/python "
            "'pytest==9.1.1' 'freezegun==1.5.0' 'colorama==0.4.6'",
        ],
        test_cmd=".eval-venv/bin/python -m pytest -q {tests}",
        allowed_globs=["loguru/**"],
        test_globs=["tests/**"],
    ),
    "honojs/hono": RepoProfile(
        slug="honojs/hono",
        language="typescript",
        setup=["bun install --frozen-lockfile"],
        test_cmd="bun run vitest --run {tests}",
        allowed_globs=["src/**"],
        test_globs=["**/*.test.ts", "**/*.test.tsx", "**/*.spec.ts", "runtime-tests/**"],
        # hono's `main` vitest project excludes '**/*.case.test.*' and no other
        # project includes it, so these files are never executed by the suite.
        excluded_test_patterns=["*.case.test.*"],
        typecheck_cmd="bun run tsc -p tsconfig.spec.json --noEmit",
    ),
}


# --------------------------------------------------------------------------
# Environment construction.
# --------------------------------------------------------------------------


def agent_env(home: Path | None = None) -> dict[str, str]:
    """Environment for an `agy` invocation.

    Deliberately minimal: a stock scaffold means the agent must not inherit the
    operator's shell, tool configuration or credentials. Only what a build needs.
    """
    return {
        "HOME": str(home or EVAL_HOME),
        "PATH": PINNED_PATH,
        "AGY_CLI_DISABLE_AUTO_UPDATE": "true",
        "AGY_CLI_HIDE_ACCOUNT_INFO": "1",
        "LANG": "en_US.UTF-8",
        "LC_ALL": "en_US.UTF-8",
        "TERM": "dumb",
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
    }


def build_env(cwd: Path) -> dict[str, str]:
    """Environment for task setup and test execution (not for `agy` itself)."""
    env = agent_env()
    env["HOME"] = str(WORK_ROOT / "buildhome")
    env["CI"] = "1"
    env["PWD"] = str(cwd)
    return env


# --------------------------------------------------------------------------
# Integrity checks.
# --------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_binary() -> None:
    """Raise unless the frozen agy binary is byte-identical to the pinned one.

    Rule #7: if the CLI changes mid-experiment the affected batch is void. The
    auto-update flag is not trusted; this checksum is the actual enforcement.
    """
    if not AGY_BIN.exists():
        raise SystemExit(f"frozen agy binary missing: {AGY_BIN}")
    actual = sha256_file(AGY_BIN)
    if actual != AGY_SHA256:
        raise SystemExit(
            f"agy binary checksum mismatch — batch is VOID\n"
            f"  expected {AGY_SHA256}\n  actual   {actual}"
        )


def preflight() -> list[str]:
    """Check every precondition. Returns a list of problems; empty means ready."""
    problems: list[str] = []
    if not AGY_BIN.exists():
        problems.append(f"missing frozen binary: {AGY_BIN}")
    else:
        try:
            verify_binary()
        except SystemExit as exc:
            problems.append(str(exc))
    if not PRISTINE_HOME.exists():
        problems.append(
            f"missing pristine eval HOME snapshot: {PRISTINE_HOME} "
            "(create it from an authenticated eval home before the first run)"
        )
    token = PRISTINE_HOME / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
    if PRISTINE_HOME.exists() and not token.exists():
        problems.append(f"pristine snapshot is not authenticated: {token} absent")
    for tool in ("git", "uv", "bun"):
        found = shutil.which(tool, path=PINNED_PATH)
        if not found:
            problems.append(f"tool not on pinned PATH: {tool}")
    py = MISE / "python" / "3.12" / "bin" / "python3"
    if not py.exists():
        problems.append(f"pinned python missing: {py}")
    if REPO_ROOT.is_relative_to(EVAL_HOME) or REPO_ROOT.is_relative_to(WORK_ROOT):
        problems.append("repository sits inside agent-reachable workspace")
    return problems


def git(*args: str, cwd: Path | None = None, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed:\n{proc.stderr.strip()}")
    return proc.stdout
