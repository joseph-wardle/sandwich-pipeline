# BrushCard

Places hand-painted brush-stroke cards in 3D (Nuke 17 USD-based 3D system)
and renders them through the shot camera as an **alpha mask**. Beauty,
lighting, motion blur, DOF and CG holdouts are out of scope.

| File | Purpose |
|---|---|
| `brush_card.py` | Group callbacks and helpers (dropdown, refresh, unique naming) |
| `build_brush_card.py` | `build()` creates a BrushCard group from scratch |
| `../toolsets/brush_card.nk` | The same group as a pasteable ToolSet |
| `../menu.py` | **SKD > BrushCard** menu entry |

## Install

Nothing to do when Nuke is started through the pipeline launcher:

* `NUKE_PATH` already points at `src/pipe/dcc/nuke/site`, which puts
  `SKD_Tools/scripts` on the Python path.
* `BRUSH_CARD_LIB` is set by `launch.py` to
  `/job/sandwich/05_production/lighting/crepuscular_cards`. Set it yourself
  before launching to use a different library (e.g. for testing).

Without the launcher, add `site` to `NUKE_PATH`. If `BRUSH_CARD_LIB` is
unset the tool falls back to the production path above.

## Stroke library

* Formats: `.png`, `.exr`, `.tif`, `.tiff`. The dropdown shows file names
  without the extension.
* The alpha channel is the mask, so strokes need real transparency. An image
  on a solid black background with no alpha renders as a rectangle.
* Strokes are assumed to be **straight** (unpremultiplied) alpha, which is
  what PNG always is. Turn off *premultiply stroke* on the card for
  premultiplied EXRs.
* The card's shape follows the image's aspect ratio automatically.
* The mask is only as solid as the painting: a stroke painted at 80% opacity
  gives 80% alpha.

## Usage

1. **SKD > BrushCard** (or paste `toolsets/brush_card.nk`).
2. Pick a `stroke`. Press **Refresh** after adding files to the library.
3. Position the card with translate / rotate / scale / uniform scale.
   Shot scenes are in centimeters, so cards start at `uniform scale` 100
   (1 m wide).
4. Chain cards through their `scene` input, then build the mask:

```
BrushCard1 -> BrushCard2 -> BrushCard3 -> ScanlineRender2 (input "scn", 1)
Camera4 (shot cam.usd)                 -> ScanlineRender2 (input "cam", 2)
ScanlineRender2 alpha                  -> Write
```

A GeoMerge of unchained cards works too. Leave the ScanlineRender2 `bg` input
(0) empty, or connect a plate to set the output format. Set its
`objects_mask` to `/BrushCards//*` if set geometry is also in the scene.

## How it stays unique

Every card lives in one merged USD stage, so two things are derived from the
group name whenever a card is built, pasted, loaded or renamed:

* the card prim: `/BrushCards/<GroupName>`
* the internal texture nodes: `<GroupName>_Read`, `<GroupName>_Premult`.
  Nuke names each card's material after the node feeding it, and duplicate
  names make every card render the same stroke.

Don't rename the nodes inside the group by hand.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Dropdown shows `<no strokes found>` | The library folder is missing or empty. Refresh shows the path it looked in; check `BRUSH_CARD_LIB`. |
| Every card shows the same stroke | Internal nodes were renamed by hand. Rename the group (any name) to resync. |
| Card renders as a solid rectangle | The stroke image has no alpha channel. |
| Card is invisible | No stroke selected (the internal GeoCard is disabled and passes the scene through), or the card is behind the camera. |
| A stroke removed from the library is still in the dropdown | Intentional: saved comps keep their stroke. Pick another one to drop it. |
| `ImportError: brush_card` in the Script Editor | Nuke was started without the pipeline `NUKE_PATH`. The card still renders; only the dropdown and Refresh need the module. |
