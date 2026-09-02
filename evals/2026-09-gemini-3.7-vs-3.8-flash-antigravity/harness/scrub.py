#!/usr/bin/env python3
"""Redact run artifacts before publication, and refuse when unsure.

This repository is public and publishes raw transcripts. Those transcripts are
produced by an agent running with `--dangerously-skip-permissions` on a personal
machine, so they contain absolute paths, a username, a hostname, and whatever the
agent happened to read.

Two different jobs, deliberately kept apart:

  REDACT   deterministic, reversible-in-meaning substitutions for things that are
           merely identifying — home directory, username, hostname.

  REFUSE   anything matching a credential pattern is NOT quietly redacted. The
           scrub fails, names the file and line, and publishes nothing. A silent
           redaction would hide the fact that a secret reached an artifact at all,
           which is the thing you most need to know.

    python3 harness/scrub.py --batch runs/pilot --out runs_public/pilot
    python3 harness/scrub.py --check-staged        # pre-commit gate
"""

from __future__ import annotations

import argparse
import getpass
import json
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

HOME = str(Path.home())
USER = getpass.getuser()
HOSTNAME = socket.gethostname()

#: Identifying but not secret. Substituted so artifacts stay readable.
REDACTIONS: list[tuple[str, str]] = [
    (HOME, "<HOME>"),
    (USER, "<user>"),
    (HOSTNAME, "<host>"),
]

#: Credential shapes. A hit fails the scrub; nothing is published.
SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bntn_[A-Za-z0-9]{20,}"), "Notion token"),
    (re.compile(r"\bHRKU-[A-Za-z0-9_\-]{20,}"), "Heroku API key"),
    (re.compile(r"\bfigd_[A-Za-z0-9_\-]{20,}"), "Figma API key"),
    (re.compile(r"\bghp_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{30,}"), "GitHub token"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"), "OpenAI-style key"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key id"),
    (re.compile(r"\bya29\.[A-Za-z0-9_\-]{20,}"), "Google OAuth access token"),
    (re.compile(r"\b1//[A-Za-z0-9_\-]{30,}"), "Google OAuth refresh token"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private key block"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"), "JWT"),
    (re.compile(r'"refresh_token"\s*:'), "OAuth token structure"),
    (re.compile(r"[A-Za-z0-9._%+\-]+@(?!example\.)[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
     "email address"),
    (re.compile(r"\b(?:postgres|postgresql|mongodb|redis|mysql)://[^\s\"']*:[^\s\"'@]+@"),
     "connection string with password"),
]

#: Not secrets themselves — filenames that *hold* secrets. Naming them in source
#: or in .gitignore is correct and expected. Seeing them inside a run artifact is
#: different: it means the agent touched the credential store, which is worth a
#: human look even though no secret value is present.
ARTIFACT_SUSPICION: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bantigravity-oauth-token\b"), "agent referenced the agy credential file"),
    (re.compile(r"\bmcp_config\.json\b"), "agent referenced the MCP config (holds live keys)"),
    (re.compile(r"/\.gemini/config/"), "agent reached into the real agy config directory"),
    (re.compile(r"\bDownloads/secrets\b"), "agent referenced the secrets directory"),
]

TEXT_SUFFIXES = {".json", ".jsonl", ".patch", ".txt", ".log", ".csv", ".md", ".lock"}


def redact(text: str) -> str:
    for needle, replacement in REDACTIONS:
        if needle:
            text = text.replace(needle, replacement)
    return text


def find_secrets(text: str, label: str, artifact: bool = False) -> list[str]:
    """Credential shapes. With `artifact=True`, also flag references to the files
    that hold credentials — meaningful inside a transcript, noise in source."""
    patterns = SECRET_PATTERNS + (ARTIFACT_SUSPICION if artifact else [])
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for pattern, name in patterns:
            if pattern.search(line):
                hits.append(f"{label}:{lineno}: {name}")
    return hits


def scrub_batch(batch: Path, out: Path) -> int:
    if not batch.exists():
        print(f"no such batch: {batch}", file=sys.stderr)
        return 1

    files = [p for p in batch.rglob("*") if p.is_file()]
    problems: list[str] = []
    staged: list[tuple[Path, str]] = []

    for src in files:
        rel = src.relative_to(batch)
        if src.suffix not in TEXT_SUFFIXES:
            problems.append(f"{rel}: unexpected binary or unknown file type")
            continue
        text = redact(src.read_text(errors="replace"))
        hits = find_secrets(text, str(rel), artifact=True)
        if hits:
            problems.extend(hits)
            continue
        staged.append((out / rel, text))

    if problems:
        print("SCRUB FAILED — nothing was written.\n", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        print(
            "\nA credential pattern reached a run artifact. Do not publish this "
            "batch.\nInvestigate how it got there, then rotate the credential.",
            file=sys.stderr,
        )
        return 1

    if out.exists():
        shutil.rmtree(out)
    for dest, text in staged:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)

    print(f"scrubbed {len(staged)} files -> {out}")
    print(f"redacted: home directory, username {USER!r}, hostname {HOSTNAME!r}")
    return 0


def check_staged() -> int:
    """Pre-commit gate: refuse any staged content matching a credential pattern."""
    names = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
        capture_output=True, text=True,
    ).stdout.split()
    problems: list[str] = []
    for name in names:
        blob = subprocess.run(
            ["git", "show", f":{name}"], capture_output=True, text=True
        )
        if blob.returncode != 0:
            continue
        problems.extend(find_secrets(blob.stdout, name))
        if HOME in blob.stdout or (USER and USER in blob.stdout):
            problems.append(f"{name}: unredacted home directory or username")
    if problems:
        print("COMMIT BLOCKED — sensitive content in staged files:\n", file=sys.stderr)
        for p in sorted(set(problems)):
            print(f"  {p}", file=sys.stderr)
        return 1
    print(f"pre-commit scan clean ({len(names)} staged files)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--check-staged", action="store_true")
    args = ap.parse_args()
    if args.check_staged:
        return check_staged()
    if not (args.batch and args.out):
        print("need --batch and --out, or --check-staged", file=sys.stderr)
        return 2
    return scrub_batch(args.batch, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
