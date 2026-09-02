# VorteXpert Labs — Evals

Reproducible evaluations of AI coding tools, models and agent harnesses.

Every evaluation in this repository is self-contained under `evals/<date>-<slug>/` and ships its
own pre-registration, task definitions, raw run artifacts, deterministic scorers and analysis. Raw
transcripts and diffs are published for every run so anyone can re-score the outputs with their own
grader rather than taking our numbers on trust.

## Principles

These apply to every eval here, not just the first one.

1. **The harness is inside the measurement.** An agent scaffold, its system prompt, its context
   handling and its tool loop all affect results. We name the harness in the title and describe it
   in full. We never claim to be measuring a model in isolation when we are measuring a product.
2. **Every arm is run by us, in the same harness, in the same window.** Vendor-published numbers
   are never used as a baseline. Where they appear at all, they sit in a separate table labelled
   as differently-harnessed and not comparable.
3. **Pre-registration before the first scored run.** Tasks, metrics and the analysis plan are
   committed to git before any data is collected. Anything added afterwards is labelled exploratory.
4. **Deterministic grading only.** Hidden tests pass or fail; files are touched or not; lines are
   counted. No LLM-as-judge.
5. **Null results are published.** "No detectable difference" is a finding and ships as one, with
   the raw artifacts attached.
6. **Raw artifacts are public.** Full transcripts and final diffs for every run, including failures.

## Evaluations

| Date | Evaluation | Status |
|---|---|---|
| 2026-09 | [Gemini 3.7 Flash vs 3.8 Flash in Google Antigravity](evals/2026-09-gemini-3.7-vs-3.8-flash-antigravity/) | Phase 1 — design |

## Layout

```
evals/<date>-<slug>/
  README.md            what was measured, how to reproduce it
  PREREGISTRATION.md   committed before the first scored run
  tasks/               task definitions; hidden tests the agent never sees
  harness/             task builder, runner, run-queue generator
  scoring/             deterministic scorers, operating only on stored artifacts
  analysis/            paired tests, confidence intervals, tables
  runs/                raw artifacts, one directory per run
  recording/           run log with ISO timestamps for footage alignment
```

## Reproducing

Each eval's README carries its own instructions, pinned tool versions and environment lock. Start
there rather than here.
