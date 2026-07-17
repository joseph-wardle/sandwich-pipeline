"""Playblast flows for previs files (one-Maya-file-per-sequence).

`dialog.PrevisPlayblastDialog` is the artist-facing window; it adapts the
render-only base (`shot.dialog.MPlayblastDialog`) to two previs-only
surfaces:

- a **Shot tab** that swaps in a per-previs-shot dropdown when the open
  scene carries previs state, and
- a **Sequence tab** that stitches every shot's primary into one clip via
  `sequence.MSequencePlayblaster`, routed to ShotGrid dailies from the
  viewer's Confirm panel.

`take.MTakePlayblaster` renders one shot's primary for the previs panel's
take-export flow. RLO-file playblasts keep the base dialog's behaviour.
"""
