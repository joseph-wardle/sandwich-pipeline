"""Playblast flows for previs files (one-Maya-file-per-sequence).

`dialog.PrevisPlayblastDialog` is the artist-facing window; it adapts the
shared `MPlayblastDialog` to two previs-only surfaces:

- a **Shot tab** that swaps in a per-previs-shot dropdown (with an optional
  Compare-alternates checkbox) when the open scene carries previs state, and
- a **Sequence tab** that stitches every shot's primary into one
  dailies-ready movie for ShotGrid upload.

The grid compositor lives in `compare.MComparePlayblaster`, the sequence
stitcher in `sequence.MSequencePlayblaster`. RLO-file playblasts continue to
live in the sibling `shot/` package.
"""
