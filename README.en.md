# FreePencil2

[日本語](README.md) | **English**

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

The same package can be installed on all of the following. The 33 regression tests
are run on every version.

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

Install the generated `dist/freepencil2-*.zip` via
**Edit → Preferences → Add-ons → ▼ → Install from Disk**.

## About this repository and note

**This is a development repository.** The progress of development is published
as-is; use it at your own risk. No user manual and no support are included.

| | GitHub (here) | note |
|---|---|---|
| Source code | **Yes** (all versions via tags) | Yes (the zip contains .py files) |
| Ready-to-use zip | **Not provided** (build it yourself) | **Included** |
| Zips of past versions | **Not provided** | **Yes** |
| Japanese manual | No | **Yes** |
| Support | No | **For purchasers of the first article** |
| Update notifications | No | **Yes** |

For the source of a past version, get it from the git tag and build it yourself.
**Prebuilt past versions are not hosted on GitHub.** Old packages left lying around
make it hard to isolate the cause of problems; if you need a past version, that is
handled through the paid support on note.

The follow-up articles also cover how to make line art and various techniques.
Buying them supports development.

→ note: https://note.com/megamarsun/n/nddacd81c6eae

The change history is in [CHANGELOG.md](CHANGELOG.md).

## Repository layout

- The add-on itself lives at the repository root
- `external_resources/` — scripts that generate the node groups (for 4.x and 5.x)
- `locale/` — translation files
- `dev/` — evaluation pipeline and regression tests for development (not needed to run the add-on)

## License

GPL-3.0-or-later

## Author

Masamune Sakaki — https://masamunesakaki.com/
