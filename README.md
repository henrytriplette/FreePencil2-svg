# FreePencil2 - SVG Mod

[日本語](README.ja.md) | **English**

> **An unofficial fork of [FreePencil2](https://github.com/megamarsun/FreePencil2) by Masamune Sakaki.**
> It adds SVG export for pen plotters as the primary feature, and registers
> as a separate extension, so it can be installed and enabled alongside the
> original.

A Blender add-on that automatically generates line art from 3D models. Lines
come out as **vectors (SVG)** or as raster (a rendered image via the compositor).

**Its main use is SVG export for pen plotters.**

It automatically paints the model with vertex colors and extracts the color
boundaries as lines (the so-called "color-separation" method). Because the
principle is simple, it is fast: even a 1.28-million-polygon model is processed
in just under 30 seconds.

The hard part of the color-separation method has always been the preprocessing —
*how* to separate the colors. FreePencil2 automates exactly that.

- Automatically determines the split angle from the distribution of dihedral angles
- Colors the adjacency graph so that neighboring regions always land in different color classes
- Automatically merges tiny regions
- Assigns distinct tones to parts that touch each other
- Per-bone color separation via rig detection

A single press of "Fully automatic setup" in STEP0 completes everything from the
color separation to building the compositor nodes. Anywhere you don't like the
automatic result, you can touch it up with vertex painting in STEP4 (redraw the
color separation / add lines / remove lines).

## Running alongside the original

Both add-ons can be **installed and enabled at the same time**. The
extension id (`freepencil2_svg_mod`), operator ids (`fpm*.`), panel ids
(`FPM_PT_*`) and scene properties (`fpm_*`) are all separate, so neither
shadows the other. This add-on's sidebar tab is **FreePencil SVG**.

Verified by installing both and enabling them together: 60 registered
types (30 each) and 160 scene properties (80 each), with both sets live.

**Use only one of them per scene for STEP2/STEP3.** What is separate is the
*registration*, not the data they write:

- STEP3 builds the **scene's** compositor tree. Two add-ons both building it
  in one scene overwrite each other, whatever their ids are.
- STEP2 injects the AOV node group into **every material**. The node group
  names (`FreePencil_v1_1_0_*`, `FreePencil_aov_Group_v1_1_0`) are
  deliberately left shared: giving this fork its own would inject a second
  AOV group into every material and write the AOVs twice.

So: enable both, switch between their panels freely, and paint or export
from either — but run the compositor setup from one add-on at a time.
Settings do not carry over either, since the property names differ; the
vertex colour layers (`mecha_color` and friends) are shared, so both read
the same painted meshes.

## SVG export (pen plotter)

Export from **SVG Export (pen plotter)**, the first section of the sidebar.
Run it after the color separation is done (STEP0 or STEP1).

It does not trace the rendered image. The vectors come from the definition of
a line itself — an edge whose two adjacent faces differ in color. The
compositor's Sobel draws a band with width, so tracing it makes a plotter go
around each line twice as an outline. Emitting the edge itself always gives a
single centreline.

- Hidden-line removal compares against the Z pass; only what is in front survives
- Line ends are joined and the draw order is optimised before writing
- Millimetres, no fill, constant stroke width (stroke width = pen width)
- STEP4 paint is honoured: lines erased with `mask_color`, or made invisible
  with `line_color`, do not reach the SVG either

### Line sources

Each source can be switched on or off independently.

| source | default | what it is |
|---|---|---|
| Color separation | ON | edges where the adjacent faces' `mecha_color` differs |
| Material boundaries | ON | edges where the material changes |
| Bone boundaries | **OFF** | edges where `bone_color` differs; adds many lines |
| Open edges | ON | edges without exactly two faces |
| Silhouette | ON | edges where the surface turns away from the camera |

**Open edges** are abundant in imported CAD with unwelded shells: 255,985 of
683,165 edges (37%) on the measured model. Switching them off cuts the count.

**Bone boundaries** are the one source whose default differs from the raster
path (`fpm_ch_bone` defaults to 1.0). On a plotter they add too many lines, so
turn them on only if you want them.

### Layers (assigning pens)

The output can be split into SVG layers (`Single layer` / `By line source` /
`By object`). vpype and Inkscape read these, so you can assign a different pen
to each.

Layers can also be written as **separate files** (`<name>_<layer>.svg`). The
page transform is computed once across all layers and shared, so the files
line up when loaded separately.

Chaining and draw-order sorting stay inside a layer, so splitting increases
both the path count and the pen travel (measured: 3286 paths / 2997 mm ->
5210 / 6434 by source, 5627 / 7583 by object). If you do not need separate
pens, a single layer plots fastest.

To draw the outline in a heavier pen, use the **outline layer** (on by
default). It looks to either side of each edge in the depth buffer and keeps
only those where one side is background, or drops away sharply behind the
edge, putting them in an `outline` layer.

**The silhouette layer is not the outer contour.** It holds every edge where
adjacent faces flip between front- and back-facing, which on thin-plate CAD
occurs throughout the interior too. Use the outline layer instead.

**Outline depth step** (default 0.02) controls how fine a step counts.
Raising it keeps only the larger steps: measured 1512 paths at 0.005, 1083 at
0.02, and 410 at 0.10 - by then essentially the machine's outer contour and
its feet.

### Seeing it before you export

**Refresh preview** draws the lines that would be exported straight into the
3D view, so you can judge line density and what got culled without opening
the SVG somewhere else.

Hidden-line removal is computed for the render camera, so **it is only
truthful from camera view** - orbit away and the occlusion no longer matches
(the line positions still do). It does not follow changes on its own; press
it again after changing a setting.

### Fitting to the page

| fit | what it does |
|---|---|
| Camera frame | keeps the composition; a small subject stays small on paper |
| Drawing bounds | fits what was actually drawn to the page, so the margin is constant |

Drawing bounds is the default. Beyond a predictable margin, it makes
the merge tolerance (in mm) behave honestly against the drawing. When the
drawing is small on the page, unrelated ends fall inside the tolerance and
only the path count goes down. Measured: 162x125 mm and 3286 paths by camera
frame, 246x190 mm and 3802 paths by drawing bounds - the latter is the count
you actually get at that pen size. Choose the camera frame only when you want
the composition reproduced on the paper as-is.

### Presets and camera batch

**Preset** at the top of the panel holds the usual combinations (`Fine pen`,
`Bold outline, 2 pens`, `Quick draft`).

**Export checked cameras** writes `//svg_exports/NN_<camera>.svg` for every
camera ticked in STEP5 (the .blend must be saved). If one camera fails the
rest are still written, and the failure is reported.

The settings are split across three panels: what you touch every time is in
the parent, with line sources and the finer settings in their own sub-panels.

### Plot time estimate

Shown in the panel after an export: drawn length, travel length and an
estimated time, from the pen-down speed, travel speed and the seconds each
pen lift costs (12.8 to 17.1 minutes for the measured CAD model). It does not
model acceleration, so treat it as a guide.

Measured (a 1,047,642-face CAD model, A4 landscape, 1600 px):

| stage | paths | pen-up travel |
|---|---|---|
| chains split at junctions | 26927 | 183948 mm |
| line ends joined (0.1 mm default) | 3286 | — |
| draw order sorted | 3286 | 2997 mm |

About 5 seconds for the whole export (excluding the color separation).

**Set the merge tolerance from the pen width, not from how small the drawing
is.** If the drawing is small on the page, unrelated ends fall inside the
tolerance and only the path count goes down.

For `reloop`, `layout` or HPGL output, run the result through vpype. vpype is
not bundled: it requires Shapely and scipy, which cannot be reconciled with
shipping one package for 4.2 through 5.2.

```bash
vpype read out.svg reloop linesort write plot.svg
```

### Measured processing times

Blender 4.5, from pressing STEP0 until completion (each measured twice).

| Model | Scale | Time |
|---|---|---|
| Mecha | 155 meshes / 50k polygons | approx. 8.7 s |
| Tank | 43 meshes / 420k polygons | approx. 19.4 s |
| Steam locomotive | single mesh / 1.28M polygons | approx. 28 s |

### Known limitations

- Transparent materials (BLEND) are converted to HASHED because AOVs are not
  written for them (real glass with transparency/refraction is out of scope; this
  can be disabled with the checkbox in STEP0). Shape, vertex count, sharp edges
  and seams are left unchanged
- Due to the interaction between part/tone separation and the brightness ceiling,
  models with many parts may end up with the adjacent luminance difference of some
  parts halved, making the lines faint (unresolved)
- Line width differs slightly between 4.5 and 5.2 (positions match 99.9%; 5.2 lays
  down about 1.8% more ink). Stick to one of them within a single artwork
- A .blend saved in 5.2 will not have a correctly working compositor when opened in 4.5

## Supported versions

The same package can be installed on all of the following, and the regression
tests are run on every version (61 of them now, 21 covering the SVG export).
The SVG export is confirmed to produce identical results on 4.5.6 and 5.2.1 -
edges, path count and drawn length all match on a 1M-face model.

| Blender | Status | Rendering | Live viewport preview |
|---|---|---|---|
| 5.2 LTS | Recommended | ✅ | ✅ |
| 4.5 LTS | Recommended | ✅ | ✅ |
| 4.3 | Verified | ✅ | ✅ |
| 4.2 LTS | **Limited support** | ✅ | ❌ |

**About the limited support for 4.2.** F12 rendering produces the same line art as
the other versions (the difference in line volume for an identical scene is within
1.1% across all four versions). However, the 4.2 viewport compositor does not
evaluate AOV outputs, so no lines appear in the live preview. There is no
workaround on the add-on side, so on 4.2 the add-on does not switch to rendered
view at all and shows a note to that effect in the panel. Preview-related bugs will
not be fixed for 4.2.

4.1 and earlier are not supported (`ShaderNodeOutputAOV.aov_name` does not exist).

## Building and installing

The distribution zip can be built with Blender's CLI.

```bash
blender --command extension build --source-dir . --output-dir dist
```

Install the generated `dist/freepencil2_svg_mod-*.zip` via
**Edit → Preferences → Add-ons → ▼ → Install from Disk**.

## About the original and this fork

This repository is an unofficial fork of [megamarsun/FreePencil2](https://github.com/megamarsun/FreePencil2).
The color-separation method itself and the STEP0-STEP5 raster pipeline are
the original author's work; what this fork adds is the SVG export.

**The articles, manual and paid support on the original author's note do
not cover this fork.** Please direct questions and purchases about the
original to the original channels, and bugs or requests for this fork to
this repository's issues.

- Original: https://github.com/megamarsun/FreePencil2
- The original author's note (articles, manual, support): https://note.com/megamarsun/n/nddacd81c6eae

**This is a development repository.** No ready-to-use zip is hosted here;
build it yourself with the steps under "Building and installing" above.
Source for past versions is available from the git tags.

The change history is in [CHANGELOG.md](CHANGELOG.md).

## Repository layout

- The add-on itself lives at the repository root
- `external_resources/` — scripts that generate the node groups (for 4.x and 5.x)
- `locale/` — translation files
- `dev/` — evaluation pipeline and regression tests for development (not needed to run the add-on)

## License

GPL-3.0-or-later

## Authors

- Original: Masamune Sakaki — https://masamunesakaki.com/
- SVG Mod: Henry Triplette — https://github.com/henrytriplette/FreePencil2-svg

A modification, under the same licence, of the original released under
GPL-3.0-or-later.
