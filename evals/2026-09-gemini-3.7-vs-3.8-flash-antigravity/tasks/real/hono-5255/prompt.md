# Task

You are working in a checkout of `honojs/hono`.

## fix(accepts): support wildcard media types and specificity ordering in defaultMatch

## Failing tests

These tests currently fail and must pass when you are done. Their contents are not shown to you, and they are not present in this working tree.

- `src/helper/accepts/accepts.test.ts::should match wildcard subtype application/*`
- `src/helper/accepts/accepts.test.ts::should return default support for global wildcard */*`
- `src/helper/accepts/accepts.test.ts::should return default support for single asterisk wildcard *`
- `src/helper/accepts/accepts.test.ts::should prefer exact match over wildcard match at same quality factor`
- `src/helper/accepts/accepts.test.ts::should match subtype wildcard while global wildcard falls through to default`

## What to do

Fix the behaviour described above by editing the project's source code. Work
autonomously and finish the change; there is nobody available to answer
questions.

Do not modify, delete, or disable any existing test. Do not add new
dependencies. Do not change build, CI, or packaging configuration.

When you are done, leave the working tree containing your change.
