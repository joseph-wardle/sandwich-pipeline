"""Maya's side of assembly decomposition: Split Pieces, Edit Piece, Save Piece.

- A group's name is the whole declaration: `<asset>` or `<asset>__<variant>`.
- Splitting runs on Linux only: a child's `publish/tex/<variant>` is a symlink
  to the assembly's textures, and Windows cannot make it.
- A child has no `model.mb`; it is modelled only from inside the assembly, so
  an asset modelled by hand is never joined by a split.
- A piece is placed by moving its prim in the assembly, never its open copy.
- Publishing an assembly rebuilds every child whose placed variant is unbuilt,
  which regenerates that child's builder graph; renamed nodes survive, the
  rest are replaced.
"""
