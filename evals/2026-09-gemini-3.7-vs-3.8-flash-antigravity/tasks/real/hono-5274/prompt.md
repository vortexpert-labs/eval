# Task

You are working in a checkout of `honojs/hono`.

## fix(utils/stream): do not let abort listeners crash abort()

## Failing tests

These tests currently fail and must pass when you are done. Their contents are not shown to you, and they are not present in this working tree.

- `src/utils/stream.test.ts::abort() continues when an abort listener throws`
- `src/utils/stream.test.ts::abort() handles a rejecting thenable returned by a listener`
- `src/utils/stream.test.ts::abort() does not leave an unhandled rejection when an async listener rejects`

## What to do

Fix the behaviour described above by editing the project's source code. Work
autonomously and finish the change; there is nobody available to answer
questions.

Do not modify, delete, or disable any existing test. Do not add new
dependencies. Do not change build, CI, or packaging configuration.

When you are done, leave the working tree containing your change.
