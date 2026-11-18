"""The farm doctor: diagnoses why Tractor tasks fail, finds failing blades, and
retries what retrying can fix.

Tractor retries a render by itself three times, and only for a few exit codes;
past that, someone had to press Retry Errors, often over and over. The doctor
reads each errored task's log, works out why each attempt failed
(`diagnose`), and decides whether another try can help (`policy`). Flaky
failures are retried with longer and longer waits, those of a bad blade on
another blade, and those that fail the same way everywhere, such as a full
disk quota or a node that won't cook, are left for a person, with the reason.

The Sandwich Renderer window shows all of this on its Farm Doctor tab. The
headless doctor (`__main__`) runs the same checks every few minutes and, when
told to act, retries for real with its own Tractor login (`login`). It only
touches the production's jobs.
"""
