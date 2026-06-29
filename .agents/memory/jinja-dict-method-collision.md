---
name: Jinja dict-method collision
description: Why a dict key named "items" (or keys/values/get) silently breaks in a Jinja template.
---

In Jinja, `foo.bar` tries `getattr(foo, "bar")` BEFORE `foo["bar"]`. For a dict
passed to a template, `foo.items` therefore returns the built-in `dict.items`
method object, never your `"items"` key — so `{{ comp.items|length }}` raises
`object of type 'builtin_function_or_method' has no len()`.

**Why:** bit the Compound Compilation report — the build dict had an `"items"`
key (raw-material breakdown); the template rendered fine in a pure unit test but
500'd only at route render. Renamed the key to `"materials"`.

**How to apply:** never name a template-facing dict key `items`, `keys`,
`values`, `get`, `update`, `pop`, etc. If you must, access it as `foo["items"]`
(bracket form skips the attribute lookup). Prefer renaming the key.
