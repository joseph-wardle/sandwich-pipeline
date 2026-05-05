# `sandwich-pipeline` Coding Standard

## Purpose

This codebase is a proven production infrastructure for BYU’s animated short film production. It must empower to do their best work even with tight restrictions, and it must also serve as a teaching artifact for the student TDs who inherit it.

This standard is not about stylistic purity. It exists to help maintainers make safe, readable, practical changes when context is incomplete and time is limited.

When a rule conflicts with production reality, choose the option that makes the code easier for a new maintainer to read, trust, and modify safely.

---

## Priority Order

All decisions should follow this order:

1. **Self-documenting code**
2. **Readability for future maintainers**
3. **Lightweight, simple code with minimal abstraction**
4. **Good user-facing errors**
5. **Correct and clear API usage across complicated tools**

If a design improves one lower priority by harming a higher one, it is usually the wrong design.

---

## Core Philosophy

Good pipeline code is:

* easy to read in one pass
* easy to trace from entry point to side effect
* explicit about what external system it touches
* named in terms artists and TDs already use
* typed enough that editors and checkers help the next maintainer
* boring in the best way

The code should teach the reader how the workflow works.

Prefer:

* direct code over framework-like code
* explicit data flow over hidden state
* thin wrappers over abstraction layers
* local clarity over clever reuse

---

## Code Organization and Naming

### Organize by workflow and domain

A new maintainer should be able to find code by asking:

* Where is Maya publishing?
* Where is the Houdini environment built?
* Where is the ShotGrid version created?
* Where is the Nuke loader UI?

The path to a given script should explain its function, and finding the implementation of a function should be natural due to the folder structure.

### Name things by production meaning

Names should reflect the terminology used by the DCC, API, or production workflow.

Use nouns for data and verbs for actions:

* `build_publish_context`
* `validate_component_output_node`
* `resolve_texture_directory`
* `create_shotgrid_version`

If a function name needs a long comment to explain what it does, the structure or naming is probably wrong.

### File size

There is no hard line limit, but very large files are a signal to review whether unrelated concerns are accumulating. Long files are acceptable when they remain cohesive. 

---

## Function and Module Design

### Functions should represent one workflow step

A good function usually does one understandable action.

Split code when splitting improves comprehension. Do not split code into tiny helpers if doing so makes the workflow harder to follow.

### Prefer shallow call stacks

A maintainer under crunch should be able to trace a workflow without bouncing across many files.

A good pattern is:

* a top-level function that reads like a checklist
* a small number of helpers that hide distracting detail

### Keep side effects explicit

Functions that touch the outside world should be obvious.

Examples:

* reading or writing files
* mutating DCC scene state
* opening dialogs
* creating ShotGrid records
* launching subprocesses
* modifying environment variables

Avoid helpers that look harmless but secretly mutate the scene or write to disk.

### Prefer simple orchestration over internal frameworks

Avoid building mini-frameworks inside the pipeline.  Reusable code is good. Premature architecture is not.

---

## Typing and Data Modeling

### Everything should be typed

Every function should have parameter types and a return type, including private helpers.

Code should pass:

* `ruff check`
* `ruff format`
* `ty check`

Types are part of the documentation.

### Localize dynamic boundaries

`Any`, `cast`, and `type: ignore` are sometimes necessary, especially around DCC APIs and legacy integrations. Keep them at the narrowest possible boundary.

---

## DCC and External API Integration Principles

### Consult official documentation first

Before changing Maya, Houdini, Nuke, Substance Painter, USD, Qt, ShotGrid, or similar integrations:

* read the official docs
* verify exact API names and side effects
* confirm whether existing code depends on a workaround or quirk

Do not assume the current code is correct just because it already exists.

### Preserve native terminology

If Maya calls it a node, call it a node.
If Houdini calls it a parameter, call it a parameter.
If USD calls it a prim, layer, or payload, use those words.
If ShotGrid uses asset, shot, task, or version, use those words.

### Thin wrappers are encouraged when they add safety or clarity

A wrapper is good when it:

* centralizes repeated validation
* enforces required options
* normalizes inconsistent return shapes
* improves user-facing errors
* makes call sites clearer

A wrapper is bad when it only adds indirection or hides which external system is being called.

### Guard external API calls at the boundary

DCC and USD APIs often return empty results or invalid objects without raising.

Examples of required guarding:

* do not index `mc.ls(...)[0]` without checking the result
* check `prim.IsValid()` after `GetPrimAtPath()`
* do not assume Houdini context or parameters always exist

Fail early with clear production-facing messages.

### Keep canonical schema and field names centralized

Magic strings used as:

* ShotGrid field names
* metadata keys
* variant names
* protocol markers
* node type names

should be named constants, not repeated bare literals.

This makes schema changes searchable and safer.

### Be honest about host constraints

DCC scripting often involves global states, mutable scene states, version quirks, and platform-specific workarounds. Do not contort the code to pretend these are not real. Isolate them, name them clearly, and document the reason when needed.

---

## Error Handling and Logging

### Error handling must help recovery

Every failure path should help answer:

* what failed
* what the user was trying to do
* what they can do next
* what a developer may need to inspect

### Distinguish user-facing and developer-facing errors

User-facing messages should be:

* brief
* specific
* actionable
* written in production language
* free of traceback noise

Developer-facing diagnostics should be:

* concise
* relevant
* logged with useful identifiers
* detailed enough to debug without guesswork

Artists should not need to parse Python exceptions.

### Catch exceptions deliberately

Catch only what you can handle meaningfully.

Use broad catches only at true workflow boundaries, cleanup paths, or intentionally optional integrations, and log enough context to debug them.

If an exception is intentionally suppressed, explain why.

### Validate early at boundaries

Check assumptions close to the input edge:

* required scene state
* required nodes
* expected file layout
* required config values
* valid publish roots
* required ShotGrid links

Fail early rather than allowing downstream crashes.

### Use logging, not `print`

Use the `logging` module for diagnostics in maintained production code.

Logs should be sparse, useful, and searchable.

---

## Comments and Docstrings

### Comments explain why, not what

Avoid comments that merely restate the code.

### Prefer restructuring over explanatory comments

If code needs a comment to explain what it does, first ask whether it can be rewritten more clearly.

### Use docstrings deliberately

Docstrings are useful when they improve:

* IDE discoverability
* public or reused helper clarity
* workflow boundary understanding
* non-obvious side effects
* return or failure expectations
* wrappers around external APIs

Docstrings are not required for every trivial private helper.

### Module-level docstrings

Each maintained module should have a short docstring explaining what it is for and where it fits in the pipeline.

### Keep comments and docstrings fresh

If a comment or docstring is stale when you touch code, update or remove it immediately.

---

## Guidance for UI-Facing Tools

### UI code should optimize for artist confidence

A tool should help the user understand:

* what action they are taking
* what the tool will do
* what went wrong
* how to recover

### Artist-facing messages are required

Any error shown to an artist must use plain production language and, where possible, tell them what to do next.

Bad:

* “Publish failed”
* “Invalid input”
* “ShotGrid error”

Better:

* “Could not publish because the current Maya scene has not been saved.”
* “Could not create the version because this task is missing its ShotGrid link.”
* “The selected output directory is not inside the asset publish root.”

### Do not leak Python exceptions into the UI

Catch errors at the UI boundary and convert them into clean artist-facing messages. Log the technical details separately.

### Keep UI and workflow logic separated

Dialog classes should coordinate user interaction. They should not contain large chunks of publish logic, filesystem work, or API orchestration.

### Long-running work should be observable

UI tools should make expensive operations visible through progress, status, or clear blocking behavior. Do not leave artists guessing whether the tool is hung.

---

## Guidance for Modifying Legacy Code

### Improve touched code opportunistically

When modifying a file, make safe local improvements where they clearly help.

Do not turn a small fix into a multi-file cleanup campaign.

### Preserve behavior unless behavior change is explicit

A style improvement should not silently change pipeline semantics.

If behavior must change, make that change explicit and document why.

### Extract new logic out of legacy hubs

If a legacy file is already too large or too mixed-purpose, prefer adding new logic in a small adjacent module and thinning the old entry point rather than making the large file larger.

### Mark intentional workarounds

If code exists to work around a DCC bug, platform bug, or external API limitation, label it clearly so it can be found and removed later.

---

## Tooling and Enforcement

All maintained code should pass:

* `ruff format`
* `ruff check`
* `ty check`

Do not suppress lint or type errors casually.

If `# noqa`, `# type: ignore`, or similar suppression is necessary, keep it narrow and explain why.

---

## When to Write External Documentation

The code should remain the primary source of truth for implementation details.

Write external documentation when the information is:

* cross-cutting across many modules
* operational rather than code-local
* about workflow policy rather than implementation
* required before someone can safely modify the system
* difficult to infer from code alone

---

## Closing Principle

**Write code that a tired, under-trained future TD can safely read, trust, and extend during crunch.**

That tired, under-trained future TD will certainly be you at some point. Ask me how I know :)
