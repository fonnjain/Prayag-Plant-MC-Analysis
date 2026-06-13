---
name: Jinja macro import in child templates
description: Where to put {% from %} macro imports in Flask/Jinja templates that extend a base
---

In Jinja child templates that use `{% extends "base.html" %}`, a `{% from "_macros.html" import info %}` must be placed **inside** the `{% block content %}` (not at the top of the file before the block). Imports outside any block in a child template are not in scope where the block content renders.

**Why:** Child templates only emit content inside their blocks; statements outside blocks are effectively ignored for rendering scope, so a top-of-file import leaves the macro undefined inside the block.

**How to apply:** Put the macro import as the first line inside `{% block content %}` on every page template that calls the macro.
