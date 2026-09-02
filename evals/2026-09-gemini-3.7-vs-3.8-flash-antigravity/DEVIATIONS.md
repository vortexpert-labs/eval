# Deviations from the pre-registration

Every departure from `PREREGISTRATION.md` is recorded here with its date, the
change, and the reason. Nothing is amended silently.

---

## 2026-09-03 — Problem statements now come from the linked issue, not the PR body

**Registered behaviour.** Section 4 fixed the task construction rules but did not
specify where the agent's problem statement comes from. The first implementation
used the pull request title and body.

**What went wrong.** A pull request body is written *after* the fix, by the person
who wrote it, and routinely explains the solution. Inspection of the eleven built
candidates found two whose statement handed over the answer:

- `loguru-1484` carried a section headed "The fix" naming the exact method, the
  exact cause, and the test that was added.
- `hono-5274` described the fix ("sync throws are caught, and a returned promise
  gets a no-op catch"), both hidden tests, and their expected pass/fail counts.

A third, `loguru-1510`, was clean: a reproduction script and a traceback, with no
description of the change. The contrast is the point — leakage was **uneven across
tasks**, silently making some trivial while others stayed hard. That is worse than
uniform leakage, because it distorts the task set rather than the overall level.

**Change.** The problem statement is now taken from the **linked issue** where the
pull request closes one, since an issue is written before the fix exists and
describes the symptom. Where no linked issue exists, the statement falls back to
the **pull request title alone**, which is symptom-level, rather than the body.

A leak scan then rejects any statement containing a fix section, a solution or
cause section, the phrase "the fix", author narrative ("I changed…"), a `diff`
block, or a description of added tests. `statement_source` and any `leak_flags`
are recorded in every task's `repo.lock` and published.

**Why this is legitimate after registration.** The change is a fix to a defect in
task *construction*. It uses no run data, no model output, and no outcome
information of any kind — no scored or calibration run had been collected when it
was made. It cannot be used to select tasks that favour either arm.

**Known consequence, disclosed.** Tasks falling back to a title-only statement are
substantially harder, because the agent must localise the bug from a one-line
description. This shifts the difficulty distribution of the whole task set. It is
applied identically to both arms and is measured by the calibration pass, but it
means difficulty is not comparable to the first build.

---

## 2026-09-03 — Prompts now list the names of the failing tests

**Registered behaviour.** Section 4 states that the hidden tests are never shown
to the agent. It did not say whether the agent is told *which* tests fail.

**Why this changed.** After moving statements to linked issues (deviation above),
five of the nine surviving tasks had only a one-line pull request title as their
problem statement — for example, the entire statement for `hono-5283` was
`fix(client): omit empty query delimiter`. From six words the agent had to infer
the meaning, localise it in a large codebase, and match a maintainer's intent
precisely enough to satisfy a test it cannot see. A correct but different fix
would score as a failure. That measures intent-guessing rather than coding
ability, and it adds variance where a nine-task set can least afford it.

**Change.** Each prompt now lists the **names** of the tests that fail, qualified
by file, in the form a continuous integration run reports them. Test **contents
remain hidden** and the test files remain absent from the working tree.

**Why this does not leak.** A test name states the requirement — what the code
should do. It does not state the implementation. This is strictly less information
than a maintainer has when fixing a real bug, since they can also read the test.

**Applied identically to both arms**, and to issue-specified and title-only tasks
alike, so it shifts difficulty uniformly rather than differentially. Difficulty is
not comparable to either earlier build; the calibration pass measures the set as
it now stands.
