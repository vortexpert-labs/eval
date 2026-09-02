# Gemini 3.7 Flash vs 3.8 Flash — inside Google Antigravity

**Status: Phase 1, design. No scored runs have been collected.**

## What this measures

This compares **Google Antigravity (`agy`) driving Gemini 3.7 Flash against Google Antigravity
driving Gemini 3.8 Flash**, both pinned to HIGH reasoning, on real coding tasks.

It does **not** measure the two models in isolation. The Antigravity agent scaffold — its system
prompt, its tool loop, its context handling, its permission model — sits inside every measurement
and cannot be factored out. A result here is a statement about the product, not about the weights.
That is stated here, in the title, and in the opening paragraph of any write-up, not in a footnote.

Both arms are run by us, on the same machine, from the same account, in an interleaved queue. No
vendor-published number is used as a baseline.

## Design

| | |
|---|---|
| Models | `gemini-3.7-flash-high`, `gemini-3.8-flash-high` |
| Harness | Antigravity CLI, pinned binary, headless print mode |
| Pilot | 6 real tasks × 3 repeats × 2 models = 36 runs |
| Full run | 15 real + 5 constraint tasks × 3 repeats × 2 models = 120 runs |
| Re-run | Identical configuration, 7–10 days after the first, to separate serving drift from model difference |
| Grading | Deterministic. Hidden tests, file-path checks, line counts. No LLM judge. |
| Ordering | Interleaved A/B/B/A with pair adjacency |

**Primary metric:** hidden-test pass rate.

**Secondary metrics**, which carry most of the interesting content and have per-run sample size
rather than per-task: files touched outside `allowed_paths.txt` (scope creep), tests modified or
deleted (reward hacking), lines changed in the final diff (slop proxy), tool calls and turns to
completion, wall-clock time, constraint violations, and non-completions by category.

**Statistical honesty.** At 6 tasks the pass-rate comparison is not a publishable finding and is
not presented as one — the interval spans nearly everything. Even at 20 tasks only a large gap is
detectable. This is stated up front rather than discovered in review. If the result is "no
detectable difference," that is the headline.

## Why the model comparison is contamination-controlled

Real tasks derive from pull requests merged **after 2026-08-13**, the launch date of Gemini 3.7
Flash, so neither model can have trained on the solutions. Launch date is used rather than training
cutoff because it is strictly more conservative: Gemini 3.8 Flash's published knowledge cutoff is
March 2026.

A residual asymmetry remains and is disclosed rather than papered over: the two models may have
different knowledge cutoffs, so the newer one may simply know more recent library APIs. That is
part of what "newer model" means and cannot be controlled for.

## Harness environment

The evaluation runs against a **stock Antigravity scaffold**, deliberately isolated from the
operator's working configuration by pointing `HOME` at a dedicated eval home. This removes, and was
measured to remove, roughly 5,900 tokens of prompt and ~200 MCP tools that a normal developer
install injects invisibly.

| | |
|---|---|
| Scaffold | Stock. No `AGENTS.md`, no skills, no plugins, no MCP servers. |
| Baseline prompt size | 13,826 input tokens, monitored per run for drift |
| Eval `HOME` | Outside this repository. Contains a plaintext OAuth token and is never committed. |
| Per-run isolation | Eval `HOME` restored from a pristine authenticated snapshot before every run |
| Binary | Frozen copy, checksummed, verified before and after each batch |
| Auto-update | Disabled via `AGY_CLI_DISABLE_AUTO_UPDATE`; enforcement is by checksum, not by trusting the flag |

Per-run isolation matters more than it sounds. The agent home accumulates conversation, brain and
implicit-memory state across runs, so without a reset run #100 executes against a measurably
different scaffold than run #1.

## Known harness behaviours that affect scoring

Established during environment recon and documented because they silently corrupt results:

- **`status: SUCCESS` does not mean the task was attempted.** A run whose every tool call was
  auto-denied returns `SUCCESS` with an empty response and a warning on **stderr only**. A run
  counts as a genuine attempt only if `status` is `SUCCESS`, `response` is non-empty, and stderr
  carries no auto-deny warning. Anything else is recorded as a harness fault and is void — never
  scored as a model failure.
- **Exit codes are not a failure signal.** Print mode deliberately does not treat benign tool
  errors as fatal.
- **`ask_question` is a built-in tool.** A run that asks a question and terminates is recorded as
  question-terminated, counted per model, and reported. It is not suppressed by instructing the
  agent, because instructing the agent would be tuning the scaffold.
- **Thinking level cannot be silently downgraded.** The CLI rejects a mismatch between the model
  slug and `--effort` as a hard error before making any call, so passing the slug alone is
  unambiguous. What remains undocumented by Google is whether HIGH denotes the same token budget on
  3.7 as on 3.8; this is disclosed as a limitation.

## Reproducing

Full instructions, pinned versions and the environment lock land here once Phase 2 is built. Raw
artifacts for every run — transcripts, diffs, test output, metrics — are published under `runs/`.
