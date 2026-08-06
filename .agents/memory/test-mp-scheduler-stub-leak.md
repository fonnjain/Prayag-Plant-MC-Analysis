---
name: test_mp_scheduler stub leak
description: autouse fixture in test_mp_scheduler.py leaked a stub-bound mp_scheduler into sys.modules, poisoning subsequent test files.
---

# test_mp_scheduler stub leak

## The rule
The `patch_mp_model` `autouse` fixture in `tests/test_mp_scheduler.py` **must save and restore `sys.modules["mp_scheduler"]`** around each test, not just `sys.modules["mp_model"]`.

## Why
The fixture:
1. Puts a stub `mp_model` (with `get_params → MpParams(min_run_block_hours=5.0)`) into `sys.modules`
2. Deletes `mp_scheduler` so it re-imports bound to the stub

After `yield`, `monkeypatch` restores `sys.modules["mp_model"]` to the real module. But the **re-imported `mp_scheduler`** stays in `sys.modules` with its module-level `_mp` still pointing to the old stub object. Every test that runs afterwards and calls `mp_scheduler.run_shift_schedule` goes through the stub's `get_params`, getting `min_run_block_hours=5.0` even when the caller monkeypatches `mp_model.get_params` to return `None`.

This caused `test_scheduler_min_run_block_fallback` (in `test_mp_seed_rates.py`) to get 5.0 instead of the expected 2.0 fallback — passes in isolation, fails in full suite.

## How to apply
In any `autouse` fixture that deletes a module from `sys.modules` to force re-import:
```python
real_mod = sys.modules.get("mp_scheduler")
# ... patch and yield ...
sys.modules.pop("mp_scheduler", None)
if real_mod is not None:
    sys.modules["mp_scheduler"] = real_mod
```
This pattern must be applied for EVERY module that is force-deleted, not just the one being stubbed.
