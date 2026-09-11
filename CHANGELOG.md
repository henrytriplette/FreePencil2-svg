# FreePencil2 - Changelog

## [Unreleased]
### Fixed
- **Tiled single-layer exports with registration marks were not valid
  XML.** `svg_document` only declared `xmlns:inkscape` on the `<svg>` root
  when the export was split into layers, but `tile_layers` always adds the
  `regmarks` group when marks are on, and that group carries
  `inkscape:groupmode="layer"` / `inkscape:label` like every layer group.
  So with `layers=NONE`, more than one tile and marks enabled, every sheet
  used the `inkscape:` prefix without declaring it. Inkscape and browsers
  are lenient about this; `xml.etree` (`unbound prefix`) and strict plotter
  drivers are not, and refused the files.

  The declaration now follows the groups actually written
  (`_is_layer_group`): it appears whenever any `<g>` carries the prefix and
  is still omitted from the plain single-layer file, which stays free of
  Inkscape-specific markup as before. Regression test: a 2x1 tiled
  single-layer export with marks parses with `xml.etree.ElementTree`, and
  the untiled single-layer file still has no `xmlns:inkscape`.

## [2.13.0] - 2026-09-11
### Changed
- **Where a line disappears behind something is now found to 1/256 of the
  edge, not 1/8.** A partially hidden edge used to be cut at the boundary
  between two of its 8 visibility samples, so the end of every line that
  runs behind a silhouette was off by up to an eighth of the edge - a
  visible gap or overshoot at each contour crossing on long CAD edges.
  The cut is now bisected five times against the depth pass between the
  last visible and first hidden sample (`REFINE_STEPS`), which only costs
  five more projections for the partially visible edges. A side effect on
  the demo scene: pieces from neighbouring edges now meet at the true
  crossing, so they merge (294 -> 288 paths at 400 px) and the drawn
  length grows by 1.5% - that is the line that was missing.

  This is the one change that touches the cross-version claim. Counts
  (edges, paths, points, layer split) still match exactly on 4.2.23 /
  4.3.2 / 4.5.6 / 5.2.1, but the refined cut lands wherever the EEVEE
  depth pass of that version puts the pixel, so the drawn length differs
  by 0.2 mm in 1232 (1231.9 on 4.2/4.3, 1232.1 on 4.5/5.2). At 1600 px
  the versions already disagreed by more than that before this release.

- **The draw order is improved with 2-opt after the greedy sort.** The
  greedy nearest-neighbour pass leaves long jumps back to lines it skipped;
  reversing a run of the sequence removes them and changes nothing but the
  two pen-up moves at its ends (`two_opt`). Measured on the demo scene:
  pen-up travel 782 -> 590 mm (-24.5%) at 400 px, 716 -> 527 mm (-26%) at
  1600 px, with the drawn set unchanged. The window of candidates scales
  with the path count (3e6 comparisons a pass) so 3000 paths look at every
  pair and 30000 hatch lines look 100 ahead; 400 paths take 0.25 s.

- **Pen home** (Advanced -> Paths) says which corner the plotter parks in
  (top left by default, as AxiDraw). The sort starts from there, so the
  first move is short instead of always assuming the SVG origin.

### Added
- **The preview refreshes on its own.** 2.12.0 made the overlay admit it
  was stale; now a timer (`bpy.app.timers`, no depsgraph handlers) polls
  the same stamp four times a second and recomputes once the camera,
  meshes and settings have held still for 0.75 s, so dragging the camera
  does not fire a depth render on every event. `Auto refresh` sits next
  to the preview buttons and is on by default; turn it off on a scene
  where the preview takes long enough to notice. The panel says
  "updating" rather than "refresh" while it is on. Verified in a windowed
  Blender: moving the camera produced a fresh overlay 1.4 s later.

- **Batch exports run modally and can be cancelled.** `Export checked
  cameras` and `Export frame range` used to block Blender for the whole
  run - 250 frames at 5 s each is 20 minutes with no way out. Pressed
  from the panel they now write one file per timer tick, show
  `SVG 12/250 frames - Esc to cancel` in the status bar and the progress
  cursor, and Esc stops after the current file; the original camera or
  frame is restored either way. Called from a script (`bpy.ops` in
  background mode) `execute` still runs synchronously, so the batch
  harness and the tests are unchanged.

- **Three hand-marked line sources**, off by default, for adding lines
  without touching the vertex colours: **Freestyle marks** (Edit mode:
  Edge > Mark Freestyle Edge; read from the `freestyle_edge` attribute
  so it works on every supported version), **Sharp marks**, and
  **Crease angle** (dihedral angle at or above a threshold, 60 degrees by
  default). They take part in the layer split like any other source.

- **Layers get their own stroke colour** (`Colour layers`, on by default)
  so the pens are told apart in Inkscape and vpype; the colour is fixed
  by layer name, so a layer keeps its colour when written to its own
  file. Plotters ignore colour and a single-layer export stays black.
  **Outline pen width** and **Hatch pen width** (0 = same as the pen)
  give those two layers their own `stroke-width`, since the outline layer
  was documented as "for a heavier pen" but always wrote the main width.

### Tests
- 84 (was 77), all passing on 4.2.23, 4.3.2, 4.5.6 and 5.2.1: the cut
  refinement on a synthetic depth buffer, 2-opt shortening and preserving
  the line set, pen home corners, the three new sources, layer colours
  and widths, the auto-refresh timer following the preview and its
  toggle, and the batch operators keeping a synchronous `execute`.

## [2.12.0] - 2026-09-10
### Changed
- **STEP0 no longer builds the raster pipeline by default.** The SVG export -
  the point of this fork - reads the vertex colours straight off the mesh and
  renders only a depth pass; it never touches the compositor. STEP0 ran STEP1,
  STEP2 and STEP3 unconditionally anyway, so a user who only wanted an SVG got
  an AOV group injected into every material, a compositor tree built, the
  viewport switched to Rendered and every material previewed in white. Three
  visible changes with nothing to do with the file they were after - and the
  usual reading of that is "the add-on broke my scene".

  STEP0 now stops after the paint. `fpm_auto_raster` (off by default) brings
  back STEP2/STEP3, and the raster-only options in the STEP0 panel (AA,
  supersampling, AOV detection, white preview, File Output) are grouped under
  it and greyed out while it is off. The button says which of the two it will
  do. Existing raster users tick one box; the twelve smoke tests that used
  STEP0 as a shortcut to build everything now opt in explicitly.

  The BLEND-to-HASHED conversion stays ungated: alpha-blended materials do not
  write depth in EEVEE, so it matters to the SVG depth pass too, not just AOVs.

- **The sidebar says where you are.** The parent panel now shows camera,
  colour separation and preview state as ticks or warnings, then offers the
  next button to press. Where the SVG panel used to say "Run STEP0 or STEP1
  first" it now shows the actual Auto setup button.

- **Panel order was undefined between two pairs.** `FP_PT_SvgSources` and
  `FP_PT_Step0` both declared `bl_order = 0`, and `FP_PT_SvgAdvanced` and
  `FP_PT_Step1` both declared `1`, so the sidebar order was down to
  registration order - exactly what the `_FPSub` docstring says `bl_order`
  exists to prevent. The SVG family moved to -3/-2/-1, and a test now asserts
  the values stay unique.

- **Labels say which branch a panel belongs to** - STEP2 and STEP3 are marked
  "raster render only", STEP5 covers both batches, and the implementation
  vocabulary is off the buttons ("Generate Sample Node" for two different
  operators is now "Set up AOVs" and "Build compositor nodes"; "Select Node
  Type" is "Node type").

- **Panel labels now fit the sidebar, and keep their translations.** Checked
  in a real window: at default sidebar width a 38-character panel header
  renders in full, while a 43-character checkbox label is ellipsized - "Also
  set up the raster render (STEP2/STEP3)" came out as "Also set up the
  r...r (STEP2/STEP3)". That toggle and the STEP2/STEP3/STEP5 headers were
  shortened to fit.

  Renaming those headers had also orphaned their `.po` entries, since
  translations are keyed by the English string: five panel headers would have
  rendered untranslated in Japanese. Six entries re-keyed in both locales.
  Neither the test suite nor CI can see either class of defect.

- **Both camera batch buttons sit with the camera list.** The ticks feed the
  SVG batch and the raster batch, but only the raster button was next to them;
  the SVG panel now also reports how many cameras are ticked and where to
  change that.

### Added
- **Reset panel.** One button that takes the add-on back out of the scene:
  the AOV group is removed from every material, the compositor nodes STEP3
  built are deleted, the AOV slots are dropped, the vertex colours are
  removed, and the render and viewport settings are put back to what they
  were before STEP2 first ran.

  The "before" values are captured the first time `setup_aov` or
  `setup_compositor` writes to the scene, and stored as JSON in a scene
  custom property, so they survive saving and reopening the .blend. Only the
  first capture counts - re-capturing would record the add-on's own writes
  as the original state. Viewport shading lives in `reset_scene.py` rather
  than `fp_core.py`, which must stay headless-safe and not touch
  `context.screen`.

  Compositor nodes the user added are left alone: only nodes carrying
  FreePencil's own labels or node groups are removed. Material `pass_index`
  values written by STEP2's material-ID counting are not restored.

  The vertex colours go too, so STEP1 has to be re-run afterwards; the
  operator confirms first and registers UNDO.

### Changed
- **The SVG page fit now defaults to Camera frame** instead of Drawing
  bounds, so what is composed in the 3D view is what lands on the paper.
  Drawing bounds stays available for filling the sheet, and keeps its
  documented advantage - the merge tolerance in mm bites at the scale of
  the drawing rather than at whatever size the subject happens to be.

  The batch harness (`dev/batch/fp_batch.py`) pins `fit="DRAWING"`
  explicitly rather than following the new default, so its SVG metrics stay
  comparable with reports from previous runs.

### Fixed
- **"Keep hidden lines" also dropped the camera crop.** The option is
  documented as "Skip hidden-line removal (for diagnosis)", but
  `visible_spans` returned early with every edge marked visible, which
  skipped the `inside` frame test as well as the depth test. Nothing
  downstream re-applies it - `visible_polylines` just projects what it is
  handed, and a non-tiled export never clips to the page - so geometry
  outside the camera frame ended up in the SVG. With the default
  `fit=DRAWING` that off-screen geometry stretched the bounding box and
  shrank the actual subject to a fraction of the page, which read as
  "the preview does not match the export".

  Now only the occlusion test is skipped; the frame crop stays. Regression
  test: a cube placed 30 m to the camera's side contributes no edges with
  `keep_hidden=True`, while lines inside the frame survive.

- **The viewport preview never admitted it was out of date.** The overlay is
  a snapshot: `refresh_preview` stores world-space segments whose visibility
  was decided against a depth render from the camera as it stood at that
  moment, and nothing invalidated it - the add-on registers no
  `bpy.app.handlers` at all. Move the camera and the segments stay glued to
  the model, so the positions still look right, but the hidden-line result
  belongs to the old viewpoint; since the overlay draws with
  `depth_test_set('NONE')`, every stale back-face line paints straight
  through the model. That is the "ghost lines" doubling.

  The preview now stamps what it was computed from - camera transform and
  lens, frame, render resolution, every mesh object's transform, and the
  settings that actually change the 3D lines - and compares it on each draw.
  A stale overlay is drawn in light grey instead of near-black, and the
  panel replaces the segment count with "Preview is out of date - refresh".

  Deliberately **not** auto-refreshed: a refresh renders a depth pass and
  re-extracts every line, so hanging it off a depsgraph handler would stall
  the viewport on every camera nudge.

  Paper-side settings (page, margin, pen, tiling, jitter, fit) do not mark
  it stale, because they do not change the lines drawn in the 3D view.
  Mesh edits and repainting are still not detected - press refresh.

## [2.11.2] - 2026-09-10
### Added
- **SVG metrics in the batch report.** Each model now also goes through the
  SVG export, and the report gains path count, drawn distance, pen-up
  travel, travel ratio and estimated plot time, with a link to the SVG
  itself. Where `ink`/`components` say whether lines appeared, these say
  whether the result is practical to plot. `--no-svg` skips it.

  Deliberately **not folded into the score** - the existing formula
  (ink 45% + fragmentation 30% + separation 25%) stays as it is, so
  reports remain comparable with previous runs.

  Measured on the 1,047,643-face model: 2481 paths, 9.4 m drawn, 2.3 m
  travel, travel ratio 0.24, about 7 minutes, adding 4.1 s to the run.

### Fixed
- **The batch's adaptive retry silently did nothing.** `choose_adaptive_
  overrides` returns `fpm_*` keys, but the filter applying them still read
  `key.startswith("fp_")` - which is false for `fpm_`, since the third
  character is `m` rather than `_`. Every retry therefore re-ran with
  unchanged settings and produced the same result. Introduced by the
  namespace rename in 2.8.2.

## [2.11.1] - 2026-09-09
### Fixed
- **The SVG export failed outright on Blender 4.2**: `OPEN_EXR` there has
  no `BW` colour mode (only RGB/RGBA), so setting it raised `TypeError`
  and the depth pass never rendered. `compat.set_color_mode` now falls
  back to the first mode the build accepts; the reader only takes the
  first channel, so RGB gives the same result.

### Changed
- **All four supported Blender versions are now actually tested.** 4.2.23
  and 4.3.2 were installed alongside 4.5.6 and 5.2.1; the suite (70 tests)
  passes on each, the package builds and validates on each, and the SVG
  export produces byte-identical output across all four - 574 edges,
  71 paths, 195 points, 1458.3 mm drawn, 1578.8 mm travel, same layer
  split. Previously only 4.5 and 5.2 had been exercised, while the
  manifest claimed 4.2.0 as the minimum.

## [2.11.0] - 2026-09-09
### Added
- **Tiling across sheets**, for plotting a drawing larger than the bed.
  The drawing is fitted to the composite size (columns x page by rows x
  page) and cut into page-sized sheets written as `<name>_r1c1.svg` etc.

  Merging, simplification, jitter and draw-order sorting run **once on the
  composite** before cutting; doing them per sheet would make the lines
  disagree across a seam. Verified that clipping preserves drawn length
  exactly - 5588.4 mm across four sheets against 5588.4 mm on the
  composite - so nothing is lost or doubled at a join.

  Registration marks (on by default) are added to every sheet. The margin
  applies to the composite rather than each sheet, so content reaches an
  inner seam, which is what makes the join continuous.

### Changed
- `build_svg` split into `prepare_layers` (transform, merge, simplify,
  jitter, sort) and `svg_document` (serialise), so tiling can prepare once
  and serialise many times. Behaviour is unchanged.

## [2.10.0] - 2026-09-09
### Added
- **Hand jitter** (off by default). Wobbles the lines so they read as drawn
  rather than machined. The offset is a function of position rather than a
  random value per point, so lines that shared an end still share it after
  wobbling - per-point randomness would open a gap at every junction.
  Straight runs are densified first, since a two-point line has nothing to
  bend. A 0.5 mm wobble lengthens the drawing by under 1%.

- **Frame range batch.** `Export frame range` writes
  `//svg_exports/frame_####.svg` across the scene's frame range and step,
  re-evaluating the meshes each frame so deforming rigs export correctly,
  and restores the current frame afterwards.

## [2.9.0] - 2026-09-09
### Added
- **Depth-cued line weight.** `Layers -> By depth` bands the drawing from
  near to far so each band can take its own pen, and thins the stroke
  width towards the far band so the depth reads in the SVG itself. The
  band range is taken from the visible lines only - including hidden edges
  pushed the range back and bunched everything visible into the near bands
  (asking for 3 bands produced 2).

- **Hatching from the diffuse light pass** (off by default), written as its
  own `hatch` layer. Instead of clipping hatch lines to island outlines,
  parallel lines are drawn across the page and cut against the light pass,
  so they follow the silhouette and any holes for free. Levels add passes
  at +45 degrees over progressively darker ranges, giving cross-hatching
  in the darkest areas. It needs lit materials; the white preview only
  swaps the compositor, so it can stay on.

### Fixed
- **The background was never detected in the depth pass.** EEVEE writes the
  camera's `clip_end` (measured 1000.07) for background pixels, not the
  1e10 that `BACKGROUND_Z` assumed, so every background test silently
  failed. It had been harmless - background read as "very far", so
  occlusion and the outline step test still gave the right answers - but
  hatching depends on it directly and covered the whole page. The depth
  pass now normalises background to infinity at read time. Verified no
  change to existing output: the 1,047,642-face model produces identical
  edges, paths, drawn length and travel.

## [2.8.2] - 2026-09-09
### Added
- **Japanese translations for the SVG UI.** The whole new panel was
  untranslated: `locale/ja_JP.po` had 129 entries covering the original
  UI and none of the ~50 SVG strings, so the add-on's primary panel
  rendered in English inside an otherwise Japanese interface. 50 entries
  added to `ja_JP.po`, and the same keys to `en_US.po` so the two files
  cover the same strings.

### Changed
- **English is now the primary README** (`README.md`); the Japanese one
  moved to `README.ja.md`.
- **The coexistence claim is now accurate.** The README said both add-ons
  could be enabled together and stopped there. Registration is genuinely
  separate, but the data they write is not: STEP3 builds the *scene's*
  compositor tree, and STEP2 injects the AOV group into every material.
  Both READMEs now say to run STEP2/STEP3 from one add-on per scene, and
  explain why the node group names are deliberately left shared - giving
  this fork its own would put a second AOV group in every material and
  write the AOVs twice.

## [2.8.1] - 2026-09-09
### Changed
- **The README now reads as a fork, not as a copy of the original's.** It
  had kept the upstream "GitHub vs note" section verbatim, which pointed
  users at the original author's **paid support** for problems with this
  fork, and at their note for past-version zips. That is misleading to
  users and unfair to the original author.

  Both READMEs now open by identifying this as an unofficial fork of
  megamarsun/FreePencil2, state plainly that the original's articles,
  manual and paid support do not cover this fork, and credit the original
  author alongside the fork maintainer.

## [2.8.0] - 2026-09-09
### Added
- **SVG export for pen plotters, as the add-on's primary output.** A new
  sidebar section (`SVG Export (pen plotter)`, shown first) writes the
  color-separation boundaries as vector paths instead of a rendered image.

  The rendered image is not traced. Lines come from the definition itself -
  an edge whose two adjacent faces differ in `mecha_color`, plus open
  boundaries and camera silhouettes. The compositor's Sobel draws a band
  with width, so tracing it makes a plotter go around every line twice as
  an outline; emitting the edge gives a single centreline.

  - Hidden-line removal compares each edge against the Z pass. Whether that
    pass holds plane distance or ray length is decided by measurement (face
    centres are projected and matched against the buffer), not assumed.
  - The depth render runs in a throwaway scene, so an existing compositor
    tree is left untouched.
  - `linemerge` / `linesort` equivalents are implemented in numpy. vpype
    itself is not bundled: it requires Shapely and scipy, both compiled
    wheels, which cannot be reconciled with shipping one package for
    4.2 through 5.2.

  Measured on a 1,047,642-face CAD model (A4 landscape, 1600 px): 26927
  chains -> 3286 paths after merging, pen-up travel 183948 mm -> 2997 mm,
  about 5 seconds for the export.

  Edge extraction reuses `mesh_islands.MeshTopology`, so the vector line set
  cannot drift from what the raster pipeline draws. The core lives in
  `svg_export.py` and the dev harness calls the same functions, following
  the same split as `fp_core.py`.

- **Selectable line sources for the SVG export.** The vector path used to
  read `mecha_color` only, while the raster path detects edges across
  several channels, so the two produced different drawings. Material
  boundaries and `bone_color` are now available as sources, and each of
  the five (color separation / material / bone / open edges / silhouette)
  can be switched off on its own.

  `bone` is off by default, unlike the raster path where `fpm_ch_bone` is
  1.0: bone boundaries add a lot of lines for a plotter.

  Depth discontinuity is deliberately not a source. In vector form a depth
  jump is the front object's silhouette edge, which is already emitted;
  the remainder would need screen-space tracing.

- **STEP4 paint is honoured by the SVG export.** `mask_color` (erase the
  line) and `line_color` (white makes it invisible) were both ignored, so
  a line erased in the viewport still appeared in the SVG. Both layers are
  brush-painted rather than per-face, so corner values are averaged per
  vertex and each edge is judged from its two endpoints, thresholded at
  0.5 because a plotter cannot draw a half-visible line.

- **Layered SVG output for multi-pen plots.** The export can be split into
  SVG layers by line source or by object, written with Inkscape's
  `inkscape:groupmode`/`inkscape:label` attributes so vpype and Inkscape
  read them as layers. Chaining, merging and draw-order sorting all stay
  inside a layer, since joining across layers would defeat the point.

  Splitting therefore costs paths and travel (measured: 3286 paths /
  2997 mm as one layer, 5210 / 6434 by source, 5627 / 7583 by object).
  The default stays a single layer.

  The `silhouette` layer is **not** the outer contour - it is every edge
  where adjacent faces flip between front- and back-facing, which on
  thin-plate CAD occurs throughout the interior. Use the outline layer
  below for that.

- **Outline layer from the depth buffer.** With layers by source, the edges
  that actually form the outline of the drawing go into their own `outline`
  layer. Each edge is probed to either side in the depth pass: if one side
  is background, or drops away by more than `fpm_svg_outline_gap` relative
  to the edge, it is an outline edge.

  This classifies existing edges rather than adding any, so the drawn
  geometry is unchanged. It is not a line source for that reason - a depth
  jump in vector form is already the front object's silhouette edge.

  The step threshold tunes how much is picked up. Measured on the CAD
  model: 1512 outline paths at 0.005, 1083 at 0.02 (default), 410 at 0.10,
  where it reduces to the machine's outer contour, its feet and the deep
  grille well.

- **Viewport preview.** `Refresh preview` computes the lines that would be
  exported and draws them in the 3D view through a `SpaceView3D` draw
  handler, so the parameters can be judged without opening the SVG in
  another application. It draws the 3D segments before projection, so no
  line logic is duplicated for it.

  Occlusion is computed for the render camera, so the preview is only
  truthful from camera view. It is refreshed on demand rather than live,
  since the depth pass and extraction take a few seconds on a large scene.
  The draw handler is removed from the add-on's `unregister`, because
  submodule `unregister` hooks are not called.

- **Fit the drawing to the page, and estimate the plot.** `Fit` chooses
  between the camera frame (previous behaviour) and the bounds of what was
  actually drawn. The latter keeps the margin constant and, more usefully,
  makes the merge tolerance honest: a drawing that is small on the page
  lets unrelated line ends fall inside the tolerance. Measured: 162x125 mm
  / 3286 paths by camera frame against 246x190 mm / 3802 paths by drawing
  bounds.

  The export now also reports drawn length, travel length and an estimated
  plot time from the pen-down speed, travel speed and per-lift cost, shown
  in the panel afterwards (12.8 to 17.1 minutes for the measured model).
  Acceleration is not modelled.

- **Drawing bounds is now the default fit**, so the margin is predictable
  and the merge tolerance stays honest against the pen width regardless of
  how the shot is framed.

- **One SVG per layer**, for sending each to a different pen. The page
  transform is computed once across all layers and shared by every file, so
  they line up when loaded separately - computing it per file would shift
  the layers apart.

- **Presets** (`Fine pen`, `Bold outline, 2 pens`, `Quick draft`) and a
  **camera batch** that writes `//svg_exports/NN_<camera>.svg` for the same
  camera ticks STEP5 uses. A camera that fails does not stop the others;
  the failures are reported.

- The SVG panel is split into three: the parent holds what is touched every
  time, with `Line sources` and `Advanced` as sub-panels. It had grown to
  more than twenty properties in one column.

### Fixed
- **The depth pass wrote nothing on Blender 5.x**, so the SVG export failed
  outright there with "no depth EXR written". On 5.x a File Output node
  carries an empty trailing socket after its named slots, and the link was
  being made to `inputs[-1]` - the placeholder - rather than to the slot.
  Nothing errored; the render simply produced no file. It now links by
  slot name, which is what `fp_core` already did.

  Found by installing 5.2.1 and running the suite; the export is now
  verified there.

- **STEP3 failed on Blender 5.x with the default node type.** `fpm_node_type`
  defaults to `test`, and the 4.x script for that group assigned to
  `CompositorNodeFilter.inputs[0]`. The sockets were reordered in 5.x
  (4.x: `Fac, Image`; 5.x: `Image, Factor, Type`), so that assignment hit
  the RGBA image socket and raised `TypeError` - and the image link, also
  written by index, would have gone to `Factor`.

  Fixed by adding `Exported_FreePencil_v1_1_0_test_5x.py`, built the way
  `utils_nodegroup` documents and `pro_5x` already was: create the group on
  4.5, open the file in 5.2 so Blender migrates it, export the result. The
  4.x script is untouched, so 4.x behaviour cannot change. Pre-existing
  since v2.5.0.

- **Separate registration namespace, so this can be enabled alongside the
  original add-on.** Changing the extension id alone was not enough: both
  add-ons still registered the same operator ids, panel ids and `fp_*`
  scene properties, so only whichever loaded last stayed live.

  - operators `freepencil*.` -> `fpm*.`
  - panels `FREEPENCIL_PT_*` -> `FPM_PT_*`, and the three panels that had
    no explicit `bl_idname` (so registered under their class name) renamed
  - the node/shader group export operators moved out of Blender's own
    `node.` / `shader.` namespaces, where they were identical to upstream
  - all 80 scene properties and `fp_cam_render`: `fp_*` -> `fpm_*`
  - sidebar tab is now "FreePencil SVG"

  Measured with both add-ons installed and enabled at once: 60 registered
  types (30 each) and 160 scene properties (80 each), with both panel,
  operator and property sets live. Before the change it was 30 and 80,
  with one add-on shadowing the other.

  **Settings do not carry over from the original add-on**, since the
  property names differ. Node group and vertex colour layer names are
  deliberately unchanged, so both read the same painted meshes.

### Notes
- The raster pipeline (STEP0-STEP5) is unchanged and remains fully
  available; only the panel order puts SVG export first.
- Twenty-one regression tests cover the SVG path (61 total, was 39),
  plus one that builds every node group type on the running Blender. All
  61 pass on both 4.5.6 and 5.2.1, and the two produce byte-identical
  vector output on a 1,047,642-face model (683165 edges, 6322 paths,
  43542.4 mm drawn, 11263.2 mm travel, same layer split). Included
  one that pins the pen starting outside the drawing - a case where
  `linesort` previously became a silent no-op.

## [2.7.0] - 2026-08-23
### Changed
- **Line sensitivity now defaults to 0.5 (was 1.0), and STEP0 sets it.**
  The paint separation was already correct, but many boundaries never
  reached the detection threshold, so no line appeared. Whether a line
  shows is decided by RGB distance (measured cut-off 0.05-0.14), and
  neighbouring colours that are close in luminance - pale blue against
  white, for instance - sat below it.

  Measured ink at 1920, no supersampling:

  | model | 1.0 | 0.5 |
  |---|---|---|
  | tank | 0.0855 | 0.0919 |
  | mech | 0.1048 | 0.1110 |
  | ship | 0.0482 | 0.0534 |
  | camera | 0.0894 | 0.1004 |

  Rigging that used to break into dashes is now continuous, and no noise
  was introduced; the dense mech stays clean even at 0.35, so 0.5 leaves
  headroom. **This changes the output of existing files** - raise the
  slider back to 1.0 in STEP3 to get the previous look.

## [2.6.2] - 2026-08-08
### Fixed
- **A file saved in 5.2 and opened in 4.5 can now be repaired by pressing
  STEP3.** 5.x keeps the scene compositor as a *node group*; open that
  .blend in 4.x and the group stays wired in as `scene.node_tree`, which
  4.x cannot drive. Measured on a Suzanne line-art file:

  | | ink |
  |---|---|
  | as built in 5.2 | 0.0065 |
  | opened in 4.5 | 0.9286 (near black) |
  | 4.5, after pressing STEP3 — **before** this fix | **1.0000 (fully black — worse)** |
  | 4.5, after pressing STEP3 — after this fix | **0.0067 (recovered)** |

  A 4.x scene tree is embedded data and never appears in
  `bpy.data.node_groups`, so a tree that *is* in there came from 5.x.
  `compat.discard_foreign_scene_tree` detects exactly that and swaps in a
  fresh embedded tree before STEP3 builds. The manual previously told
  people to delete the node groups by hand; that section is rewritten.

- **Node group version stamps now include the Blender generation.** The
  4.x and 5.x graphs are built from different exported files, but both
  stamped the same version number, so a group carried across generations
  looked current and was never rebuilt. The stamp is now `2-4x` / `2-5x`.

  Verified in both directions and same-version: 4.5 -> 5.2 gives 0.0067 ->
  0.0065, and 4.5 -> 4.5 is unchanged at 0.0067.

## [2.6.1] - 2026-08-08
### Removed
- **"This to Quads" is gone.** It rewrote the mesh permanently and paid the
  cost of an Edit-mode round trip for it, but barely touched the drawing.
  Measured over 8 models: **6 of them came out with the ink ratio unchanged
  to the last digit**, while the time went up anyway — 0.6s to 7.1s on an
  A320, 6.5s to 22.9s on an Audi. Face count only moves when the mesh
  happens to have triangles to merge; the Edit-mode entry is paid either
  way. On a 10.6M-face production set it cost 31 seconds and 13.8 GB, and
  quietly cut the mesh from 10,594,485 to 9,040,042 faces — every time
  STEP1 was pressed. (`dev/batch/HANDOFF.md` had already recorded the same
  finding on 5 models in an earlier round.)

  Old files that still have the setting saved are unaffected: the property
  no longer exists, so the value is simply never read. Default output is
  unchanged — 360/360 colour attributes identical before and after removal.

### Added
- **STEP0 / STEP2 / STEP3 now say what they did.** They only ever popped up
  on failure, so a successful run looked like nothing had happened. Each
  now reports whether the node group was created, updated, or already up to
  date, along with its name and node count, the AOVs that were enabled, and
  the file-output passes and destination.

### Fixed
- `dev/batch/hash_paint.py` unpacked `append_objects()` backwards —
  it returns `(meshes, others)` — and normalised the scene against the
  armatures instead of the meshes. The 348/348 comparison it produced is
  still valid (both sides ran through the identical harness), but the tool
  was wrong and is fixed.
- `mesh_islands.py` built `loop_poly` assuming loops are packed in face
  order while the face-centre code explicitly honoured `loop_start`. Only
  one of the two would have been right on a mesh that broke the assumption.
  The check now lives in one place and both paths use it.
- `mesh_islands.connected_components` returned a silently wrong labelling
  if it hit its 200-round runaway guard. It raises now.
- `scripts/install_all.py` read the add-on version from a module that was
  still the pre-install one, so a freshly installed 2.6.0 reported 2.5.0.
  It reloads first, cross-checks against `bl_info` in the installed file,
  and fails the run if the two disagree.

## [2.6.0] - 2026-08-08
### Added
- **Far crush relief (STEP3).** In deep sets — a shop floor, a street, a
  classroom — distant props pack so tightly on screen that their lines
  merge into solid black. Measured on a corridor of 26 receding shelf rows
  at 1200px, the share of pixels whose entire 3x3 neighbourhood is ink:

  | distance | off | **amount 0.6** | amount 1.0 |
  |---|---|---|---|
  | near | 0.0001 | 0.0000 | 0.0000 |
  | mid | 0.1178 | **0.0000** | 0.0000 |
  | far | **0.2387** | **0.0000** | 0.0000 |
  | ink left in the far band | 0.4378 | **0.1419** | 0.0033 |

  Fading by distance would erase the far geometry along with the mess, so
  the trigger is **local line density** instead: crushing *is* saturated
  density, and a distant silhouette that is not crowded survives. Five
  nodes are inserted just before the group's `line` output —
  `alpha *= 1 - amount * clamp((blur(mask) - threshold) / (1 - threshold))`.
  Amount defaults to 0, which inserts nothing and leaves existing images
  bit-identical. Moving the slider re-applies in place; back to 0 removes
  the nodes and restores the original wiring.

  Note 1.0 is too strong (0.3% of the far lines survive); start at 0.5-0.7.

  The nodes are located by following the wiring back from the `line`
  output, not by node name — the same exported file yields different
  auto-assigned names on 4.2 and 4.5, which broke a name-based first cut.

### Performance
- **STEP1 no longer redoes the same mesh once per linked duplicate.** A
  production set (a department store) had 1,186 mesh objects sharing only
  159 mesh datablocks: summed over objects that is 711M faces against
  89.2M of actual data — the same mesh was painted up to 14 times, and
  every pass but the last was thrown away. Worse, the colour seed comes
  from the *object* name, so which pass won depended on iteration order.
  STEP1 now paints one representative per mesh datablock, chosen by lowest
  object name so the result no longer depends on selection order.

- **Island detection moved from bmesh to numpy arrays** (new
  `mesh_islands.py`). Everything it needs — loop→edge, loop→face, face
  normals, areas, centres, sharp/seam/material flags — comes out of
  `foreach_get` in one call each, so no BMesh is built at all.

  | mesh | before | after |
  |---|---|---|
  | 3,189,380 faces | 55.8s | **32.1s** |
  | 10,594,485 faces | 201.3s | **112.6s** |

  Connected components use Shiloach-Vishkin. Hooking *roots* rather than
  nodes is what makes it viable: the node-hooking version needed 96 rounds
  and 5.66s where root-hooking with edge contraction converges in 3 rounds
  and 0.24s.

  The paint output is unchanged, verified by sha1 over every colour
  attribute of 8 models: **348/348 identical**. Reaching that required
  reproducing three accidents of the old code, each found by measurement:
  BMesh reports a zero normal for degenerate faces where the mesh API
  returns (0,0,1), and `calc_face_angle` then returns exactly 60° for them;
  triangle centres differ by 1 ULP because the mesh API divides by 3 while
  BMesh multiplies by 1/3, and that coordinate feeds the colour-jitter
  hash; and Blender sums n-gon (n>=5) vertices in reverse order.

- **Cheaper preparation.** `apply_face_colors` writes all corners with one
  numpy `foreach_set` instead of walking `polygon.loop_indices` (8.7s on a
  3.2M-face mesh). The dihedral angle of each edge is computed once and
  shared between the auto-threshold and the boundary test (it used to run
  7.94M times over 4.79M edges). `many_loose_parts` only asks whether the
  selection has 8 or more parts, which is already true when 8 or more
  objects are selected, so nothing is counted at all in a large scene.
  `_channel_painted` reads each mesh datablock once, smallest first.

### Fixed
- `fp_batch.lineart_metrics` loaded the render with a relative path, which
  Blender resolves against something other than the working directory, so
  a batch run with a relative `--out` failed after rendering. Third place
  this same trap has appeared; resolved at the source now.

- **mask_color did nothing useful on Blender 5.x.** Reported by a user: on
  5.2, painting the mask channel only erased lines around brightness 0.2,
  had almost no effect from 0.4 to 0.8, and white did nothing at all. On
  4.2 / 4.3 / 4.5 the same file erased every painted area regardless of
  brightness, which is the intended behaviour for a mask.

  The 5.x compositor node group is a separate exported file. That export
  ran with a `try/except` around each node's property block, and 5.x moved
  `color_hue` / `color_saturation` / `color_value` from node properties to
  input sockets. The exporter hit an `AttributeError`, wrote
  `# skipped node properties (...)`, and dropped the **entire** block —
  name, label, position and socket values — for four nodes:

  | node | lost | consequence |
  |---|---|---|
  | Color Key (mask chain) | key colour black -> white default, tolerances | mask inverted |
  | Color Key.001 (inpaint) | key tolerances | slightly different matte |
  | Inpaint.001 | name / label only | none (default matched) |
  | Dilate/Erode | name / label only | none (defaults matched) |

  The mask chain keys out **black**; leaving it at the white default made
  painted (bright) areas transparent instead of opaque, flipping the
  channel. Restored the 4.x values. All four Blender versions now produce
  identical results, pinned by `t36`.

  Also checked and cleared as false alarms: `Filter` (Sobel),
  `Dilate/Erode` and `Set Alpha` merely moved their settings from node
  properties to input sockets in 5.x, with matching values; and the
  `Normal` -> `Vector Math (dot product)` port is exact — the compositor
  Normal node computes `-dot(in, normalize(dir))`, so direction
  `(-1,-1,-1)` equals a dot with `(0.5774, 0.5774, 0.5774)` (measured).

### Changed
- **STEP4 channel labels now say what the channels do.** `mask_color` was
  labelled "White erases lines" since the 2023 original, which reads as
  white-specific; brightness is in fact irrelevant, so it is now "paint to
  erase lines". `line_color` was labelled just "Line Color" with no hint
  that it sets line *darkness* and never adds lines; it is now
  "Line Color(line darkness)". Manual section 5 rewritten to match.
- **File Output passes are now selectable, and default to line / color /
  light.** The third slot used to be the shadow pass, which on EEVEE rarely
  comes out clean enough to use; diffuse direct light composites far more
  easily. Shadow is still available as an opt-in checkbox.

  | pass | source | default |
  |---|---|---|
  | line | PRO group output | on |
  | color | PRO group output | on |
  | light | Diffuse Direct render pass | on |
  | shadow | Shadow render pass | off |

  The checkbox name is the written filename. Unchecking everything skips
  the File Output node entirely. Only the passes you select get enabled on
  the view layer. The Render Layers socket for diffuse direct is `DiffDir`
  on 4.x and `Diffuse Direct` on 5.x; `compat.render_layer_socket` resolves
  either. Existing files: rerun STEP3 to rewire.
- **Generated node trees are now laid out automatically.** Coordinates came
  straight from the exported .blend, where nothing had been arranged: the
  PRO group had 97 overlapping node pairs out of 80 nodes, and 47 of its 97
  links ran right-to-left. STEP3 now runs a layered pass (longest-path
  layering + iterated barycentre ordering) over every tree it builds.

  | tree | overlaps | backward links |
  |---|---|---|
  | scene root | 1 -> 0 | 3 -> 1 |
  | AOV group | 11 -> 0 | 0 -> 0 |
  | PRO group | 97 -> 0 | 47 -> 0 |

  Positions only; links, socket values and render output are untouched
  (mecha still renders ink 0.03765 / silhouette 0.32275 / 1487 components).

### Performance
- **STEP1 is much faster on multi-part models.** Profiling a 138-part /
  889k-face asset showed 93% of the time inside `bpy.ops` calls, almost
  all of it `object.mode_set`: the per-object loop entered and left Edit
  mode for every object, and each switch re-evaluates the whole scene
  depsgraph, so the cost grew with part count.

  The bmesh was only ever read (islands are derived from faces/edges; the
  colours are written afterwards through the data API), so Edit mode was
  never needed. It now uses `bmesh.new()` + `from_mesh()`. Edit mode is
  only entered when "This to Quads" is on, which genuinely rewrites the
  mesh.

  `ensure_vertex_color` likewise stopped using
  `geometry.color_attribute_add` — the data API takes the object directly,
  where the operator worked on whatever was active and forced a mode
  switch per attribute (four per object).

  | model | parts / faces | before | after |
  |---|---|---|---|
  | mecha | 155 / 50k | 8.7 s | **1.32 s** |
  | tank | 43 / 421k | 19.4 s | **7.84 s** |
  | carriage | 138 / 889k | 191 s | **~15 s** |
  | C62 | 1 / 1279k | 28 s | 27.1 s |

  Output is bit-identical on the mecha (ink 0.03765, silhouette 0.32275,
  1487 components — same as the shipped v2.5.0).

### Added
- Blender 4.2 LTS and 4.3 are now covered by the test matrix. The full
  smoke suite (33 tests) runs on 4.2 / 4.3 / 4.5 / 5.2, and rendered line
  output stays within 1.1% ink across all four.
- `compat.HAS_AOV_IN_VIEWPORT_COMPOSITOR` marks whether the viewport
  compositor evaluates AOV outputs (4.3+).
- Support tier table in README and the manual.

### Fixed
- **Blank white viewport on Blender 4.2.** STEP2 and STEP3 both switched
  the viewport to Rendered mode unconditionally. Blender 4.2's viewport
  compositor does not evaluate AOV outputs, so the preview showed nothing
  but white. Measured by isolating the graph: a plain `Invert` and a node
  group both render fine in 4.2, only the AOV input comes through empty —
  so there is no way around it from the add-on side. On 4.2 the viewport
  is now left alone, the preview toggle is disabled with an explanation,
  and the panel says to render with F12.
- Minimum Blender version disagreed between `bl_info` (4.3.0) and
  `blender_manifest.toml` (4.2.0). Both are 4.2.0 now, and a test pins
  version and minimum-version agreement between the two files.

## [2.5.0] - 2026-07-26
### Added
- Blender 5.2 support. The same package now works on both 4.5 and 5.2.
  Version differences are absorbed in `compat.py`, and a 5.x-native
  compositor node script is shipped alongside the 4.x one.
- Progress bar for STEP1 / STEP0. Long vertex color passes no longer
  freeze Blender; the operator runs modally and can be cancelled with ESC.
- STEP0 now turns on the white material preview, so line art is visible
  right after the one-button setup.

### Changed
- Island boundaries are driven by sharp edges instead of Freestyle marks.
  The previous approach temporarily overwrote the user's sharp edges with
  the Freestyle marks and restored them afterwards, which was destructive
  and prone to leaving the mesh in a modified state. STEP1 no longer
  modifies the mesh at all.
  Note: Blender 5.0 removed the `use_freestyle_mark` Python property on
  mesh elements in favour of the attribute API; Freestyle itself and its
  edge marks are still available.
- STEP3 no longer opens a separate compositor window.
- The sidebar starts with only STEP0 expanded.

### Fixed
- Node groups are now regenerated when the shipped graph changes. Files
  containing an older group were previously stuck with it forever.
- Vertex color export honours `render_color_index`, so glTF exports carry
  the painted colors.
- Node export (`node_io`) produced scripts that failed to run when the
  tree contained an Anti-Aliasing node.
- Distribution zip no longer bundles development files.

## [2.4.0] - 2026-07-23
### Added
- Per-channel line strength sliders (STEP3): depth / mecha / bone /
  material / generate, live-updating the generated node group from the
  sidebar. 0 fully disables a channel (ramp colors whitened, restorable).
- White material preview toggle (STEP3): a compositor Mix switches the
  PRO group's Image input between the beauty pass and white, giving an
  instant pure-line-art preview without touching any material.
- 2x supersampling option (STEP3, and STEP0 default ON): render at 200%
  and scale the Composite / File Output results back to 50% for crisp
  1px lines.
- STEP0 per-item toggles, including automatic AOV configuration from the
  scene (painted-channel detection by value, material-ID linkage).
### Changed
- Depth channel reworked to a relative depth gradient
  (Sobel(Z) / (Z + 0.5)) instead of frame min-max normalization, which
  the far clip dominated; interior depth steps now produce lines and the
  depth slider is effective. Regenerate STEP3 nodes in existing files.
### Fixed
- Channel strength 0 previously still drew strong edges (gradients
  exceed the ramp's 1.0 position cap).

## [2.3.0] - 2026-07-18
### Added
- STEP0 "Full Auto": one button that analyzes the scene (rig detection,
  material blend modes), applies recommended settings and runs STEP1-3.
- Part tint (mecha color): touching objects get different brightness bands
  so part boundaries (hairline, collar, assembly seams) become lines.
- Hard boundary bones (`fpm_bone_hard_names`): comma-separated bone names
  whose weight region is painted with the dominant color only, producing a
  line at the boundary (e.g. "head,neck" for a jaw line).
- Auto edge angle (STEP1): per-object sharp-edge threshold from the
  dihedral-angle distribution, with guards for rigged/multi-part models.
- Seam/material boundaries and minimum island area merge options for STEP1.
- Line sensitivity (STEP3): scales the node group's line-detection ramps.
- File Output in STEP3: optional node writing line / color / Shadow passes
  as PNGs (default `//render/`).
- STEP5 "Camera Batch Render": per-camera checkboxes and one button that
  renders every checked camera into `//camera_renders/NN_<camera>/`.
### Changed
- Sidebar UI reorganized into collapsible sub-panels (STEP0-STEP5) with
  fixed ordering; full Japanese/English translations for all new strings.
- Depth channel defaults strengthened (threshold 0.22 -> 0.15, darker line
  color) for clearer silhouette and step lines.
- STEP2/STEP3 core logic extracted to `fp_core.py`; operators are
  headless-safe (no UI popups in background mode).
### Fixed
- Crash when running STEP1 headless (popup menu in background mode).
- STEP1 failure on selected meshes with zero faces.

## [2.2.0] - 2026-07-10
### Added
- Reproducible color seed for Auto Vertex Color (STEP1): a "Random seed each run"
  toggle, a Seed field, and a randomize button. Turning the toggle off reproduces
  exactly the same island colors for a given seed and mesh.
### Changed
- Island color generation now uses a process-independent hash of the object name,
  so colors are reproducible across Blender sessions (previously the built-in
  `hash()` was salted per process).

## [2.1.2] - 2025-08-15
### Fixed
- Automatically enable Z-depth pass for compositing when generating Pro node

## [2.1.1] - 2025-08-01
### Changed
- Translation dictionary moved to `locale/*.po` files and loaded at runtime

## [2.1.0] - 2025-06-27
### Added
- Color-noise scale, min neighbor color distance, max color retries の 3 プロパティを追加
- メインパネルに UI スライダーを配置

## [2.0.0] - 2025-04-04

### Added
- Dynamic translation support for EnumProperty items using `items=callback() + pgettext()`
- Full Japanese translation coverage for Blender 4.3.2+
- `description()` method for operator tooltips

### Changed
- Panel structure restored to match main branch (UI clarity improved)
- Translation registration moved to be first in register() function
- Debug translation utilities removed to prevent interference

### Fixed
- Enum dropdown labels not being translated
- Tooltips and buttons displaying incorrect language under Japanese UI
