---
name: Jinja2 macro definition order
description: Jinja2 macros must be defined before any call site; no hoisting.
---

## Rule
In Jinja2 templates, `{% macro name(...) %}` must appear **before** any `{{ name(...) }}` call site in the same template. Jinja2 does NOT hoist macros.

## Why
Discovered when plan_board.html defined `{% macro machine_card(mp) %}` at the bottom of the `{% block content %}` block but called `{{ machine_card(mp) }}` inside for-loops earlier in the same block. At render time Flask raised `UndefinedError: 'machine_card' is undefined`.

## How to apply
- Always place `{% macro %}` definitions at the **top** of the block (or template), before any loops or conditionals that call them.
- If a macro is shared across multiple pages, move it to a separate `_macros.html` file and `{% from "_macros.html" import macro_name %}` at the top.
- Debug hint: this error will not show up in `env.parse()` (syntax check passes); it only surfaces at render time.
