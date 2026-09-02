# Candidate task sources

Screened 2026-09-02. Published as part of the audit trail: this is the pool the task set was drawn
from, recorded **before** any task was built or any scored run collected, so the selection cannot
be reverse-engineered from results.

## Screening criteria

1. Actively maintained, with pull requests merged **after 2026-08-13** (Gemini 3.7 Flash's launch
   date — the contamination cutoff).
2. A real test suite that runs quickly and without heavy native toolchains (no Docker available).
3. Candidate PRs that touch **1–3 source files and at least one test file**, with ≤150 added lines.
   Small, surgical, behaviour-changing diffs make the "revert source, keep tests, tests must fail"
   construction reliable.
4. Screened for agent configuration the repository ships itself — `AGENTS.md`, `CLAUDE.md`,
   `.agents/`, `.cursorrules`, `GEMINI.md`, `hooks.json`. These load into an agent run and are part
   of the task environment, so they must be recorded, not discovered later.

## Pool

| Repo | Lang | Merged PRs since cutoff | Qualifying PRs | Ships agent config |
|---|---|---|---|---|
| **`pallets/click`** | Python | 23 | **7** | none |
| **`Delgan/loguru`** | Python | 13 | **6** | none |
| **`honojs/hono`** | TypeScript | 30 | **26** | none |
| `pallets/werkzeug` | Python | 10 | 5 | none |
| `psf/black` | Python | 11 | not enumerated | none |
| `tox-dev/tox` | Python | 28 | not enumerated | none |
| `pydantic/pydantic` | Python | 46 | 14 | `.agents/skills/pydantic/SKILL.md` (10,134 B) |
| `colinhacks/zod` | TypeScript | 100+ | 15 | `AGENTS.md`, `CLAUDE.md`, `.cursorrules` |
| `agronholm/anyio` | Python | 18 | not enumerated | `AGENTS.md`, `CLAUDE.md` |
| `python-jsonschema/jsonschema` | Python | 16 | **0** | none |
| `pytest-dev/pytest` | Python | 37 | 0 (non-standard test layout) | none |
| `encode/httpx`, `Textualize/rich`, `pallets/jinja`, `arrow-py/arrow`, `date-fns/date-fns` | — | 0 | — | — |
| `immerjs/immer` | TypeScript | 16 | 0 | none |

**Selected for the pilot: `pallets/click`, `Delgan/loguru`, `honojs/hono`** — all three ship no
agent configuration, so every pilot task runs under an identical stock scaffold.

`python-jsonschema/jsonschema` was initially selected and then **rejected on inspection**: all 16
of its post-cutoff merges are Dependabot bumps or pre-commit autoupdates, with no behaviour-changing
fix among them. Recorded here rather than quietly swapped out.

**No `hooks.json` was found in any candidate.** That matters: a repository-supplied `hooks.json`
executes arbitrary shell commands on tool-use lifecycle events, and every eval run uses
`--dangerously-skip-permissions`. The task builder refuses any repository containing one.

## Runtime requirements

| Repo | Requirement | Available locally |
|---|---|---|
| `pallets/click` | `requires-python >=3.10` | yes — pinned Python 3.12.14 |
| `pydantic/pydantic` | `requires-python >=3.10`, Rust-backed `pydantic-core` | yes, via wheels |
| `Delgan/loguru` | pure Python | yes |
| `honojs/hono` | `packageManager: bun@1.2.20`; test is `tsc && vitest --run` | **bun not installed** |
| `colinhacks/zod` | `packageManager: pnpm@10.12.1`; test is `vitest run` | yes — pnpm present |

## Notes on selection

**Scaffold uniformity is a real consideration.** A repository that ships its own `AGENTS.md` or
skill injects that content into every run against it. This does not bias 3.7 against 3.8 — both
arms see it identically — but it makes the scaffold non-uniform *across tasks*, so a pydantic task
and a click task are not run under equivalent conditions. For a 6-task pilot, drawing only from
repositories with no agent configuration keeps the scaffold constant and one fewer thing to explain.

**`refactor:` and `perf:` PRs are poor task candidates** and are excluded at build time. Their tests
frequently pass at the base commit because no behaviour changed, which breaks the required
"tests fail cleanly before the fix" precondition. Preference order is `fix(...)`, then
behaviour-changing `feat`.

Every candidate is verified at build time by checking out the base commit, applying only the PR's
test changes, and confirming the suite fails cleanly. Any task that does not is dropped, and the
drop is recorded here rather than silently removed.
