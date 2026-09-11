# FreePencil2 - SVG Mod: User Manual

This is the reference manual for the add-on: how to install it, how the
workflow is laid out, and what every button and setting in the sidebar does.
For the background, measurements and design reasoning, see [README.md](README.md);
for what changed between versions, see [CHANGELOG.md](CHANGELOG.md).

Version documented: **2.13.0**. Blender **4.2 - 5.2** (see
[Supported Blender versions](#supported-blender-versions)).

---

## Contents

1. [What the add-on does](#what-the-add-on-does)
2. [Installation](#installation)
3. [Key concepts](#key-concepts)
4. [Quick start: model to plotter SVG](#quick-start-model-to-plotter-svg)
5. [The sidebar, panel by panel](#the-sidebar-panel-by-panel)
   - [Header and status](#header-and-status)
   - [SVG export (pen plotter) - main output](#svg-export-pen-plotter---main-output)
   - [Line sources](#line-sources)
   - [Advanced](#advanced)
   - [STEP0: Full Auto](#step0-full-auto---start-here)
   - [STEP1: Auto Vertex Color](#step1-auto-vertex-color)
   - [STEP2: AOV (raster only)](#step2-aov-raster-only)
   - [STEP3: Compositor (raster only)](#step3-compositor-raster-only)
   - [STEP4: Manual Vertex Color](#step4-manual-vertex-color)
   - [STEP5: Cameras (batch export)](#step5-cameras-batch-export)
   - [Reset](#reset)
   - [Compositor editor panel](#compositor-editor-panel)
6. [Presets](#presets)
7. [Output files](#output-files)
8. [Recipes](#recipes)
9. [Scripting](#scripting)
10. [Troubleshooting](#troubleshooting)
11. [Supported Blender versions](#supported-blender-versions)
12. [Appendix: complete property reference](#appendix-complete-property-reference)

---

## What the add-on does

FreePencil2 turns a 3D model into line art. It does this in two stages:

1. **Colour separation.** The mesh is split into "islands" (regions bounded
   by sharp edges, seams or material changes) and every island is painted a
   flat colour into a vertex-colour layer called `mecha_color`, such that
   neighbouring islands always get clearly different colours.
2. **Line extraction.** Every place where the colour changes is a line.

From that painted mesh you can get lines out in two ways:

| Output | Mechanism | Needs |
|---|---|---|
| **SVG** (the point of this fork) | Reads the vertex colours off the mesh, emits the boundary *edges* as vector paths, removes hidden lines against a depth render | STEP0 or STEP1 only |
| **Raster image** (F12 render, the original FreePencil2 pipeline) | AOV passes + a compositor node group draw the colour boundaries as pixels | STEP2 + STEP3 (or STEP0 with the raster option ticked) |

The SVG path never touches the compositor. That is why the panel is arranged
with the SVG export first and the raster steps (STEP2/STEP3) marked
"raster only".

The SVG output is designed for **pen plotters**: millimetre units, no fills,
one centreline per edge (never a traced outline of a raster line), constant
stroke width per layer, joined line ends, and a draw order optimised to
minimise pen-up travel.

---

## Installation

### From a built zip

1. Build the extension zip (no zip is hosted in the repository):

   ```bash
   blender --command extension build --source-dir . --output-dir dist
   ```

2. In Blender: **Edit > Preferences > Add-ons > (dropdown ▼) > Install from
   Disk**, pick `dist/freepencil2_svg_mod-<version>.zip`, and enable it.

### Where it lives in the UI

- **3D Viewport > Sidebar (N) > tab "FreePencil SVG"** - the whole workflow.
- **Compositor node editor > Sidebar (N) > tab "FreePencil SVG"** - one
  checkbox (anti-aliasing node) for the raster path.

### Running alongside the original FreePencil2

Both add-ons can be installed and enabled at the same time; the extension id
(`freepencil2_svg_mod`), operator ids (`fpm*.`), panel ids (`FPM_PT_*`) and
scene properties (`fpm_*`) are all distinct. The vertex-colour layers are
shared (both read the same painted mesh), and so are the AOV/compositor node
group names - so **run STEP2/STEP3 from only one of the two add-ons in a
given scene**, otherwise they overwrite each other's compositor tree.
Settings do not carry over between the two.

---

## Key concepts

### The vertex-colour layers

STEP1 creates these colour attributes on every processed mesh (face-corner
domain):

| Layer | Written by | Meaning |
|---|---|---|
| `mecha_color` | STEP1, STEP4 (edit) | The colour separation. Every island is a flat colour; a line appears where two adjacent faces differ. **This is what the SVG export reads.** |
| `bone_color` | STEP1 (when a rig is detected) | One colour per bone / vertex group, soft-blended by weights. Optional line source. |
| `mask_color` | You, in STEP4 | Paint anything non-black here to **erase** lines. Brightness is irrelevant. |
| `line_color` | You, in STEP4 | Line darkness for the raster path. **White makes a line invisible** - the SVG export drops it too. |

### Islands

An island is a connected patch of faces that is not crossed by any island
boundary. Boundaries come from:

- edges whose dihedral angle is above the **edge angle** (set by hand, or
  chosen automatically from the mesh's angle distribution),
- UV seams and material changes (if **Seam/material boundaries** is on),
- edges you marked **Sharp** in Edit mode.

Tiny islands can be merged into their largest neighbour (**Min island
area %**) so speckle does not become a mess of short lines.

### The STEP numbering

| Step | Purpose | Needed for SVG? |
|---|---|---|
| STEP0 | One-button setup: picks recommended settings and runs STEP1 (and optionally STEP2+3) | Yes (or STEP1) |
| STEP1 | Colour separation (paint `mecha_color` etc.) | Yes |
| STEP2 | Inject AOV outputs into every material, add AOV slots to the view layer | No - raster only |
| STEP3 | Build the compositor node tree that renders the lines | No - raster only |
| STEP4 | Manual touch-up of the vertex colours | Optional |
| STEP5 | Tick which cameras the batch exports use | Optional |

### Hidden-line removal

The SVG export renders a **Z (depth) pass** of the scene from the active
camera into a throw-away temporary scene (your compositor is untouched), then
samples points along each candidate edge and keeps only the spans that are in
front of the depth buffer. Where a line goes behind something, the cut point
is refined by bisection to 1/256 of the edge. Because it is computed for the
**render camera**, the viewport preview is only truthful when you look
through the camera.

---

## Quick start: model to plotter SVG

1. Put a **camera** in the scene and frame the model (Numpad 0). The page
   orientation and the depth pass follow the camera and render resolution.
2. Open the sidebar (N) > **FreePencil SVG**. The status box at the top tells
   you what is missing.
3. In **STEP0: Full Auto**, leave *Also set up the raster render* **unticked**
   and press **Auto setup (paint for SVG)**. A progress bar runs; a message box
   says "Painted. Ready to export SVG." when it is done. (Select specific
   meshes first to paint only those; with nothing selected every render-visible
   mesh is painted.)
4. In **SVG export (pen plotter)**, press **Refresh preview** and look through
   the camera. Lines are drawn in the 3D view exactly as they would be
   exported.
5. Choose a **Page**, **Margin** and **Pen width** to match your paper and pen.
6. Press **Export SVG**, pick a file name. The panel then shows the path
   count, drawn length, pen-up travel and a time estimate.
7. Optional: post-process with vpype (`vpype read out.svg reloop linesort write plot.svg`)
   or load into your plotter software directly.

---

## The sidebar, panel by panel

All panels live under one parent, **FreePencil v2.13.0**, in the
**FreePencil SVG** tab. Sub-panels are collapsible; only *SVG export* and
*STEP0* are open by default.

### Header and status

The top box shows what the SVG export needs, with a check or warning icon:

- **Camera** - ready / none in scene
- **Colour separation** - painted / not yet (looks for a `mecha_color` layer
  on any mesh)
- **Preview** - current / out of date (only while the preview is showing)

Under it is the *next thing to press*: "Add a camera to the scene", an
**Auto setup (paint for SVG)** button, or "Ready to export SVG".

On Blender 4.2 an extra note says live (raster) preview is unavailable - this
does not affect the SVG export.

---

### SVG export (pen plotter) - main output

Everything you touch on a normal export. The finer settings are in the
[Line sources](#line-sources) and [Advanced](#advanced) sub-panels.

If the scene is not ready, the panel says so and offers the auto-setup button
instead of the export controls.

#### Preset (menu)

Applies a ready-made combination of settings. See [Presets](#presets).

#### Refresh preview / Auto refresh

| Control | What it does |
|---|---|
| **Refresh preview** | Computes the lines that would be exported with the current settings and draws them in the 3D viewport (dark lines). Nothing is written to disk. |
| **X** (next to it) | Removes the preview lines from the viewport. |
| **Auto refresh** (default: on) | Recomputes the preview on its own once the camera, meshes or export settings have held still for ~0.75 s. Turn it off on scenes where the preview takes long enough to be annoying, and press the button instead. |
| Info line | After a refresh: a summary such as edge/path counts. If the camera or a mesh has moved since, it says **Preview is out of date** and the stale lines are drawn in light grey (their hidden-line removal no longer matches). |

The preview reacts to the camera, mesh transforms and the settings that
change which lines exist. It does **not** watch vertex-paint edits or mesh
edits - press Refresh after those.

#### Page

| Setting | Default | Range | What it does |
|---|---|---|---|
| **Page** | A4 | A5 / A4 / A3 / Letter | Paper size. **Orientation follows the render aspect ratio** (landscape render = landscape page). |
| **Fit** | Camera frame | Camera frame / Drawing bounds | *Camera frame*: the camera's frame is fitted to the page, so the composition you see in the viewport is what lands on paper (a small subject stays small). *Drawing bounds*: the extent of the lines actually drawn is fitted to the page, so the margin is constant whatever the framing - use this to fill the sheet. |
| **Margin (mm)** | 10 | 0 - 100 | Blank border on every side. With tiling it applies to the composite, not to each sheet. |
| **Pen width (mm)** | 0.3 | 0.01 - 5 | Stroke width written to the SVG. Match your pen; it also sets the sensible scale for *Merge* and *Simplify*. |

#### Layers

| Setting | Default | Options / range | What it does |
|---|---|---|---|
| **Layers** | Single layer | Single layer / By line source / By object / By depth | How the output is split into SVG layers (Inkscape/vpype layers), so each can be sent to a different pen. See [Output files](#output-files) for the layer names. |
| **One file per layer** | off | - | With layers on, write one SVG per layer (`<name>_<layer>.svg`). All files share a single page transform, so they line up when loaded separately. |
| **Colour layers** | on | - | Give each layer its own stroke colour so the pens are told apart on screen. Plotters ignore colour. A single layer is always black. |
| **Depth bands** | 3 | 2 - 5 | *By depth* only: how many near-to-far bands (`depth1` is nearest). The band range is taken from the visible lines only. |
| **Far line weight** | 0.6 | 0.1 - 1.0 | *By depth* only: stroke width of the farthest band as a fraction of the pen width; the bands in between are interpolated. Lower = stronger depth cue. |

Layer modes in detail:

- **Single layer** - one `<g>` with everything (plus a separate `hatch` group
  if hatching is on). Fastest to plot: merging and draw-order sorting work
  across all lines.
- **By line source** - one layer per source: `outline`, `silhouette`,
  `freestyle`, `crease`, `sharp`, `mecha`, `material`, `bone`, `open`. An
  edge that qualifies for several sources goes to the first in that order.
  The `outline` layer is controlled from *Advanced*.
- **By object** - one layer per mesh object, named after the object.
- **By depth** - `depth1` … `depthN` bands, nearest first.

Splitting increases path count and pen travel, since merging and sorting stay
inside a layer. If you do not need separate pens, use a single layer.

#### Hatching

Adds tone as its own `hatch` layer, from the render's **diffuse direct light**
pass. Parallel lines are drawn across the page and cut against the light
pass, so they follow the silhouette and any holes automatically. **It needs
lit materials** - the shading comes from the actual render (the white preview
does not interfere, because it only swaps the compositor).

| Setting | Default | Range | What it does |
|---|---|---|---|
| **Hatching** | off | - | Enable the hatch layer. |
| **Hatch spacing (mm)** | 1.2 | 0.1 - 20 | Distance between hatch lines on paper. |
| **Hatch levels** | 2 | 1 - 3 | Tone steps. Each level covers a darker range of the light pass at +45° to the previous, so the darkest regions become cross-hatched. |
| **Hatch angle** | 45 | 0 - 180 | Angle (degrees) of the first level. |
| **Hatch threshold** | 0.5 | 0 - 1 | Hatch where diffuse light is below this. Higher = more of the model gets toned. |
| **Hatch pen width (mm)** | 0 | 0 - 5 | Stroke width of the hatch layer. 0 = same as the main pen width. |

#### Tiling

Plot a drawing larger than the machine bed by splitting it across sheets.

| Setting | Default | Range | What it does |
|---|---|---|---|
| **Columns** / **Rows** | 1 / 1 | 1 - 6 each | Number of sheets horizontally / vertically. 1×1 = no tiling. The drawing is fitted to the *composite* (columns × page wide, rows × page tall), then cut; each sheet is written at page size as `<name>_r1c1.svg`, `<name>_r1c2.svg`, … |
| **Registration marks** | on | - | Corner marks (6 mm L-shapes, 3 mm inset) on every sheet, in a `regmarks` layer, for lining the sheets up. |

Merging, simplification, jitter and sorting are done **once on the composite**
before cutting, so lines agree exactly across seams. The margin applies to the
composite, so content runs right up to inner seams - that is what makes the
join continuous.

#### Hand jitter

Wobbles the lines so CAD output reads as drawn rather than machined.

| Setting | Default | Range | What it does |
|---|---|---|---|
| **Jitter (mm)** | 0 | 0 - 5 | Amplitude of the wobble. 0 = off. |
| **Jitter scale (mm)** | 8 | 0.5 - 100 | Wavelength. Small = shaky, large = long lazy curves. |
| **Jitter seed** | 1 | 0 - 9999 | Change for a different wobble. |

The offset is a function of *position*, not a random value per point, so two
lines that share an end still share it after wobbling (no gaps at junctions).
Straight runs are densified first so they have something to bend. A 0.5 mm
wobble lengthens the drawing by under 1%.

#### Export buttons

| Button | What it does |
|---|---|
| **Export SVG** | Opens a file browser (default `<blend name>.svg` next to the .blend, or `freepencil.svg`). The file browser's options panel has **Selected only** to export just the selected meshes. Otherwise every mesh in the view layer that is *not disabled for rendering* is exported. |
| **Export checked cameras** | Writes `//svg_exports/NN_<camera>.svg` for every camera ticked in [STEP5](#step5-cameras-batch-export). Requires a saved .blend. Runs in the background of the UI with a status-bar counter; **Esc cancels** after the current file. If one camera fails, the rest are still written and the failure is reported. The active camera is restored afterwards. |
| **Export frame range** | Writes `//svg_exports/frame_####.svg` for every frame of the scene's frame range and step, re-evaluating meshes each frame (deforming rigs export correctly). Same background/Esc behaviour. The current frame is restored afterwards. |
| *Cameras ticked: n/m* | Reminder of how many cameras are ticked in STEP5. |

After an export the panel shows the **last result**: paths / points, drawn mm
/ travel mm, and an estimated plot time at the configured pen-down speed.

---

### Line sources

Sub-panel of *SVG export*. Which edges become lines. Every source can be
switched on or off independently.

| Source | Default | What it is |
|---|---|---|
| **Color separation** | on | Edges where the adjacent faces' `mecha_color` differs. The colour-separation lines themselves. |
| **Material boundaries** | on | Edges where the material index changes. |
| **Bone boundaries** | **off** | Edges where `bone_color` differs. Off because it adds a lot of lines on a plotter; enable it for characters when you want bone-region lines. |
| **Open edges** | on | Edges that do not have exactly two faces (open borders, non-manifold). Imported CAD with unwelded shells has many of these - turning it off can cut the line count a lot. |
| **Silhouette** | on | Edges where the surface turns from front- to back-facing relative to the camera. **Not** the outer contour: on thin-plate models this fires all over the interior. For a heavy outer contour use the *outline layer* instead. |
| **Honour STEP4 paint** | on | Drop lines erased with `mask_color` or made invisible (white) with `line_color`. |

**Marked by hand** - add lines without touching the vertex colours:

| Source | Default | What it is |
|---|---|---|
| **Freestyle marks** | off | Edges marked *Edge > Mark Freestyle Edge* in Edit mode (read from the `freestyle_edge` attribute). |
| **Sharp marks** | off | Edges marked *Edge > Mark Sharp*. |
| **Crease angle** | off | Edges whose dihedral angle is at or above **Angle (deg)** (default 60, 0 - 180). Folds without going through the colour separation. |

---

### Advanced

Sub-panel of *SVG export*. Settings you decide once and rarely revisit.

#### Paths

| Setting | Default | Range | What it does |
|---|---|---|---|
| **Merge (mm)** | 0.1 | 0 - 5 | Join line ends closer than this into one path. **Set it from the pen width, not from how small the drawing is** - if the drawing is small on the page, unrelated ends fall inside the tolerance. |
| **Simplify (mm)** | 0.05 | 0 - 5 | Ramer-Douglas-Peucker: drop points that move the line by less than this. |
| **Sort draw order** | on | - | Reorder paths to shorten pen-up travel: greedy nearest-neighbour, then a 2-opt pass that removes long jumps (measured: about −25% travel). Sorting stays within a layer. |
| **Pen home** | Top left | Top left / Top right / Bottom left / Bottom right | Corner where the plotter parks the pen; the draw order starts from the nearest line to it. Top left suits AxiDraw and Inkscape-driven plotters; bottom left suits most G-code plotters. |

#### Outline layer

Only active with **Layers = By line source**.

| Setting | Default | Range | What it does |
|---|---|---|---|
| **Outline layer** | on | - | Put edges that actually form the outline of the drawing into an `outline` layer. Decided from the depth buffer: an edge qualifies if one side is background or drops away sharply behind the edge. Unlike *Silhouette*, thin plates do not flood it. |
| **Outline depth step** | 0.02 | 0 - 1 | How much deeper (relative to the edge's depth) the far side must be to count as an outline. Raise it to keep only larger steps: measured 1512 paths at 0.005, 1083 at 0.02, 410 at 0.10 (essentially the outer contour). |
| **Outline pen width (mm)** | 0 | 0 - 5 | Stroke width of the outline layer; 0 = main pen width. |

#### Hidden line removal

| Setting | Default | Range | What it does |
|---|---|---|---|
| **Depth resolution** | 1600 | 256 - 8192 | Width in pixels of the depth pass (height follows the render aspect). Line accuracy and the export time both follow this. 800 is fine for drafts. |
| **Samples per edge** | 8 | 1 - 64 | Visibility test points along each edge. Cuts are refined between samples by bisection, so 8 already gives 1/256-edge precision. |
| **Depth bias** | 0.001 | 0 - 0.1 | Relative tolerance when comparing an edge point against the depth buffer. Lines lie on the surface, so some bias is required; raise it if surface lines flicker away, lower it if lines show through thin parts. |
| **Neighbourhood** | 0 | 0 - 4 | Radius in pixels for the depth lookup. Above 0 the *maximum* depth in the neighbourhood is used, which lets the interior of detailed models show through outer panels. |
| **Keep hidden lines** | off | - | Skip hidden-line removal entirely (diagnosis / wireframe look). |

The add-on decides on its own whether the version's Z pass stores planar or
ray distance by testing face centres against the buffer, so you never need to
set that.

#### Plot estimate

Used only for the time shown after an export (no acceleration modelling -
treat it as a guide).

| Setting | Default | Range |
|---|---|---|
| **Pen down mm/s** | 80 | 1 - 2000 |
| **Travel mm/s** | 200 | 1 - 2000 |
| **Pen lift (s)** | 0.12 | 0 - 5 |

---

### STEP0: Full Auto - start here

Analyses the scene, applies recommended settings and runs STEP1 - and, if
asked, STEP2 and STEP3. Undoable. Press **Esc** during the progress bar to
cancel.

**Target meshes:** the selected meshes; if none are selected, every mesh that
is not disabled for rendering (they get selected for you).

Each tick decides whether Full Auto overrides the corresponding STEP1/2/3
setting:

| Tick | Default | Effect when on |
|---|---|---|
| **Auto edge angle** | on | Sets STEP1 *Auto edge angle* on (per-object angle from the dihedral distribution). |
| **Seam/material boundaries** | on | Sets STEP1 *Seam/material boundaries* on. |
| **Merge small islands** | on | Sets STEP1 *Min island area %* to 0.02. |
| **Part tint** | on | Sets STEP1 *Part tint* on (touching objects get different brightness bands so part boundaries become lines). |
| **Bone colours by rig detection** | on | Enables the bone AOV / `bone_color` when an Armature modifier is found on a target. |
| **BLEND to HASHED (keep glass)** | on | Materials with blend mode BLEND are switched to HASHED (EEVEE writes no AOVs for BLEND). Real glass (Transmission / low alpha) is left alone. |

Always applied regardless of ticks: bone grouping = *Basename*, line
sensitivity = 0.5, node type = *Pro*.

**Also set up the raster render** (default: **off**) - additionally runs
STEP2 and STEP3 so you can render the line art with F12. The SVG export does
not need any of this; leaving it off keeps the viewport and materials as they
are. When on, these sub-options apply:

| Tick | Default | Effect |
|---|---|---|
| **Anti-aliasing** | on | Include the anti-aliasing node in the compositor tree. |
| **2x supersampling (thin lines)** | on | Render at 200% and scale the compositor output to 50% (near-essential for thin lines). |
| **Auto AOVs from scene** | on | Full Auto owns the STEP2 AOV toggles: gen/mask/line follow whether those vertex colours are actually painted, mat follows *Add the material ID*. |
| **White material preview** | on | Turn on the white preview after setup so the line art is visible immediately. |
| **Enable File Output** | off | Also enable the STEP3 File Output node. |

The button is labelled **Auto setup (paint for SVG)** or **Auto setup (paint +
raster)** depending on the raster tick. When it finishes, a message box
reports the mesh count, whether raster/bone AOV were set up, and how many
materials were converted to HASHED.

---

### STEP1: Auto Vertex Color

The colour separation itself. Operates on the **selected** mesh objects
(linked duplicates sharing a mesh are painted once). Undoable, cancellable
with Esc.

| Setting | Default | Range | What it does |
|---|---|---|---|
| **Add the material ID** | off | - | Also assign each material a pass index so material boundaries can be a raster channel (STEP2 *material color* AOV). |

**Island split**

| Setting | Default | Range | What it does |
|---|---|---|---|
| **Auto edge angle** | off | - | Choose the sharp-edge angle automatically per object from the mesh's dihedral-angle distribution. Disables the manual slider. |
| **Edge Angle** | 30 | 0 - 180 | Dihedral angle (degrees) above which an edge is an island boundary. |
| **Seam/material boundaries** | off | - | Treat UV seams and material changes as island boundaries too. |
| **Min island area %** | 0 | 0 - 5 | Merge islands smaller than this percentage of the mesh's total area into their largest neighbour. 0 = off. STEP0 uses 0.02. |

Edges you have marked **Sharp** in Edit mode are always respected as
boundaries.

**Bone options** (only matter when a rig is present)

| Setting | Default | Options | What it does |
|---|---|---|---|
| **Bone grouping** | Basename (.###) | Exact / Basename (.###) / Strip trailing digits | How bone / vertex-group names are grouped into one colour: exactly as named; treating `.001`-style suffixes as the same bone; or ignoring trailing digits even without a dot. |
| **Hard boundary bones** | (empty) | comma-separated names | Bones whose region boundary should be a hard step in `bone_color` (so a line appears there, e.g. `head` for a chin line). Empty = all boundaries soft-blended. |
| **Part tint (mecha color)** | on | - | Give touching parts (separate objects such as hair and face) different brightness bands in `mecha_color` so their boundaries become lines; `bone_color` stays a pure weight blend. |

**Color seed** (reproducibility)

| Setting | Default | What it does |
|---|---|---|
| **Random seed each run** | on | Generate a new seed every run. The seed actually used is written back to *Seed* so you can fix it later. |
| **Seed** | 0 | With *Random seed* off: the seed to use. Same seed + same mesh = same colours. |
| **↻** | - | Pick a new random seed. |

**Separate Paint Vertex Colors** - runs STEP1 on the selected meshes.

---

### STEP2: AOV (raster only)

Injects the FreePencil AOV node group into every node-based material and adds
the matching AOV slots to the view layer. Requires a selected mesh that
already has `mecha_color`. **Not used by the SVG export.**

| Tick | Default | AOV written |
|---|---|---|
| **AOV Bone Color** | off | `bone_color` |
| **AOV Generate Color** | off | `gen_color` |
| **AOV Mask Color (paint to erase lines)** | off | `mask_color` |
| **AOV Line Color (line darkness)** | off | `line_color` |
| **AOV Material Boundary Color** | off | material boundaries (needs *Add the material ID* in STEP1) |

`mecha_color` is always written. When STEP0's *Auto AOVs from scene* is on,
these toggles are overridden by what is actually painted.

**Set up AOVs** - runs it. On Blender 4.3+ the viewport is switched to
Rendered with the `mecha_color` pass shown so you can check the separation.
The viewport state before the switch is recorded so *Reset* can restore it.

---

### STEP3: Compositor (raster only)

Builds the compositor node tree that turns the AOVs into rendered line art.
Requires STEP2. **Not used by the SVG export.**

| Setting | Default | What it does |
|---|---|---|
| **Node type** | Test | *Test Node* or *Pro Node* - which generated node group to build. STEP0 sets *Pro*. |
| **Enable Compositor Preview** | on | After building, switch the 3D viewport to Rendered with the viewport compositor always on. Disabled on 4.2 (the 4.2 viewport compositor does not evaluate AOVs). |
| **White material preview** (toggle) | off | Show pure line art on white **without touching materials**: the compositor's beauty input is mixed to white. Turn off to see the original materials again. |
| **Include Anti-Aliasing Node** | off | Insert an anti-aliasing node before Composite. |
| **2x supersampling (thin lines)** | off | Render at 200% and scale the composite (and File Output) back to 50%, turning 2 px lines into crisp 1 px. |
| **Line sensitivity** | 0.5 | 0.05 - 2.0. Scales the line-detection thresholds inside the node group. Lower = weaker colour differences also become solid lines. 1.0 is the raw node value; 0.5 is the tuned default. Updates live. |

**Far crush relief** - thins lines where they have merged into solid black
(typically the far background of a large set). Updates live.

| Setting | Default | Range | What it does |
|---|---|---|---|
| **Amount** | 0 | 0 - 1 | 0 = off (image unchanged). |
| **Radius (px)** | 6 | 1 - 32 | How far to look when measuring line crowding. Larger = only wide black areas are thinned. |
| **Threshold** | 0.35 | 0.05 - 0.95 | How crowded an area must be before thinning starts. Lower = acts on sparser lines. |

**Line strength per channel** - each 0 - 2, default 1.0, live: **Depth**,
**Mecha**, **Bone**, **Material**, **Generate**. 1.0 = current, higher = more
/ stronger lines, 0 = channel off.

**File Output**

| Setting | Default | What it does |
|---|---|---|
| **Enable File Output** | off | Add a File Output node that writes the selected passes as PNGs. |
| **Output path** | `//render/` | Base directory. |
| **line** | on | Write the line art (`line.png`). |
| **color** | on | Write the flat colour output (`color.png`). |
| **light (diffuse)** | on | Write the diffuse direct light pass (`light.png`); easier to composite than shadow. |
| **shadow** | off | Write the shadow pass (`shadow.png`); EEVEE's shadow pass is often noisy. |

**Build compositor nodes** - builds (or updates) the tree and reports what was
done. On 4.2 a note reminds you to render with F12 instead of the live view.

---

### STEP4: Manual Vertex Color

Touch up the automatic result by vertex painting. Needs `mecha_color` to
exist (run STEP1 first).

| Control | What it does |
|---|---|
| **Vertex Color Type** | Which layer **Paint Vertex Color** activates: *Mecha Color* (redraw the separation), *Mask Color* (paint to erase lines - any non-black colour), *Line Color* (line darkness; white = invisible). |
| **Paint Vertex Color** | Puts the selected meshes into Vertex Paint mode, activates the chosen layer, enables face selection masking with all faces selected, and sets the viewport to Solid / Flat / Vertex colour so you see what you paint. |

**Vertex-color options** (these feed STEP1's colour generation)

| Setting | Default | Range | What it does |
|---|---|---|---|
| **Color noise scale** | 1.0 | 0.01 - 10 | Larger scatters island colours more randomly. |
| **Min color distance** | 0.5 | 0 - 1.732 | Minimum RGB distance between neighbouring islands. Lower allows more similar colours. |
| **Max color retries** | 30 | 1 - 200 | Retries when a generated colour already exists nearby. |

**Half Fill** (Edit mode only): select two or more vertices that define a
boundary line; every face on one side of that line has the loops of the
selected vertices painted with the **Half mask colour** swatch (default red)
in `mecha_color`. Useful for forcing a line along a chosen boundary. The
operator has a `direction` property (in the redo panel) to flip which side is
painted.

The SVG export honours all of this (with *Honour STEP4 paint* on): repainted
`mecha_color` moves the lines, `mask_color` erases them, white `line_color`
hides them.

---

### STEP5: Cameras (batch export)

Lists every camera in the scene, sorted by name, with a tick box
(**Render this camera**, stored per object) that feeds **both** batch
operations:

| Button | What it does |
|---|---|
| **Export checked cameras** | Same as in the SVG panel: one SVG per ticked camera into `//svg_exports/`. Needs a saved .blend. |
| **Render checked cameras** | Raster path: renders each ticked camera and redirects the compositor File Output nodes to `//camera_renders/NN_<camera>/`. Needs STEP3 with File Output enabled, and a saved .blend. The normal render image is not saved - only the File Output passes. |

The active camera is marked with a different icon.

---

### Reset

**Reset scene** takes the add-on back out of the scene, after a confirmation
dialog. Undoable. It:

- removes the AOV group from every material,
- deletes the compositor nodes FreePencil built (only nodes with FreePencil's
  own labels - your own nodes are left alone),
- removes the AOV slots from the view layer,
- deletes the vertex colours it painted (`mecha_color` etc.),
- clears the viewport preview,
- restores render and viewport settings to what they were before STEP2/STEP3
  first ran (recorded in the .blend, so this works after reopening the file).

STEP1 has to be run again afterwards. If there is no recorded state (e.g. a
tree built before this add-on was installed) the settings are left as they
are and the report says so.

---

### Compositor editor panel

In the Compositor node editor's sidebar, tab **FreePencil SVG**: the single
**Include Anti-Aliasing Node** checkbox (same property as in STEP3), for when
you are already looking at the tree.

---

## Presets

The **Preset** menu at the top of the SVG panel sets these properties (all
others are left as they are):

| Preset | Pen | Merge | Simplify | Layers | Depth res | Samples | Other |
|---|---|---|---|---|---|---|---|
| **Fine pen** | 0.3 | 0.1 | 0.05 | Single | 1600 | 8 | - |
| **Bold outline, 2 pens** | 0.5 | 0.2 | 0.08 | By line source | 1600 | - | Outline layer on, outline depth step 0.10, one file per layer |
| **Quick draft** | 0.5 | 0.3 | 0.25 | Single | 800 | 4 | Open edges off |

---

## Output files

### Units and structure

- `<svg width="210mm" height="297mm" viewBox="0 0 210 297">` - user units
  are millimetres, page size is the chosen paper in the render's orientation.
- Every line is a `<polyline points="x,y x,y …"/>` with 3-decimal
  coordinates, inside a `<g>` carrying `fill="none"`, the stroke colour,
  `stroke-width` (mm), round caps and joins.
- With any layer mode other than *Single layer*, each group also has
  `inkscape:groupmode="layer" inkscape:label="<name>" id="layerN"` and the
  root declares the Inkscape namespace - this is what vpype and Inkscape read
  as layers.
- Layer order in the file: `hatch` first (it is a background), then
  `outline`, `silhouette`, `freestyle`, `crease`, `sharp`, `mecha`,
  `material`, `bone`, `open`, then `depth1`…`depth5`, then anything else
  (object names) and `regmarks`.
- Stroke widths: main pen for everything, except `outline` and `hatch` when
  their own width is non-zero, and depth bands, which thin linearly from the
  pen width down to *Far line weight* × pen width.
- Stroke colours: black for a single layer; a fixed palette keyed by layer
  name when *Colour layers* is on (so `outline` is the same colour in every
  file); grey for `hatch`.

### File names

| Operation | Written |
|---|---|
| Export SVG | The chosen path (`.svg` appended if missing). |
| … with *One file per layer* | `<name>_<layer>.svg` per layer, e.g. `robot_outline.svg`, `robot_mecha.svg`. |
| … with tiling | `<name>_r<row>c<col>.svg` per sheet, 1-based, e.g. `robot_r1c1.svg`, `robot_r1c2.svg`. |
| Export checked cameras | `//svg_exports/01_<camera>.svg`, `02_…` (numbered in name order; illegal filename characters replaced by `_`). |
| Export frame range | `//svg_exports/frame_0001.svg` … |
| Render checked cameras (raster) | `//camera_renders/01_<camera>/` + the File Output pass PNGs. |

`//` means "next to the saved .blend file".

### Post-processing with vpype

vpype is not bundled (its dependencies cannot be shipped for all supported
Blender versions), but the output is made for it:

```bash
vpype read out.svg reloop linesort write plot.svg
```

For HPGL, multi-layer pen assignment, `layout` and so on, see the vpype
documentation. Layers appear in vpype by their `inkscape:label` order.

---

## Recipes

**Two pens: heavy outline, fine detail.** Preset *Bold outline, 2 pens*.
Then in *Advanced*, set **Outline pen width** to the thick pen (e.g. 0.8) and
**Pen width** to the fine one. You get `<name>_outline.svg` and one file per
other source; plot `outline` with the thick pen and the rest with the fine one
(or leave *One file per layer* off and assign pens per layer in vpype/Inkscape).
Raise **Outline depth step** if interior panel steps are being picked up.

**Depth-cued drawing.** Layers = *By depth*, 3 bands, *Far line weight* 0.5.
Plot `depth1` with a 0.5 pen, `depth2` with 0.3, `depth3` with 0.1 - or keep
one pen and rely on the stroke widths for on-screen viewing.

**Toned drawing with hatching.** Light the scene (a sun lamp is enough),
enable **Hatching**, spacing ≈ 3-4 × pen width, threshold 0.5 to start.
Hatch goes to its own layer, so give it a lighter pen with **Hatch pen width**.

**A3 drawing on an A4 plotter.** Page = A4, Columns = 2 (for a landscape
render) or Rows = 2 (portrait), Registration marks on. Trim one sheet at the
seam, align the corner marks, tape.

**Fill the paper.** Fit = *Drawing bounds*. Note that *Merge* and *Simplify*
are in mm on the page, so the effective path count changes with the fit.

**Hand-drawn look.** Jitter 0.3-0.6 mm, scale 8-15 mm. Turn *Sort draw
order* on as usual; jitter is applied before sorting.

**Add a line the auto-separation missed.** Edit mode > select the edges >
*Edge > Mark Sharp* (or *Mark Freestyle Edge*), then enable **Sharp marks**
(or **Freestyle marks**) in *Line sources*. No repaint needed. Alternatively
re-run STEP1 with those edges marked Sharp - they become island boundaries.

**Remove unwanted lines.** STEP4 > Vertex Color Type = *Mask Color* > Paint
Vertex Color, paint any colour over the region. Keep **Honour STEP4 paint**
on. Press *Refresh preview* to see the effect.

**Force a line along a boundary you choose.** STEP4 > Half Fill in Edit mode
(select the boundary vertices first), or repaint one side in *Mecha Color*
with a distinct colour.

**Reproducible colours across sessions.** STEP1 > untick *Random seed each
run* and note the *Seed*. Same mesh + same seed = same `mecha_color`.

**Animation.** Set the scene frame range and step, save the .blend, press
**Export frame range**. Deforming rigs are re-evaluated per frame. Esc stops
the batch.

**Turntable / several views.** Add cameras, tick them in STEP5, save, press
**Export checked cameras**.

**Character with a rig.** STEP0 detects the Armature and paints
`bone_color`. In *Line sources*, turn **Bone boundaries** on if you want
lines at bone regions; list bones such as `head` in STEP1 *Hard boundary
bones* to get a hard line (chin) instead of a soft blend.

**Imported CAD with too many lines.** Turn **Open edges** off first (often
a third of all edges), then consider raising STEP1 *Min island area %* and
re-running STEP1.

---

## Scripting

All operators and properties are regular Blender ones and work headless
(`blender -b file.blend --python script.py`). Batch operators run
synchronously when called from a script.

| Operator | Id |
|---|---|
| STEP0 Auto setup | `bpy.ops.fpm.auto_setup()` |
| STEP1 Colour separation | `bpy.ops.fpm.auto_vertex_color()` (selected meshes) |
| STEP2 AOVs | `bpy.ops.fpm4.link_button()` |
| STEP3 Compositor | `bpy.ops.fpm2.link_button()` |
| STEP4 Paint | `bpy.ops.fpm3.link_button()` |
| STEP4 Half fill | `bpy.ops.fpm5.link_button(direction=-1)` |
| Export SVG | `bpy.ops.fpm.export_svg(filepath="//out.svg", selected_only=False)` |
| Export checked cameras | `bpy.ops.fpm.export_svg_cameras()` |
| Export frame range | `bpy.ops.fpm.export_svg_frames()` |
| Refresh / clear preview | `bpy.ops.fpm.svg_preview()` / `bpy.ops.fpm.svg_preview_clear()` |
| Apply preset | `bpy.ops.fpm.svg_preset(preset='FINE')` - also `'BOLD_OUTLINE'`, `'DRAFT'` |
| Render checked cameras | `bpy.ops.fpm.render_cameras()` |
| Reset | `bpy.ops.fpm.reset_scene()` |
| New colour seed | `bpy.ops.fpm.randomize_seed()` |

Settings are scene properties named `scene.fpm_*` (full list in the
[appendix](#appendix-complete-property-reference)); the camera tick is
`object.fpm_cam_render`.

Minimal headless export:

```python
import bpy
scene = bpy.context.scene
for o in scene.objects:
    o.select_set(o.type == "MESH")
bpy.ops.fpm.auto_setup()
scene.fpm_svg_page = 'A3'
scene.fpm_svg_layers = 'SOURCE'
bpy.ops.fpm.export_svg(filepath="//robot.svg")
```

The export core is also importable without operators:
`svg_export.export_svg(context, path, svg_export.SvgOptions(...))` returns a
statistics dict (paths, points, draw_mm, pen_up_mm, per-layer counts,
estimated_seconds, files written).

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Panel says **Set an active camera first** | Add a camera and make it the scene camera (Ctrl+Numpad 0). |
| **The model has to be painted first** | Run STEP0 (or STEP1 on the selected meshes). |
| Export error **No lines found** | Nothing painted, or every line source is off. Run STEP0/STEP1; check *Line sources*. |
| **Save the .blend to batch cameras** / batch buttons greyed out | Batch exports write next to the .blend, so it must be saved. |
| Preview shows lines on the back of the model | You are not looking through the camera, or the preview is stale (grey). Press Numpad 0 and *Refresh preview*. |
| Far too many lines on imported CAD | Turn off **Open edges**; raise **Outline depth step**; raise STEP1 *Min island area %*. |
| Lines missing at shallow folds | Lower the STEP1 *Edge Angle* (or turn *Auto edge angle* off and set it by hand) and re-run STEP1; or enable **Crease angle** with a lower angle. |
| Lines missing between two touching objects | Ensure STEP1 *Part tint* is on and re-run; or enable **Material boundaries** if they differ in material. |
| Surface lines vanish / show through | Adjust **Depth bias** (raise if vanishing, lower if showing through) or set **Neighbourhood** to 1. |
| Paths are fragmented / too many short paths | Raise **Merge (mm)** toward the pen width; switch **Fit** to *Drawing bounds* if the drawing is small on the page. |
| Hatching does nothing | Materials must be lit by real lights; check **Hatch threshold** (raise it). |
| Viewport went white / Rendered after STEP0 | *Also set up the raster render* was ticked. Untick STEP3 *White material preview*, or switch the viewport back to Solid, or use *Reset*. |
| Two FreePencil add-ons fight over the compositor | Run STEP2/STEP3 from only one add-on per scene. |
| Blender 4.2: no live line preview | Expected - the 4.2 viewport compositor ignores AOVs. Render with F12. SVG export is unaffected. |
| Transparent (BLEND) materials lose lines in the raster render | STEP0 converts them to HASHED unless *BLEND to HASHED* is off. |

---

## Supported Blender versions

| Blender | Status | SVG export | Raster render | Live raster preview |
|---|---|---|---|---|
| 5.2 LTS | Recommended | ✅ | ✅ | ✅ |
| 4.5 LTS | Recommended | ✅ | ✅ | ✅ |
| 4.3 | Verified | ✅ | ✅ | ✅ |
| 4.2 LTS | Limited | ✅ | ✅ (F12) | ❌ |

The SVG export produces identical line sets on all four. 4.1 and earlier are
not supported. A .blend saved in 5.2 will not have a working compositor tree
when opened in 4.5 (raster path only).

---

## Appendix: complete property reference

All properties live on `bpy.types.Scene` unless noted. "UI" is the panel
where the control appears.

### SVG export

| Property | UI label | Type | Default | Range / options | UI |
|---|---|---|---|---|---|
| `fpm_svg_page` | Page | enum | `A4` | `A5`, `A4`, `A3`, `LETTER` | SVG export |
| `fpm_svg_fit` | Fit | enum | `CAMERA` | `CAMERA`, `DRAWING` | SVG export |
| `fpm_svg_margin` | Margin (mm) | float | 10.0 | 0 - 100 | SVG export |
| `fpm_svg_pen` | Pen width (mm) | float | 0.3 | 0.01 - 5 | SVG export |
| `fpm_svg_layers` | Layers | enum | `NONE` | `NONE`, `SOURCE`, `OBJECT`, `DEPTH` | SVG export |
| `fpm_svg_split_files` | One file per layer | bool | False | | SVG export |
| `fpm_svg_layer_colors` | Colour layers | bool | True | | SVG export |
| `fpm_svg_depth_bands` | Depth bands | int | 3 | 2 - 5 | SVG export |
| `fpm_svg_depth_weight` | Far line weight | float | 0.6 | 0.1 - 1.0 | SVG export |
| `fpm_svg_hatch` | Hatching | bool | False | | SVG export |
| `fpm_svg_hatch_spacing` | Hatch spacing (mm) | float | 1.2 | 0.1 - 20 | SVG export |
| `fpm_svg_hatch_levels` | Hatch levels | int | 2 | 1 - 3 | SVG export |
| `fpm_svg_hatch_angle` | Hatch angle | float | 45.0 | 0 - 180 | SVG export |
| `fpm_svg_hatch_threshold` | Hatch threshold | float | 0.5 | 0 - 1 | SVG export |
| `fpm_svg_hatch_pen` | Hatch pen width (mm) | float | 0.0 | 0 - 5 | SVG export |
| `fpm_svg_tile_cols` | Columns | int | 1 | 1 - 6 | SVG export |
| `fpm_svg_tile_rows` | Rows | int | 1 | 1 - 6 | SVG export |
| `fpm_svg_tile_marks` | Registration marks | bool | True | | SVG export |
| `fpm_svg_jitter` | Jitter (mm) | float | 0.0 | 0 - 5 | SVG export |
| `fpm_svg_jitter_scale` | Jitter scale (mm) | float | 8.0 | 0.5 - 100 | SVG export |
| `fpm_svg_jitter_seed` | Jitter seed | int | 1 | 0 - 9999 | SVG export |
| `fpm_svg_preview` | Preview in viewport | bool | False | | (internal toggle) |
| `fpm_svg_preview_auto` | Auto refresh | bool | True | | SVG export |
| `fpm_svg_last_result` | Last export | string | "" | | SVG export (read-only display) |
| `fpm_svg_src_mecha` | Color separation | bool | True | | Line sources |
| `fpm_svg_src_material` | Material boundaries | bool | True | | Line sources |
| `fpm_svg_src_bone` | Bone boundaries | bool | False | | Line sources |
| `fpm_svg_src_open` | Open edges | bool | True | | Line sources |
| `fpm_svg_src_silhouette` | Silhouette | bool | True | | Line sources |
| `fpm_svg_respect_paint` | Honour STEP4 paint | bool | True | | Line sources |
| `fpm_svg_src_freestyle` | Freestyle marks | bool | False | | Line sources |
| `fpm_svg_src_sharp` | Sharp marks | bool | False | | Line sources |
| `fpm_svg_src_crease` | Crease angle | bool | False | | Line sources |
| `fpm_svg_crease_angle` | Angle (deg) | float | 60.0 | 0 - 180 | Line sources |
| `fpm_svg_merge_tolerance` | Merge (mm) | float | 0.1 | 0 - 5 | Advanced |
| `fpm_svg_simplify` | Simplify (mm) | float | 0.05 | 0 - 5 | Advanced |
| `fpm_svg_sort` | Sort draw order | bool | True | | Advanced |
| `fpm_svg_home` | Pen home | enum | `TL` | `TL`, `TR`, `BL`, `BR` | Advanced |
| `fpm_svg_outline_layer` | Outline layer | bool | True | | Advanced |
| `fpm_svg_outline_gap` | Outline depth step | float | 0.02 | 0 - 1 | Advanced |
| `fpm_svg_outline_pen` | Outline pen width (mm) | float | 0.0 | 0 - 5 | Advanced |
| `fpm_svg_depth_res` | Depth resolution | int | 1600 | 256 - 8192 | Advanced |
| `fpm_svg_samples` | Samples per edge | int | 8 | 1 - 64 | Advanced |
| `fpm_svg_bias` | Depth bias | float | 0.001 | 0 - 0.1 | Advanced |
| `fpm_svg_neighbourhood` | Neighbourhood | int | 0 | 0 - 4 | Advanced |
| `fpm_svg_keep_hidden` | Keep hidden lines | bool | False | | Advanced |
| `fpm_svg_plot_speed` | Pen down mm/s | float | 80.0 | 1 - 2000 | Advanced |
| `fpm_svg_travel_speed` | Travel mm/s | float | 200.0 | 1 - 2000 | Advanced |
| `fpm_svg_pen_lift` | Pen lift (s) | float | 0.12 | 0 - 5 | Advanced |

### STEP0 (Full Auto)

| Property | UI label | Type | Default |
|---|---|---|---|
| `fpm_auto_sharp` | Auto edge angle | bool | True |
| `fpm_auto_seam` | Seam/material boundaries | bool | True |
| `fpm_auto_merge` | Merge small islands | bool | True |
| `fpm_auto_part_tint` | Part tint | bool | True |
| `fpm_auto_bone` | Bone colours by rig detection | bool | True |
| `fpm_auto_hashed` | BLEND to HASHED (keep glass) | bool | True |
| `fpm_auto_raster` | Also set up the raster render | bool | **False** |
| `fpm_auto_aa` | Anti-aliasing | bool | True |
| `fpm_auto_supersample` | 2x supersampling (thin lines) | bool | True |
| `fpm_auto_detect_aov` | Auto AOVs from scene | bool | True |
| `fpm_auto_white_preview` | White material preview | bool | True |
| `fpm_auto_file_output` | Enable File Output | bool | False |

### STEP1 (colour separation)

| Property | UI label | Type | Default | Range / options |
|---|---|---|---|---|
| `fpm_mat_count` | Add the material ID | bool | False | |
| `fpm_sharp_auto` | Auto edge angle | bool | False | |
| `fpm_sharp_edges` | Edge Angle | float | 30.0 | 0 - 180 |
| `fpm_seam_boundaries` | Seam/material boundaries | bool | False | |
| `fpm_min_island_area_pct` | Min island area % | float | 0.0 | 0 - 5 |
| `fpm_bone_grouping_mode` | Bone grouping | enum | `basename` | `exact`, `basename`, `stripdigits` |
| `fpm_bone_hard_names` | Hard boundary bones | string | "" | comma-separated |
| `fpm_part_tint` | Part tint (mecha color) | bool | True | |
| `fpm_use_random_seed` | Random seed each run | bool | True | |
| `fpm_color_seed` | Seed | int | 0 | 0 - 2147483647 |
| `fpm_sharp_clear` | (not in UI) clear sharp | bool | False | Legacy: ignore Sharp marks as boundaries |

### STEP2 (AOV)

| Property | UI label | Type | Default |
|---|---|---|---|
| `fpm_bone_color` | AOV Bone Color | bool | False |
| `fpm_gen_color` | AOV Generate Color | bool | False |
| `fpm_mask_color` | AOV Mask Color | bool | False |
| `fpm_line_color` | AOV Line Color | bool | False |
| `fpm_mat_color` | AOV Material Boundary Color | bool | False |

### STEP3 (compositor)

| Property | UI label | Type | Default | Range / options |
|---|---|---|---|---|
| `fpm_node_type` | Node type | enum | `test` | `test`, `pro` |
| `fpm_enable_compositor_view` | Enable Compositor Preview | bool | True | |
| `fpm_white_preview` | White material preview | bool | False | |
| `fpm_white_keep_glass` | (not in UI) Keep glass transparent | bool | True | |
| `fpm_include_antialiasing` | Include Anti-Aliasing Node | bool | False | |
| `fpm_supersample` | 2x supersampling (thin lines) | bool | False | |
| `fpm_line_sensitivity` | Line sensitivity | float | 0.5 | 0.05 - 2.0 |
| `fpm_far_relief` | Amount | float | 0.0 | 0 - 1 |
| `fpm_far_relief_radius` | Radius (px) | float | 6.0 | 1 - 32 |
| `fpm_far_relief_threshold` | Threshold | float | 0.35 | 0.05 - 0.95 |
| `fpm_ch_depth` | Depth | float | 1.0 | 0 - 2 |
| `fpm_ch_mecha` | Mecha | float | 1.0 | 0 - 2 |
| `fpm_ch_bone` | Bone | float | 1.0 | 0 - 2 |
| `fpm_ch_mat` | Material | float | 1.0 | 0 - 2 |
| `fpm_ch_gen` | Generate | float | 1.0 | 0 - 2 |
| `fpm_file_output` | Enable File Output | bool | False | |
| `fpm_file_output_path` | Output path | string (dir) | `//render/` | |
| `fpm_fo_line` | line | bool | True | |
| `fpm_fo_color` | color | bool | True | |
| `fpm_fo_light` | light (diffuse) | bool | True | |
| `fpm_fo_shadow` | shadow | bool | False | |

### STEP4 (manual paint)

| Property | UI label | Type | Default | Range / options |
|---|---|---|---|---|
| `fpm_color_type` | Vertex Color Type | enum | `mecha_color` | `mecha_color`, `mask_color`, `line_color` |
| `fpm_color_noise_scale` | Color noise scale | float | 1.0 | 0.01 - 10 |
| `fpm_min_neighbor_color_distance` | Min color distance | float | 0.5 | 0 - 1.732 |
| `fpm_max_color_retries` | Max color retries | int | 30 | 1 - 200 |
| `fpm_half_color` | (swatch) Half mask color | RGBA | (1, 0, 0, 1) | |

### STEP5 (cameras)

| Property | UI label | Type | Default | Lives on |
|---|---|---|---|---|
| `fpm_cam_render` | Render this camera | bool | True | `bpy.types.Object` (each camera) |
