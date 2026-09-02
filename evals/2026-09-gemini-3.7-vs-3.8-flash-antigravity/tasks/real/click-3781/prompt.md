# Task

You are working in a checkout of `pallets/click`.

## Support pathlib.Path in edit filename

Would you welcome a PR to avoid this?

```
Argument of type "Path" cannot be assigned to parameter "filename" of type "str | None" in function "edit"
  Type "Path" is not assignable to type "str | None"
    "Path" is not assignable to "str"
    "Path" is not assignable to "None"Pylance[reportArgumentType](https://github.com/microsoft/pyright/blob/main/docs/configuration.md#reportArgumentType)
```

Easy workaround with:
```py
click.edit(filename=str(export_common.path))
```

but would be nice if didn't need `str`.

## Failing tests

These tests currently fail and must pass when you are done. Their contents are not shown to you, and they are not present in this working tree.

- `tests/test_termui.py::test_edit_pathlib`

## What to do

Fix the behaviour described above by editing the project's source code. Work
autonomously and finish the change; there is nobody available to answer
questions.

Do not modify, delete, or disable any existing test. Do not add new
dependencies. Do not change build, CI, or packaging configuration.

When you are done, leave the working tree containing your change.
