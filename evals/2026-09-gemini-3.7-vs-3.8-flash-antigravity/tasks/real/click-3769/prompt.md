# Task

You are working in a checkout of `pallets/click`.

## `click.progressbar` doesn't show full completion when using `show_pos=True` combined with `update_min_steps`

Using a `click.progressbar` with:
- `update_min_steps` which isn't a divisor of `length`
- `show_pos=True`

won't show full completion at the end. This can be reproduced as follows:

```python
import time

import click

with click.progressbar(
    range(20),
    show_pos=True,
    update_min_steps=7,
) as bar:
    for i in bar:
        time.sleep(0.1)
```

with (final) output in the terminal

```text
  [####################################]  14/20          
```

**Expected behaviour**: I had expected the output to show `20/20` at the end.
This would be consistent with the default percentage formatting, as can be seen by commenting the line `show_pos=True` and re-running the reproduction:

```text
  [####################################]  100%          
```

Environment:

- Python version: tested with 3.12.3 and 3.14.3
- Click version: 8.4.1

## Failing tests

These tests currently fail and must pass when you are done. Their contents are not shown to you, and they are not present in this working tree.

- `tests/test_termui.py::test_progressbar_lands_on_final_position`

## What to do

Fix the behaviour described above by editing the project's source code. Work
autonomously and finish the change; there is nobody available to answer
questions.

Do not modify, delete, or disable any existing test. Do not add new
dependencies. Do not change build, CI, or packaging configuration.

When you are done, leave the working tree containing your change.
