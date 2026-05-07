# `sandwich-pipeline` Coding Standard

Thanks for looking after this codebase! It’s been helpful to many and we hope to keep it tidy and easy to understand for those who use it in the future.

Good pipeline code is:
* easy to read
* easy to trace
* easy to modify

Every function and every file should help future developers quickly understand what it does, 
why it exists, and how to extend it safely without needing the original author beside them.
Here are some principles to help you achieve this goal:

### Comments explain why, not what

Avoid comments that merely restate the code. If code needs a comment to explain what it does, first ask whether it can be rewritten more clearly.

### Everything should be typed

Every function should have parameter types and a return type, including private helpers and class members. 

All maintained code should pass:

* `ruff format`
* `ruff check`
* `ty check`

`Any`, `cast`, and `type: ignore` are sometimes necessary. Keep them narrow, and explain why.

### Avoid mega-functions and mega-files

Each file should be a clear set of related functions.  If files start poking past 500 or 1000 lines, pause, 
and consider if the file should be split, either by seperating unrelated concerns, or by dividing in a structured module with clear sub-concerns.

Conversely, bouncing across many files imcreases the mental overhead of understanding a system. 
Keep call stacks shallow, and split code only when splitting improves comprehension. 

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

Avoid helpers that look harmless but secretly mutate the scene or write to disk

### Consult official documentation first

Before changing Maya, Houdini, Nuke, Substance Painter, USD, Qt, ShotGrid, or similar integrations:

* read the official docs
* verify exact API names and side effects
* confirm whether existing code depends on a workaround or quirk

Do not assume the current code is correct just because it already exists.

### Keep canonical schema and field names centralized

Magic strings should be named constants, not repeated bare literals. This makes schema changes searchable and safer.

Examples:

* ShotGrid field names
* metadata keys
* variant names
* protocol markers
* node type names

### Error handling must help recovery

Every failure path should help answer:

* what failed
* what the user was trying to do
* what they can do next
* what a developer may need to inspect

#### Distinguish user-facing and developer-facing errors

User-facing messages should be brief, specific, and actionable. 
Artists should not need to parse Python exceptions.
Catch errors at the UI boundary and convert them into clean artist-facing messages. Log the technical details separately.
If an artist doesnt read an error message, that is your fault.

Bad:

* “Publish failed”
* “Failed to evaluate unknown context option 'RENDER_THUMBNAIL'.”

Better:

* “Could not publish because the current Maya scene has not been saved.”
* “Could not render the thumbnail because no camera has been selected”

Developer-facing diagnostics should contain the full context needed to fix an issue without guesswork

Example:

```
14:44:33.144: Node Error: Failed to evaluate unknown context option 'RENDER_THUMBNAIL'.
              Context:    /stage/componentoutput1/auto_thumbnail_camera
```

## When to Write External Documentation

The code should remain the primary source of truth for implementation details. 
Don't ask developers to read a seperate wiki entry to understand what you've written.

However, artist facing documentation is encouraged! When you make a new tool, add notes and images that explain how to use it to this repositories wiki. 
Not only will this help inform artists of the resources avalible to them, but it will also help you build a presentable portfolio.

---

## Updating existing code

This codebase does not currently follow this standard everywhere. 😳

Help us out (and yourself) by leaving this codebase better than you found it!
Dont assume that just because code works it cant be better.
There are plenty of oppurtinities for improvement!

That being said, you should be *opportunistically* improving the code you touch as you extend it with new features. 
Do not try to fix code for the sake of fixing code. Changing existing code purely for stylistic reasons increases 
the chance of unintended side effects for tools people actively use.

---

## Closing Principle

**Write code that a tired, under-trained future TD can safely read, trust, and extend during crunch.**

That tired, under-trained future TD will certainly be you at some point. Ask me how I know :)
