"""Scene properties used by FreePencil operators."""

import bpy
import logging
from bpy.props import (
    StringProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    EnumProperty,
    BoolProperty,
)

logger = logging.getLogger(__name__)

def _update_line_tuning(self, context):
    """線の感度/チャンネル強さスライダーの即時反映。

    生成済みの FreePencil ノードグループのランプ位置を直接更新する
    (ノードエディタを開かずにサイドバーだけで調整できる)。
    """
    from . import fp_core
    scene = context.scene
    for ng in bpy.data.node_groups:
        if ng.name.startswith(fp_core.NODE_GROUP_PREFIX):
            fp_core.apply_line_tuning(
                ng,
                getattr(scene, "fpm_line_sensitivity", 1.0),
                fp_core.channel_strengths_from_scene(scene))


def _update_far_relief(self, context):
    """遠景つぶれ軽減のスライダーを、生成済みノードへ即時反映する。"""
    from . import fp_core
    scene = context.scene
    for ng in bpy.data.node_groups:
        if ng.name.startswith(fp_core.NODE_GROUP_PREFIX):
            fp_core.far_relief_from_scene(ng, scene)


def _update_svg_preview(self, context):
    """プレビューの ON/OFF。切ったら描画ハンドラも外す。"""
    from . import svg_export
    if self.fpm_svg_preview:
        if svg_export._preview_segments is None:
            try:
                svg_export.refresh_preview(context)
            except RuntimeError as exc:
                logger.info(f"SVG preview not ready: {exc}")
                self["fpm_svg_preview"] = False
                return
        svg_export.enable_preview()
    else:
        svg_export.disable_preview()
    for area in getattr(context.screen, "areas", ()):
        if area.type == 'VIEW_3D':
            area.tag_redraw()


def _update_svg_preview_auto(self, context):
    """自動更新の ON/OFF。ON にしたら(プレビューが出ていれば)タイマーを掛ける。"""
    from . import svg_export
    if self.fpm_svg_preview_auto:
        svg_export.ensure_auto_timer()
    else:
        svg_export.stop_auto_timer()


def _update_white_preview(self, context):
    """白マテリアル強制プレビューの ON/OFF(非破壊スワップ)。"""
    from . import fp_core
    scene = context.scene
    n = fp_core.set_white_preview(
        scene, scene.fpm_white_preview,
        keep_glass=getattr(scene, "fpm_white_keep_glass", True))
    logger.info(f"White preview {'ON' if scene.fpm_white_preview else 'OFF'}: "
                f"{n} objects")


def _update_white_keep_glass(self, context):
    """プレビュー中にガラス維持を切り替えたら復元→再適用で反映する。"""
    from . import fp_core
    scene = context.scene
    if scene.fpm_white_preview:
        fp_core.set_white_preview(scene, False)
        fp_core.set_white_preview(scene, True,
                                  keep_glass=scene.fpm_white_keep_glass)


def register_props():
    """プロパティを登録する関数"""
    scene = bpy.types.Scene

    t = bpy.app.translations.pgettext
    color_type_items = [
        ('mecha_color', t("Mecha Color"), t("Mecha Color")),
        ('mask_color',  t("Mask Color(paint to erase lines)"),
         t("Lines vanish where painted. The brightness does not matter")),
        ('line_color',  t("Line Color(line darkness)"),
         t("Sets how dark the line is. White makes it invisible")),
    ]
    node_type_items = [
        ('test', t("Test Node"), t("Test Node")),
        ('pro',  t("Pro Node"),  t("Pro Node")),
    ]

    props_to_register = {
        "fpm_sharp_clear": BoolProperty(
            name="clear sharp",
            description="Erase outline's sharp edges",
            default=False
        ),
        "fpm_mat_count": BoolProperty(
            name="material ID",
            description="Add the material ID",
            default=False
        ),
        "fpm_sharp_auto": BoolProperty(
            name="Auto edge angle",
            description=(
                "Choose the sharp-edge angle automatically from the mesh's "
                "dihedral-angle distribution (per object)"
            ),
            # 既定OFF: 既存ワークフロー(特にボーン系キャラ)の挙動を変えない。
            # 一括評価パイプラインはプリセットで明示的にONにする。
            default=False
        ),
        "fpm_sharp_edges": FloatProperty(
            name="Line sharp edges",
            description="Outline's angle threshold.",
            default=30.0,
            min=0.0,
            max=180.0
        ),
        "fpm_seam_boundaries": BoolProperty(
            name="Seam/material boundaries",
            description=(
                "Treat UV seams and material borders as island boundaries "
                "in addition to the edge angle"
            ),
            default=False
        ),
        "fpm_min_island_area_pct": FloatProperty(
            name="Min island area %",
            description=(
                "Merge islands smaller than this % of total mesh area "
                "into their largest neighbor (0 = off)"
            ),
            # 既定OFF(0): 既存挙動を変えない。バッチはプリセットで0.02を指定
            default=0.0,
            min=0.0,
            max=5.0,
            step=0.01,
            precision=3
        ),
        "fpm_color_type": EnumProperty(
            name="Vertex color type",
            description="Select vertex color type.",
            items=color_type_items,
            default='mecha_color'
        ),
        "fpm_node_type": EnumProperty(
            name="Select node type",
            description="Select Node Type",
            items=node_type_items,
            default='test'
        ),
        # Requires Blender 4.3+ for Real-Time Compositor preview
        "fpm_enable_compositor_view": BoolProperty(
            name="Enable Compositor Preview",
            description="Enable Compositor Preview",
            default=True
        ),
        "fpm_far_relief": FloatProperty(
            name="Far crush relief",
            description=(
                "Thin out lines where they have merged into solid black "
                "(typically the far background of a large set). "
                "0 = off, no change to the image"
            ),
            default=0.0, min=0.0, max=1.0, step=0.05, precision=2,
            update=_update_far_relief
        ),
        "fpm_far_relief_radius": FloatProperty(
            name="Relief radius",
            description=(
                "How far to look when measuring how crowded the lines are, "
                "in pixels. Larger = only wide black areas are thinned"
            ),
            default=6.0, min=1.0, max=32.0, step=100, precision=0,
            update=_update_far_relief
        ),
        "fpm_far_relief_threshold": FloatProperty(
            name="Relief threshold",
            description=(
                "How crowded an area must be before it is thinned. "
                "Lower = starts working on sparser lines"
            ),
            default=0.35, min=0.05, max=0.95, step=0.05, precision=2,
            update=_update_far_relief
        ),
        "fpm_line_sensitivity": FloatProperty(
            name="Line sensitivity",
            description=(
                "Scale the line-detection thresholds inside the node group. "
                "Lower = weaker edges also become solid lines (1.0 = raw node)"
            ),
            # 既定を 0.5 にする。1.0 はノードの素の値で、塗り分けは
            # できているのに検出しきい値に届かず線が出ない境界が多かった。
            # 線が出るかは RGB距離で決まり、境目は実測で 0.05〜0.14。
            # 明度の近い隣接色(水色と白など)がここを越えられていない。
            #
            # 実測(1920等倍・4モデルのインク):
            #   戦車 0.0855 -> 0.0919 / メカ 0.1048 -> 0.1110
            #   帆船 0.0482 -> 0.0534 / カメラ 0.0894 -> 0.1004
            # 目視でも索具が点線から実線になり、ノイズは増えなかった。
            # 0.35 まで下げてもメカは破綻しないので余裕を残して 0.5。
            default=0.5,
            min=0.05,
            max=2.0,
            step=0.05,
            precision=2,
            update=_update_line_tuning
        ),
        **{
            f"fpm_ch_{ch}": FloatProperty(
                name=f"{label} strength",
                description=(
                    f"Line strength of the {label} channel. "
                    "1.0 = current, higher = more/stronger lines, 0 = off"
                ),
                default=1.0,
                min=0.0,
                max=2.0,
                step=0.05,
                precision=2,
                subtype='FACTOR',
                update=_update_line_tuning
            )
            for ch, label in (
                ("mecha", "Mecha"), ("depth", "Depth"), ("bone", "Bone"),
                ("gen", "Generate"), ("mat", "Material"),
            )
        },
        # STEP0 全自動が適用する項目の個別ON/OFF
        **{
            name: BoolProperty(name=label, description=desc, default=default)
            for name, label, desc, default in (
                ("fpm_auto_sharp", "Auto: edge angle",
                 "Full auto sets the sharp-edge angle automatically", True),
                ("fpm_auto_seam", "Auto: seam/material boundaries",
                 "Full auto splits islands at UV seams and material borders", True),
                ("fpm_auto_merge", "Auto: merge small islands",
                 "Full auto merges tiny islands (0.02%)", True),
                ("fpm_auto_part_tint", "Auto: part tint",
                 "Full auto separates touching parts by brightness bands", True),
                ("fpm_auto_bone", "Auto: bone AOV by rig detection",
                 "Full auto enables the bone AOV when an armature is found", True),
                ("fpm_auto_aa", "Auto: anti-aliasing",
                 "Full auto includes the anti-aliasing node", True),
                ("fpm_auto_hashed", "Auto: BLEND to HASHED",
                 "Full auto converts BLEND materials (except real glass) to "
                 "HASHED so AOVs render", True),
                ("fpm_auto_supersample", "Auto: 2x supersampling",
                 "Full auto enables 2x render + 50% output scaling. "
                 "Near-essential: FreePencil lines are too thick without it",
                 True),
                ("fpm_auto_detect_aov", "Auto: AOVs from scene",
                 "Full auto owns the AOV setup: gen/mask/line follow whether "
                 "those vertex colors are painted, mat follows material ID. "
                 "STEP2 manual toggles are overridden while this is on", True),
                ("fpm_auto_file_output", "Auto: enable File Output",
                 "Full auto also enables the STEP3 File Output node", False),
                ("fpm_auto_white_preview", "Auto: white material preview",
                 "Full auto turns on the white material preview so the line "
                 "art is visible right after setup. Materials are untouched "
                 "(the compositor is switched); turn it off to see the "
                 "original materials. Needs the raster setup", True),
                # 既定は OFF。SVG 書き出しはコンポジタを通らないので、
                # 主目的だけなら STEP2/STEP3 は要らない。ONにすると
                # ラスタのレンダリング(F12)まで組み上がる
                ("fpm_auto_raster", "Auto: also set up the raster render",
                 "Full auto also runs STEP2 (AOV) and STEP3 (compositor "
                 "nodes), for rendering line art as an image with F12. The "
                 "SVG export does not use them, so this is off by default",
                 False),
            )
        },
        "fpm_supersample": BoolProperty(
            name="2x supersampling (thin lines)",
            description=(
                "Render at 200% resolution and scale the compositor output "
                "back to 50%, turning 2px lines into crisp 1px lines. "
                "Applies to the Composite output and File Output slots"
            ),
            default=False
        ),
        "fpm_white_preview": BoolProperty(
            name="White material preview",
            description=(
                "Temporarily replace all materials with a flat white "
                "emission (AOV-enabled) to preview pure line art. "
                "Original materials are backed up per object and fully "
                "restored when turned off"
            ),
            default=False,
            update=_update_white_preview
        ),
        "fpm_white_keep_glass": BoolProperty(
            name="Keep glass transparent",
            description=(
                "While white preview is on, leave real glass materials "
                "(Transmission / low Alpha) untouched so you can still "
                "see through windows"
            ),
            default=True,
            update=_update_white_keep_glass
        ),
        "fpm_file_output": BoolProperty(
            name="File Output",
            description=(
                "Add a File Output node to the generated compositor tree "
                "that writes the selected passes as PNGs"
            ),
            default=False
        ),
        # どのパスを書き出すかは個別に選ぶ。影は EEVEE だとノイズが多く
        # 使えないことが多いので既定 OFF、ディフューズ直接光を既定 ON。
        "fpm_fo_line": BoolProperty(
            name="Write line pass",
            description="Write the line art to line.png",
            default=True
        ),
        "fpm_fo_color": BoolProperty(
            name="Write color pass",
            description="Write the flat color output to color.png",
            default=True
        ),
        "fpm_fo_light": BoolProperty(
            name="Write light pass",
            description=(
                "Write the diffuse direct light pass to light.png. "
                "Easier to composite than the shadow pass"
            ),
            default=True
        ),
        "fpm_fo_shadow": BoolProperty(
            name="Write shadow pass",
            description=(
                "Write the shadow pass to shadow.png. "
                "EEVEE's shadow pass is often noisy"
            ),
            default=False
        ),
        "fpm_file_output_path": bpy.props.StringProperty(
            name="File Output path",
            description="Base path for the File Output node",
            default="//render/",
            subtype='DIR_PATH'
        ),
        "fpm_include_antialiasing": BoolProperty(
            name="Include Anti-Aliasing Node",
            description="Insert Anti-Aliasing node before Composite",
            default=False,
        ),
        "fpm_gen_color": BoolProperty(
            name="generator color",
            description="AOV Generator Color",
            default=False
        ),
        "fpm_mask_color": BoolProperty(
            name="mask color",
            description="AOV Mask Color(White erases lines)",
            default=False
        ),
        "fpm_line_color": BoolProperty(
            name="line color",
            description="AOV Line Color",
            default=False
        ),
        "fpm_mat_color": BoolProperty(
            name="material color",
            description="AOV Material Boundary Color",
            default=False
        ),
        "fpm_bone_color": BoolProperty(
            name="bone color",
            description="AOV Bone Color",
            default=False
        ),
        "fpm_color_noise_scale": FloatProperty(
            name="Color noise scale",
            description="Increasing the scale scatters island colors more randomly.",
            default=1.0,
            min=0.01,
            max=10.0,
            step=0.1,
            precision=2,
            subtype='FACTOR'
        ),
        "fpm_min_neighbor_color_distance": FloatProperty(
            name="Min color distance",
            description="Minimum RGB distance between neighboring islands (0–1.732). Lower values allow similar colors.",
            default=0.5,
            min=0.0,
            max=1.732,
            step=0.01,
            precision=2
        ),
        "fpm_max_color_retries": IntProperty(
            name="Max color retries",
            description="How many times to retry when a color already exists",
            default=30,
            min=1,
            max=200
        ),
        "fpm_use_random_seed": BoolProperty(
            name="Random seed each run",
            description=(
                "Generate a new random seed every run. "
                "Turn this off to reproduce exactly the same island colors."
            ),
            default=True
        ),
        "fpm_color_seed": IntProperty(
            name="Color seed",
            description=(
                "Seed for island color generation. "
                "The same seed with the same mesh reproduces the same colors."
            ),
            default=0,
            min=0,
            max=2147483647
        ),
        "fpm_bone_grouping_mode": EnumProperty(
            name=t("Bone color grouping"),
            description=t("How to group bone names when coloring bone_color"),
            items=[
                ('exact', t("Exact"), t("Use the bone/vertex-group name as-is")),
                ('basename', t("Basename (.###)"), t("Treat suffix like .001 as the same")),
                ('stripdigits', t("Strip trailing digits"), t("Ignore trailing digits even without dot")),
            ],
            default='basename',
        ),
        "fpm_part_tint": BoolProperty(
            name="Part tint (mecha color)",
            description=(
                "Give touching parts (objects) different brightness bands "
                "in mecha_color so part boundaries (hairline, collar) become "
                "lines; bone_color stays a pure weight blend and the node "
                "composites both channels"
            ),
            default=True
        ),
        "fpm_bone_hard_names": bpy.props.StringProperty(
            name="Hard boundary bones",
            description=(
                "Comma-separated bone names whose region boundary should be "
                "a hard step in bone_color (so a line appears there, e.g. "
                "'head' for a chin line). Empty = all soft blending"
            ),
            default=""
        ),
        "fpm_half_color": FloatVectorProperty(
            name="Half mask color",
            description="Color used by Half Fill",
            subtype='COLOR',
            size=4,
            min=0.0,
            max=1.0,
            default=(1.0, 0.0, 0.0, 1.0)
        ),

        # --- SVG 書き出し(ペンプロッタ) --------------------------------
        # 既定値は実機モデル(104万面のCAD)で詰めたもの。詳しくは
        # svg_export.py と dev/note_assets/README.md を見ること。
        "fpm_svg_page": EnumProperty(
            name="Page",
            description="Paper size. Orientation follows the render aspect",
            items=[('A5', "A5", "148 x 210 mm"),
                   ('A4', "A4", "210 x 297 mm"),
                   ('A3', "A3", "297 x 420 mm"),
                   ('LETTER', "Letter", "215.9 x 279.4 mm")],
            default='A4'
        ),
        "fpm_svg_margin": FloatProperty(
            name="Margin",
            description="Page margin in millimetres",
            default=10.0, min=0.0, max=100.0
        ),
        "fpm_svg_pen": FloatProperty(
            name="Pen width",
            description="Stroke width in millimetres. Match your pen",
            default=0.3, min=0.01, max=5.0
        ),
        "fpm_svg_merge_tolerance": FloatProperty(
            name="Merge tolerance",
            description=("Join line ends closer than this (mm). Set it from "
                         "the pen width, not from how small the drawing is"),
            default=0.1, min=0.0, max=5.0
        ),
        "fpm_svg_simplify": FloatProperty(
            name="Simplify",
            description="Drop points that move the line less than this (mm)",
            default=0.05, min=0.0, max=5.0
        ),
        "fpm_svg_sort": BoolProperty(
            name="Sort draw order",
            description="Reorder paths to shorten pen-up travel",
            default=True
        ),
        "fpm_svg_depth_res": IntProperty(
            name="Depth resolution",
            description=("Width of the depth pass used for hidden-line "
                         "removal. Line accuracy follows this"),
            default=1600, min=256, max=8192
        ),
        "fpm_svg_samples": IntProperty(
            name="Samples per edge",
            description="Visibility test points along each edge",
            default=8, min=1, max=64
        ),
        "fpm_svg_bias": FloatProperty(
            name="Depth bias",
            description=("Relative tolerance when comparing depth. Lines sit "
                         "on the surface, so some bias is required"),
            default=0.001, min=0.0, max=0.1, precision=4
        ),
        "fpm_svg_neighbourhood": IntProperty(
            name="Depth neighbourhood",
            description=("Radius in pixels for the depth lookup. Above 0 the "
                         "maximum is taken, which lets the interior of "
                         "detailed models show through the outer panels"),
            default=0, min=0, max=4
        ),
        # 線の出どころ。ラスタ経路の fp_ch_* に対応する。bone だけ既定で
        # 切ってある(fpm_ch_bone は 1.0 だが、ボーン境界はプロッタでは
        # 線が増えすぎるので、要る人だけ入れる)
        "fpm_svg_src_mecha": BoolProperty(
            name="Color separation",
            description="Edges where the mecha_color differs",
            default=True
        ),
        "fpm_svg_src_material": BoolProperty(
            name="Material boundaries",
            description="Edges between different materials",
            default=True
        ),
        "fpm_svg_src_bone": BoolProperty(
            name="Bone boundaries",
            description=("Edges where the bone_color differs. Off by "
                         "default: it adds a lot of lines for a plotter"),
            default=False
        ),
        "fpm_svg_src_open": BoolProperty(
            name="Open edges",
            description=("Edges without exactly two faces. Imported CAD "
                         "with unwelded shells produces many of these"),
            default=True
        ),
        "fpm_svg_src_silhouette": BoolProperty(
            name="Silhouette",
            description="Edges where the surface turns away from the camera",
            default=True
        ),
        "fpm_svg_src_freestyle": BoolProperty(
            name="Freestyle marks",
            description=("Edges marked for Freestyle (Edit mode: Edge > "
                         "Mark Freestyle Edge). A way to add lines by hand "
                         "without painting"),
            default=False
        ),
        "fpm_svg_src_sharp": BoolProperty(
            name="Sharp marks",
            description="Edges marked Sharp (Edit mode: Edge > Mark Sharp)",
            default=False
        ),
        "fpm_svg_src_crease": BoolProperty(
            name="Crease angle",
            description=("Edges whose dihedral angle is at or above the "
                         "threshold below. Folds without going through "
                         "the colour separation"),
            default=False
        ),
        "fpm_svg_crease_angle": FloatProperty(
            name="Crease angle",
            description=("Dihedral angle in degrees from which an edge "
                         "counts as a crease"),
            default=60.0, min=0.0, max=180.0
        ),
        "fpm_svg_respect_paint": BoolProperty(
            name="Honour STEP4 paint",
            description=("Drop lines erased with mask_color or made "
                         "invisible with line_color"),
            default=True
        ),
        "fpm_svg_fit": EnumProperty(
            name="Fit",
            description="How the drawing is placed on the page",
            items=[
                ('CAMERA', "Camera frame",
                 "Fit the camera frame to the page; the composition is kept, "
                 "but a small subject stays small on the paper"),
                ('DRAWING', "Drawing bounds",
                 "Fit what was actually drawn to the page, so the margin is "
                 "the same whatever the framing"),
            ],
            # 既定はカメラ。3Dビューで組んだ構図がそのまま紙に出るほうが
            # 予想と合う。DRAWING は紙いっぱいに使いたいときの選択肢
            default='CAMERA'
        ),
        "fpm_svg_depth_bands": IntProperty(
            name="Depth bands",
            description=("With layers by depth, how many bands to split the "
                         "drawing into (nearest first)"),
            default=3, min=2, max=5
        ),
        "fpm_svg_depth_weight": FloatProperty(
            name="Far line weight",
            description=("Stroke width of the farthest band, relative to the "
                         "pen width. Lower = more depth cueing"),
            default=0.6, min=0.1, max=1.0
        ),
        "fpm_svg_tile_cols": IntProperty(
            name="Tile columns",
            description=("Split the drawing across this many sheets "
                         "horizontally. 1 = no tiling"),
            default=1, min=1, max=6
        ),
        "fpm_svg_tile_rows": IntProperty(
            name="Tile rows",
            description=("Split the drawing across this many sheets "
                         "vertically. 1 = no tiling"),
            default=1, min=1, max=6
        ),
        "fpm_svg_tile_marks": BoolProperty(
            name="Registration marks",
            description="Add corner marks to each sheet for lining them up",
            default=True
        ),
        "fpm_svg_jitter": FloatProperty(
            name="Hand jitter",
            description=("Wobble the lines by this much (mm) so they read as "
                         "drawn rather than machined. 0 = off"),
            default=0.0, min=0.0, max=5.0
        ),
        "fpm_svg_jitter_scale": FloatProperty(
            name="Jitter scale",
            description=("Wavelength of the wobble (mm). Small = shaky, "
                         "large = long lazy curves"),
            default=8.0, min=0.5, max=100.0
        ),
        "fpm_svg_jitter_seed": IntProperty(
            name="Jitter seed",
            description="Change for a different wobble",
            default=1, min=0, max=9999
        ),
        "fpm_svg_hatch": BoolProperty(
            name="Hatching",
            description=("Add hatching from the diffuse light pass, as its "
                         "own layer. Needs lit materials: the shading comes "
                         "from the render, not from the line art"),
            default=False
        ),
        "fpm_svg_hatch_spacing": FloatProperty(
            name="Hatch spacing",
            description="Distance between hatch lines on the page (mm)",
            default=1.2, min=0.1, max=20.0
        ),
        "fpm_svg_hatch_levels": IntProperty(
            name="Hatch levels",
            description=("Tone steps. Each step covers a darker range at a "
                         "different angle, so dark areas end up cross-hatched"),
            default=2, min=1, max=3
        ),
        "fpm_svg_hatch_angle": FloatProperty(
            name="Hatch angle",
            description="Angle of the first hatch level, in degrees",
            default=45.0, min=0.0, max=180.0
        ),
        "fpm_svg_hatch_threshold": FloatProperty(
            name="Hatch threshold",
            description=("Hatch where the diffuse light is below this. "
                         "Higher = more of the model gets toned"),
            default=0.5, min=0.0, max=1.0
        ),
        "fpm_svg_split_files": BoolProperty(
            name="One file per layer",
            description=("With layers on, write a separate SVG per layer so "
                         "each can go to a different pen. All files share "
                         "one page transform, so they line up"),
            default=False
        ),
        "fpm_svg_preview": BoolProperty(
            name="Preview in viewport",
            description=("Draw the lines that would be exported in the 3D "
                         "view. Look through the camera: hidden line removal "
                         "is computed for the render camera"),
            default=False,
            update=_update_svg_preview
        ),
        "fpm_svg_plot_speed": FloatProperty(
            name="Pen down speed",
            description="Drawing speed in mm/s, used for the time estimate",
            default=80.0, min=1.0, max=2000.0
        ),
        "fpm_svg_travel_speed": FloatProperty(
            name="Travel speed",
            description="Pen-up speed in mm/s, used for the time estimate",
            default=200.0, min=1.0, max=2000.0
        ),
        "fpm_svg_pen_lift": FloatProperty(
            name="Pen lift",
            description="Seconds per pen up/down, used for the time estimate",
            default=0.12, min=0.0, max=5.0
        ),
        "fpm_svg_last_result": StringProperty(
            name="Last export",
            description="Summary of the most recent SVG export",
            default=""
        ),
        "fpm_svg_layers": EnumProperty(
            name="Layers",
            description=("Split the output into SVG layers. vpype and "
                         "Inkscape read these, so you can assign a "
                         "different pen to each"),
            items=[
                ('NONE', "Single layer", "One layer for everything"),
                ('SOURCE', "By line source",
                 "Silhouette, color separation, material, bone, open edges"),
                ('OBJECT', "By object", "One layer per mesh object"),
                ('DEPTH', "By depth",
                 "Near to far, so each band can take a different pen"),
            ],
            default='NONE'
        ),
        "fpm_svg_outline_layer": BoolProperty(
            name="Outline layer",
            description=("With layers by source, put the edges that actually "
                         "form the outline of the drawing (against the "
                         "background, or across a depth step) into their own "
                         "layer. Unlike Silhouette, this is decided from the "
                         "depth buffer, so thin plates do not fill it"),
            default=True
        ),
        "fpm_svg_outline_gap": FloatProperty(
            name="Outline depth step",
            description=("How much deeper one side of an edge must be, "
                         "relative to the edge, to count as an outline"),
            default=0.02, min=0.0, max=1.0, precision=3
        ),
        "fpm_svg_keep_hidden": BoolProperty(
            name="Keep hidden lines",
            description="Skip hidden-line removal (for diagnosis)",
            default=False
        ),
        "fpm_svg_home": EnumProperty(
            name="Pen home",
            description=("Corner the plotter parks the pen in. The draw "
                         "order starts from here, so the first move is short"),
            items=[('TL', "Top left",
                    "AxiDraw and most Inkscape-driven plotters"),
                   ('TR', "Top right", ""),
                   ('BL', "Bottom left", "Most G-code plotters"),
                   ('BR', "Bottom right", "")],
            default='TL'
        ),
        "fpm_svg_layer_colors": BoolProperty(
            name="Colour layers",
            description=("Give each SVG layer its own stroke colour so the "
                         "pens are told apart in Inkscape or vpype. Plotters "
                         "ignore colour; a single layer stays black"),
            default=True
        ),
        "fpm_svg_outline_pen": FloatProperty(
            name="Outline pen width",
            description=("Stroke width of the outline layer in mm "
                         "(0 = same as the pen width)"),
            default=0.0, min=0.0, max=5.0
        ),
        "fpm_svg_hatch_pen": FloatProperty(
            name="Hatch pen width",
            description=("Stroke width of the hatch layer in mm "
                         "(0 = same as the pen width)"),
            default=0.0, min=0.0, max=5.0
        ),
        "fpm_svg_preview_auto": BoolProperty(
            name="Auto refresh",
            description=("Recompute the preview on its own once the camera, "
                         "the meshes or the settings have held still for a "
                         "moment. Turn it off on heavy scenes"),
            default=True,
            update=_update_svg_preview_auto
        )
    }

    for prop_name, prop_value in props_to_register.items():
        if not hasattr(bpy.types.Scene, prop_name):
            setattr(scene, prop_name, prop_value)
            logger.info(f"Registered property: {prop_name}")
        else:
            logger.info(f"Property already exists: {prop_name}")

    # カメラ一括レンダリング対象のチェック(オブジェクト単位)
    if not hasattr(bpy.types.Object, "fpm_cam_render"):
        bpy.types.Object.fpm_cam_render = BoolProperty(
            name="Render this camera",
            description=(
                "Include this camera in FreePencil's "
                "'Render checked cameras' batch"
            ),
            default=True
        )

def unregister_props():
    """プロパティを解除する関数"""
    scene = bpy.types.Scene
    props_to_clear = [
        "fpm_sharp_edges", "fpm_sharp_auto", "fpm_seam_boundaries",
        "fpm_min_island_area_pct", "fpm_sharp_clear",
        "fpm_color_type", "fpm_mat_count",
        "fpm_gen_color", "fpm_mask_color", "fpm_line_color",
        "fpm_mat_color", "fpm_bone_color", "fpm_enable_compositor_view",
        "fpm_include_antialiasing", "fpm_line_sensitivity",
        "fpm_far_relief", "fpm_far_relief_radius", "fpm_far_relief_threshold",
        "fpm_ch_mecha", "fpm_ch_depth", "fpm_ch_bone", "fpm_ch_gen", "fpm_ch_mat",
        "fpm_file_output", "fpm_file_output_path",
        "fpm_fo_line", "fpm_fo_color", "fpm_fo_light", "fpm_fo_shadow",
        "fpm_white_preview", "fpm_white_keep_glass", "fpm_supersample",
        "fpm_auto_sharp", "fpm_auto_seam", "fpm_auto_merge", "fpm_auto_part_tint",
        "fpm_auto_bone", "fpm_auto_aa", "fpm_auto_hashed", "fpm_auto_file_output",
        "fpm_auto_detect_aov", "fpm_auto_supersample", "fpm_auto_white_preview",
        "fpm_auto_raster",
        "fpm_color_noise_scale", "fpm_min_neighbor_color_distance",
        "fpm_max_color_retries",
        "fpm_use_random_seed", "fpm_color_seed",
        "fpm_bone_grouping_mode", "fpm_bone_hard_names", "fpm_part_tint",
        "fpm_node_type",
        "fpm_half_color",
        "fpm_svg_page", "fpm_svg_margin", "fpm_svg_pen",
        "fpm_svg_merge_tolerance", "fpm_svg_simplify", "fpm_svg_sort",
        "fpm_svg_depth_res", "fpm_svg_samples", "fpm_svg_bias",
        "fpm_svg_neighbourhood", "fpm_svg_keep_hidden",
        "fpm_svg_src_mecha", "fpm_svg_src_material", "fpm_svg_src_bone",
        "fpm_svg_src_open", "fpm_svg_src_silhouette", "fpm_svg_respect_paint",
        "fpm_svg_layers", "fpm_svg_outline_layer", "fpm_svg_outline_gap",
        "fpm_svg_depth_bands", "fpm_svg_depth_weight", "fpm_svg_hatch",
        "fpm_svg_hatch_spacing", "fpm_svg_hatch_levels",
        "fpm_svg_hatch_angle", "fpm_svg_hatch_threshold",
        "fpm_svg_jitter", "fpm_svg_jitter_scale", "fpm_svg_jitter_seed",
        "fpm_svg_tile_cols", "fpm_svg_tile_rows", "fpm_svg_tile_marks",
        "fpm_svg_fit", "fpm_svg_preview", "fpm_svg_plot_speed",
        "fpm_svg_travel_speed", "fpm_svg_pen_lift", "fpm_svg_last_result",
        "fpm_svg_split_files",
        "fpm_svg_src_freestyle", "fpm_svg_src_sharp", "fpm_svg_src_crease",
        "fpm_svg_crease_angle", "fpm_svg_home", "fpm_svg_layer_colors",
        "fpm_svg_outline_pen", "fpm_svg_hatch_pen", "fpm_svg_preview_auto"
    ]
    
    for prop_name in props_to_clear:
        if hasattr(scene, prop_name):
            try:
                delattr(scene, prop_name)
                logger.info(f"Cleared property: {prop_name}")
            except AttributeError:
                logger.exception(f"Failed to clear property: {prop_name}")
        else:
            logger.info(f"Property does not exist: {prop_name}")

    if hasattr(bpy.types.Object, "fpm_cam_render"):
        try:
            delattr(bpy.types.Object, "fpm_cam_render")
        except AttributeError:
            logger.exception("Failed to clear property: fpm_cam_render")
