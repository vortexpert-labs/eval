# Pre-registration

**Committed before any scored run.** Amendments after data collection begins are prohibited;
anything added later is labelled exploratory and reported separately.

| | |
|---|---|
| Registered | 2026-09-02 |
| Experiment | Gemini 3.7 Flash vs Gemini 3.8 Flash, inside Google Antigravity |
| Status at registration | No scored runs collected. No calibration runs collected. |

---

## 1. What is being measured

**Google Antigravity (`agy`) driving `gemini-3.7-flash-high` versus Google Antigravity driving
`gemini-3.8-flash-high`**, on real coding tasks, under an identical harness.

This is not a measurement of the two models in isolation. The agent scaffold — system prompt, tool
loop, context handling, permission model — is inside every measurement and cannot be factored out.
All claims are about the product, not the weights.

## 2. Hypothesis

**No directional hypothesis is registered.** Gemini 3.8 Flash is the newer model and Google claims
improvements in coding and agentic workflows, but this study is not designed to confirm that. A
null result is a complete and publishable outcome.

## 3. Fixed conditions

| | |
|---|---|
| Harness | Antigravity CLI, frozen binary, sha256 `294dbe8814c1d8846326b0d2c45212b0e1e06b3e72662856968e6e0a65d04d34`, reports `1.1.24` |
| Auto-update | Disabled via `AGY_CLI_DISABLE_AUTO_UPDATE=true`; **enforced by checksum before and after every batch**, not by trusting the flag |
| Scaffold | Stock. Dedicated eval `HOME`, no `AGENTS.md`, no skills, no plugins, no MCP servers |
| Baseline prompt size | 13,826 input tokens; monitored per run for drift |
| Account | One account for all runs. Account is a constant, not a variable. Any switch is logged as an event and reported |
| Toolchain | Python 3.12.14, Node 24.19.0, git 2.55.0, uv 0.12.8, bun 1.4.0 — pinned by explicit `PATH`, recorded in `env.lock` |
| Per-run isolation | Fresh git worktree; eval `HOME` restored from a pristine authenticated snapshot before every run |
| Per-run timeout | 15 minutes, via `--print-timeout` plus an external watchdog |
| Turn cap | **None.** No CLI flag exists. `num_turns` is measured, not capped |

**Canonical invocation, identical for every run except the model slug:**

```
<frozen-agy> -p "<prompt>" --model <slug> --output-format stream-json \
  --dangerously-skip-permissions --print-timeout 15m --new-project \
  --mode accept-edits --disable-slash-commands
```

`--effort` is never passed. The reasoning level is carried by the model slug alone; the CLI rejects
a slug/effort mismatch as a hard error, so the level cannot be silently downgraded.

`--dangerously-skip-permissions` is mandatory, not a convenience: under a stock scaffold the
permission allowlist is empty, and without this flag every tool call is auto-denied while the run
still reports `SUCCESS`.

## 4. Task set

**Sources:** `pallets/click`, `Delgan/loguru` (Python), `honojs/hono` (TypeScript). All three ship
no agent configuration of their own, so every task runs under an identical scaffold.

**Contamination control.** Every task derives from a pull request merged **after 2026-08-13**, the
launch date of Gemini 3.7 Flash. Launch date is used rather than training cutoff because it is
strictly more conservative.

**Construction.** For each task: check out the PR's base commit, apply **only** the PR's test
changes, leave source unchanged, and verify the suite fails cleanly. The agent's job is to make the
tests pass. The tests are hidden — the agent is never shown them.

**Exclusion rules, fixed in advance:**
- Any task whose tests do not fail cleanly at base is dropped. No workarounds.
- Any repository containing `hooks.json` is refused outright (arbitrary shell execution under
  `--dangerously-skip-permissions`).
- `refactor:` and `perf:` PRs are excluded — their tests frequently pass at base.
- Platform-specific fixes that cannot fail on macOS are excluded.

**Selection rule — registered before any calibration run.** Approximately 12 candidate tasks are
built. Each is run **once** with `gemini-3.6-flash-high`, a model that appears nowhere in the
comparison. Tasks the calibrator passes trivially (≤2 turns, clean first attempt) or fails outright
are dropped. The surviving tasks in the discriminating band are kept, up to 6 for the pilot.

Calibration is never run with `gemini-3.7-flash-high` or `gemini-3.8-flash-high`. Selecting tasks on
the models under test would manufacture the result. The calibration run and its outputs are
published.

**The final task list is appended to this document as a separate, git-timestamped commit before the
first scored run.** The rule above is registered now; the outcome of applying it is recorded then.

## 5. Run protocol

- **Pilot:** 6 tasks × 3 repeats × 2 models = 36 runs.
- **Full run:** 15 real + 5 constraint tasks × 3 repeats × 2 models = 120 runs.
- **Re-run:** identical configuration, 7–10 days after the first, to separate launch-window serving
  instability from model difference. Gemini 3.8 Flash launched 2026-09-02; the pilot is a launch-day
  measurement and is reported as provisional until the re-run.
- **Ordering:** interleaved A/B/B/A. The two runs of a task/repeat pair are **adjacent** in the
  queue. Adjacency, not total elapsed time, is what decorrelates model from serving conditions; the
  queue may span multiple days and this does not weaken the design.
- No configuration changes mid-run. No re-running a completed run to obtain a better result.

## 6. Metrics

**Primary:** hidden-test pass rate.

**Secondary**, all measured per run:

| Metric | Definition |
|---|---|
| Scope creep | Count of files touched outside `allowed_paths.txt` |
| Test tampering | Count of test files modified or deleted |
| Diff size | Lines changed in the final diff |
| Turns | `num_turns` from the result envelope |
| Tool calls | Count of tool-call events in `transcript.jsonl` |
| Wall-clock | `duration_seconds`; **taken only from unrecorded runs** |
| Constraint violations | Constraint tasks only |
| Token usage | `input_tokens`, `output_tokens`, `thinking_tokens` |

## 7. Run status taxonomy

Registered in advance because misclassification here silently corrupts the primary metric.

| Status | Definition | Counts as |
|---|---|---|
| `pass` | Attempt completed, hidden tests pass | Success |
| `fail` | Attempt completed, hidden tests fail | Model failure |
| `question_terminated` | Transcript contains an `ask_question` call and the run ends without an attempt | Model failure, **reported separately** |
| `timeout` | Exceeded 15 minutes | Model failure, **reported separately** |
| `harness_fault` | Not a genuine attempt | **Void.** Never a model failure |

A run is a genuine attempt only if `status` is `SUCCESS` **and** `response` is non-empty **and**
stderr carries no auto-deny warning. Exit code is never used as a failure signal — print mode
deliberately does not treat benign tool errors as fatal.

`harness_fault` covers: quota exhaustion, network failure, CLI crash, auto-denied tool calls, and
binary-checksum mismatch. These **may** be re-run. `fail`, `timeout` and `question_terminated`
**may never** be re-run.

## 8. Analysis plan

**Primary.** Each task is scored by majority of its 3 repeats (passes if ≥2 of 3 pass). Comparison
is paired at the task level. The registered estimate is the **difference in task-level pass rate**,
with a **95% confidence interval from a paired bootstrap over tasks** (10,000 resamples, tasks
resampled with replacement). Raw run-level counts are reported alongside.

**If the interval includes zero, the registered conclusion is "no detectable difference."** No
claim of a difference is made on overlapping intervals, and no alternative test is substituted to
obtain one.

**Repeat instability** is reported as the proportion of tasks whose 3 repeats do not agree. This is
a result in its own right: a model that is unstable across identical repeats is meaningfully worse
even at equal pass rate.

**Secondary metrics** are compared per run, paired by `(task, repeat)`, reported as medians with
95% bootstrap CIs of the paired difference. These are **not corrected for multiplicity** across the
eight metrics and are therefore reported as descriptive and hypothesis-generating. No significance
claim is made on a secondary metric.

**Power.** At 6 tasks the primary comparison has essentially no power and is not presented as a
finding. At 20 tasks only a large difference is detectable. No power figure computed from assumed
variance is published; the achieved interval width is reported instead.

## 9. Stopping rules

- The run queue completes in full, or the experiment is reported as incomplete with the number of
  collected runs stated.
- **Quota exhaustion:** partial data is discarded at the **pair** level, never the run level.
  Dropping a lone run whose paired partner completed biases the comparison.
- **Binary checksum mismatch** at any batch boundary voids the affected batch and it is re-run.
- The experiment is not stopped early because a result looks interesting, and is not extended
  because it does not.

## 10. Deviations

Any departure from this document is recorded in a `DEVIATIONS.md` alongside it, with the date, the
change, and the reason. Analyses not registered here are labelled exploratory wherever they appear.

## 11. Publication commitments

- Full transcripts, final diffs, test output and metrics for **every** run, including failures,
  harness faults and dropped tasks.
- The calibration run and the candidate pool it was drawn from.
- The environment lock, the frozen binary checksum, and the exact invocation.
- A limitations section covering, at minimum: the agent scaffold being inside the loop; n; the
  subscription-mediated access path; launch-day serving instability; the undocumented question of
  whether HIGH denotes the same token budget on both models; differing model knowledge cutoffs; the
  absence of hermetic task environments; and every dropped task with its reason.
