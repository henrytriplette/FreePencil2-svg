"""UI panels for accessing FreePencil tools.

構成: 親パネル(FreePencil) + 折りたたみ可能なサブパネル
  STEP1 頂点カラー / STEP2 AOV / STEP3 ノード生成 /
  カメラ一括レンダリング / STEP4 手動塗り
"""

import bpy

from . import ADDON_VERSION
from . import compat

from .vertex_color import LINK_MAKE_OT_FP, FREEPENCIL_OT_randomize_seed
from .sample_node import LINK_MAKE_FP_OT_NODE
from .aov_node import LINK_MAKE_FP_OT_AOV_NODE
from .paint_vertex_color import LINK_MAKE_FP_OT_VCOLOR
from .half_fill import LINK_MAKE_FP_OT_HALF_FILL
from .render_cameras import FP_OT_RENDER_CAMERAS
from .auto_setup import FP_OT_AUTO_SETUP
from .svg_export import (FP_OT_EXPORT_SVG, FP_OT_EXPORT_SVG_CAMERAS,
                         FPM_OT_EXPORT_SVG_FRAMES,
                         FP_OT_SVG_PRESET, FP_OT_SVG_PREVIEW,
                         FP_OT_SVG_PREVIEW_CLEAR, VCOL_LAYER_MECHA)
from . import svg_export


class FP_PT_Line(bpy.types.Panel):
    """Main sidebar panel (parent of the collapsible sections)."""

    bl_label = f"FreePencil v{'.'.join(map(str, ADDON_VERSION))}"
    bl_idname = "FPM_PT_LINE"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "FreePencil SVG"

    def draw(self, context):
        # 4.2 は限定対応。レンダリングは動くがライブプレビューが出ないので、
        # 黙っていると「壊れている」と受け取られる。最初に伝える。
        if not compat.HAS_AOV_IN_VIEWPORT_COMPOSITOR:
            t = bpy.app.translations.pgettext
            box = self.layout.box()
            col = box.column(align=True)
            col.label(text=t("Limited support on this Blender"), icon="INFO")
            col.label(text=t("Render with F12. Live preview needs 4.3+."))


class _FPSub:
    """Mixin: common settings for FreePencil sub-panels.

    注意: 自動登録(toposort)は登録順を保証しないため、表示順は
    bl_order で明示的に固定する(登録順に依存すると毎回シャッフルされる)。
    """

    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "FreePencil SVG"
    bl_parent_id = "FPM_PT_LINE"
    # 初期状態は STEP0(全自動)だけ開く。STEP1〜5 は通常の手順では
    # 触らないので、全部開いていると縦に長くなり STEP0 が埋もれる。
    # DEFAULT_CLOSED が効くのは初回表示時だけで、以降はユーザーの
    # 開閉状態が .blend 側に保存される。
    bl_options = {"DEFAULT_CLOSED"}


def _painted_mesh_exists(context, cap: int = 200) -> bool:
    """mecha_color を持つメッシュがあるか。パネル描画は毎フレーム走るので
    見つかった時点で切り上げ、無いときも cap 個で打ち切る。"""
    for i, obj in enumerate(context.view_layer.objects):
        if i >= cap:
            break
        if obj.type == "MESH" and obj.data.color_attributes.get(
                VCOL_LAYER_MECHA) is not None:
            return True
    return False


class FP_PT_SvgExport(_FPSub, bpy.types.Panel):
    """本命の出口。塗り分けの境界をベクタのまま SVG にする。

    ラスタのコンポジタ経路(STEP2/3)は残してあるが、こちらはそれを通らない。
    Sobel の線は幅を持つ帯なので、追跡するとプロッタが輪郭を二重になぞる。

    毎回触るものだけをここに置き、線の出どころと詰めの設定は子パネルへ
    分けている(プロパティが増えて縦に長くなりすぎたため)。
    """

    bl_label = "SVG Export (pen plotter)"
    bl_idname = "FPM_PT_SVG"
    bl_order = -1
    bl_options = set()      # 主機能なので既定で開く

    def draw(self, context):
        t = bpy.app.translations.pgettext
        layout = self.layout
        scene = context.scene

        if scene.camera is None:
            layout.label(text=t("Set an active camera first"), icon="ERROR")
        elif not _painted_mesh_exists(context):
            layout.label(text=t("Run STEP0 or STEP1 first"), icon="INFO")

        layout.operator_menu_enum(FP_OT_SVG_PRESET.bl_idname, "preset",
                                  text=t("Preset"), icon="PRESET")

        row = layout.row(align=True)
        row.operator(FP_OT_SVG_PREVIEW.bl_idname,
                     text=t("Refresh preview"), icon="HIDE_OFF")
        if svg_export.preview_enabled():
            row.operator(FP_OT_SVG_PREVIEW_CLEAR.bl_idname,
                         text="", icon="X")
            info = svg_export.preview_info()
            if info:
                layout.label(text=f"{t('Preview')}: {info}", icon="INFO")

        col = layout.column(align=True)
        col.prop(scene, "fpm_svg_page", text=t("Page"))
        col.prop(scene, "fpm_svg_fit", text=t("Fit"))
        col.prop(scene, "fpm_svg_margin", text=t("Margin (mm)"))
        col.prop(scene, "fpm_svg_pen", text=t("Pen width (mm)"))

        col = layout.column(align=True)
        col.prop(scene, "fpm_svg_layers", text=t("Layers"))
        sub = col.row(align=True)
        sub.enabled = scene.fpm_svg_layers != "NONE"
        sub.prop(scene, "fpm_svg_split_files", text=t("One file per layer"))
        sub = col.column(align=True)
        sub.enabled = scene.fpm_svg_layers == "DEPTH"
        sub.prop(scene, "fpm_svg_depth_bands", text=t("Depth bands"))
        sub.prop(scene, "fpm_svg_depth_weight", text=t("Far line weight"))

        box = layout.box()
        box.prop(scene, "fpm_svg_hatch", text=t("Hatching"))
        col = box.column(align=True)
        col.enabled = scene.fpm_svg_hatch
        col.prop(scene, "fpm_svg_hatch_spacing", text=t("Hatch spacing (mm)"))
        col.prop(scene, "fpm_svg_hatch_levels", text=t("Hatch levels"))
        col.prop(scene, "fpm_svg_hatch_angle", text=t("Hatch angle"))
        col.prop(scene, "fpm_svg_hatch_threshold", text=t("Hatch threshold"))

        col = layout.column(align=True)
        col.label(text=t("Hand jitter"))
        col.prop(scene, "fpm_svg_jitter", text=t("Jitter (mm)"))
        sub = col.column(align=True)
        sub.enabled = scene.fpm_svg_jitter > 0.0
        sub.prop(scene, "fpm_svg_jitter_scale", text=t("Jitter scale (mm)"))
        sub.prop(scene, "fpm_svg_jitter_seed", text=t("Jitter seed"))

        layout.operator(FP_OT_EXPORT_SVG.bl_idname,
                        text=t("Export SVG"), icon="EXPORT")
        row = layout.row()
        row.enabled = bool(bpy.data.filepath)
        row.operator(FP_OT_EXPORT_SVG_CAMERAS.bl_idname,
                     text=t("Export checked cameras"), icon="RENDER_RESULT")
        row = layout.row()
        row.enabled = bool(bpy.data.filepath)
        row.operator(FPM_OT_EXPORT_SVG_FRAMES.bl_idname,
                     text=t("Export frame range"), icon="RENDER_ANIMATION")
        if not bpy.data.filepath:
            layout.label(text=t("Save the .blend to batch cameras"),
                         icon="INFO")

        if scene.fpm_svg_last_result:
            for line in scene.fpm_svg_last_result.split("|"):
                layout.label(text=line, icon="DOT")


class FP_PT_SvgSources(_FPSub, bpy.types.Panel):
    """どの辺を線にするか。"""

    bl_label = "Line sources"
    bl_idname = "FPM_PT_SVG_SOURCES"
    bl_parent_id = "FPM_PT_SVG"
    bl_order = 0

    def draw(self, context):
        t = bpy.app.translations.pgettext
        scene = context.scene
        col = self.layout.column(align=True)
        col.prop(scene, "fpm_svg_src_mecha", text=t("Color separation"))
        col.prop(scene, "fpm_svg_src_material", text=t("Material boundaries"))
        col.prop(scene, "fpm_svg_src_bone", text=t("Bone boundaries"))
        col.prop(scene, "fpm_svg_src_open", text=t("Open edges"))
        col.prop(scene, "fpm_svg_src_silhouette", text=t("Silhouette"))
        col.prop(scene, "fpm_svg_respect_paint", text=t("Honour STEP4 paint"))


class FP_PT_SvgAdvanced(_FPSub, bpy.types.Panel):
    """一度決めたら普段は触らない設定。"""

    bl_label = "Advanced"
    bl_idname = "FPM_PT_SVG_ADVANCED"
    bl_parent_id = "FPM_PT_SVG"
    bl_order = 1

    def draw(self, context):
        t = bpy.app.translations.pgettext
        layout = self.layout
        scene = context.scene

        col = layout.column(align=True)
        col.label(text=t("Paths"))
        col.prop(scene, "fpm_svg_merge_tolerance", text=t("Merge (mm)"))
        col.prop(scene, "fpm_svg_simplify", text=t("Simplify (mm)"))
        col.prop(scene, "fpm_svg_sort", text=t("Sort draw order"))

        col = layout.column(align=True)
        col.label(text=t("Outline layer"))
        col.enabled = scene.fpm_svg_layers == "SOURCE"
        col.prop(scene, "fpm_svg_outline_layer", text=t("Outline layer"))
        col.prop(scene, "fpm_svg_outline_gap", text=t("Outline depth step"))

        col = layout.column(align=True)
        col.label(text=t("Hidden line removal"))
        col.prop(scene, "fpm_svg_depth_res", text=t("Depth resolution"))
        col.prop(scene, "fpm_svg_samples", text=t("Samples per edge"))
        col.prop(scene, "fpm_svg_bias", text=t("Depth bias"))
        col.prop(scene, "fpm_svg_neighbourhood", text=t("Neighbourhood"))
        col.prop(scene, "fpm_svg_keep_hidden", text=t("Keep hidden lines"))

        col = layout.column(align=True)
        col.label(text=t("Plot estimate"))
        col.prop(scene, "fpm_svg_plot_speed", text=t("Pen down mm/s"))
        col.prop(scene, "fpm_svg_travel_speed", text=t("Travel mm/s"))
        col.prop(scene, "fpm_svg_pen_lift", text=t("Pen lift (s)"))


class FP_PT_Step0(_FPSub, bpy.types.Panel):
    bl_label = "STEP0: Full Auto"
    bl_idname = "FPM_PT_STEP0"
    bl_order = 0
    bl_options = set()  # ここだけ既定で開く

    def draw(self, context):
        t = bpy.app.translations.pgettext
        layout = self.layout
        scene = context.scene
        col = layout.column(align=True)
        col.label(text=t("Recommended settings for this scene"), icon="INFO")
        col = layout.column(align=True)
        col.prop(scene, "fpm_auto_sharp", text=t("Auto edge angle"))
        col.prop(scene, "fpm_auto_seam", text=t("Seam/material boundaries"))
        col.prop(scene, "fpm_auto_merge", text=t("Merge small islands"))
        col.prop(scene, "fpm_auto_part_tint", text=t("Part tint"))
        col.prop(scene, "fpm_auto_bone", text=t("Bone AOV by rig detection"))
        col.prop(scene, "fpm_auto_aa", text=t("Anti-aliasing"))
        col.prop(scene, "fpm_auto_supersample",
                 text=t("2x supersampling (thin lines)"))
        col.prop(scene, "fpm_auto_hashed", text=t("BLEND to HASHED (keep glass)"))
        col.prop(scene, "fpm_auto_detect_aov", text=t("Auto AOVs from scene"))
        col.prop(scene, "fpm_auto_white_preview",
                 text=t("White material preview"))
        col.prop(scene, "fpm_auto_file_output", text=t("Enable File Output"))
        layout.operator(FP_OT_AUTO_SETUP.bl_idname,
                        text=t("Auto setup (STEP1-3)"), icon="AUTO")


class FP_PT_Step1(_FPSub, bpy.types.Panel):
    bl_label = "STEP1: Auto Vertex Color"
    bl_idname = "FPM_PT_STEP1"
    bl_order = 1

    def draw(self, context):
        t = bpy.app.translations.pgettext
        layout = self.layout
        scene = context.scene

        col = layout.column(align=True)
        col.prop(scene, "fpm_mat_count", text=t("Add the material ID"))

        # --- 島分割 ---
        box = layout.box()
        box.label(text=t("Island split:"), icon="MOD_EDGESPLIT")
        col = box.column(align=True)
        col.prop(scene, "fpm_sharp_auto", text=t("Auto edge angle"))
        row = col.row(align=True)
        row.enabled = not scene.fpm_sharp_auto
        row.prop(scene, "fpm_sharp_edges", slider=True, text=t("Edge Angle"))
        col.prop(scene, "fpm_seam_boundaries", text=t("Seam/material boundaries"))
        col.prop(scene, "fpm_min_island_area_pct", text=t("Min island area %"))

        # --- ボーン(キャラ用) ---
        box = layout.box()
        box.label(text=t("Bone options:"), icon="BONE_DATA")
        col = box.column(align=True)
        col.prop(scene, "fpm_bone_grouping_mode", text=t("Bone grouping"))
        col.prop(scene, "fpm_bone_hard_names", text=t("Hard boundary bones"))
        col.prop(scene, "fpm_part_tint", text=t("Part tint (mecha color)"))

        # --- 配色シード(再現性) ---
        box = layout.box()
        box.label(text=t("Color seed:"), icon="FILE_REFRESH")
        col = box.column(align=True)
        col.prop(scene, "fpm_use_random_seed", text=t("Random seed each run"))
        row = col.row(align=True)
        row.enabled = not scene.fpm_use_random_seed
        row.prop(scene, "fpm_color_seed", text=t("Seed"))
        row.operator(FREEPENCIL_OT_randomize_seed.bl_idname,
                     text="", icon="FILE_REFRESH")

        layout.operator(LINK_MAKE_OT_FP.bl_idname,
                        text=t("Separate Paint Vertex Colors"),
                        icon="MESH_CUBE")


class FP_PT_Step2(_FPSub, bpy.types.Panel):
    bl_label = "STEP2: AOV"
    bl_idname = "FPM_PT_STEP2"
    bl_order = 2

    def draw(self, context):
        t = bpy.app.translations.pgettext
        layout = self.layout
        scene = context.scene

        col = layout.column(align=True)
        col.prop(scene, "fpm_bone_color", text=t("AOV Bone Color"))
        col.prop(scene, "fpm_gen_color", text=t("AOV Generate Color"))
        col.prop(scene, "fpm_mask_color",
                 text=t("AOV Mask Color(paint to erase lines)"))
        col.prop(scene, "fpm_line_color",
                 text=t("AOV Line Color(line darkness)"))
        col.prop(scene, "fpm_mat_color", text=t("AOV Material Boundary Color"))
        layout.operator(LINK_MAKE_FP_OT_AOV_NODE.bl_idname,
                        text=t("Generate AOV Node"), icon="NODETREE")


class FP_PT_Step3(_FPSub, bpy.types.Panel):
    bl_label = "STEP3: Node Generation"
    bl_idname = "FPM_PT_STEP3"
    bl_order = 3

    def draw(self, context):
        t = bpy.app.translations.pgettext
        layout = self.layout
        scene = context.scene

        col = layout.column(align=True)
        col.prop(scene, "fpm_node_type", text=t("Select Node Type"))
        # 4.2 のビューポートコンポジタは AOV を評価しないので、ONにしても
        # プレビューは出ない。触れるままにすると誤解を招くため無効化する
        row = col.row(align=True)
        row.enabled = compat.HAS_AOV_IN_VIEWPORT_COMPOSITOR
        row.prop(scene, "fpm_enable_compositor_view",
                 text=t("Enable Compositor Preview"))
        if not compat.HAS_AOV_IN_VIEWPORT_COMPOSITOR:
            col.label(text=t("Live preview needs Blender 4.3+"), icon="INFO")
        col.prop(scene, "fpm_white_preview",
                 text=t("White material preview"), icon="MATERIAL",
                 toggle=True)
        col.prop(scene, "fpm_include_antialiasing",
                 text=t("Include Anti-Aliasing Node"))
        col.prop(scene, "fpm_supersample",
                 text=t("2x supersampling (thin lines)"))
        col.prop(scene, "fpm_line_sensitivity", text=t("Line sensitivity"))

        # 遠景で線が黒ベタにつぶれるのを軽減する(0 で無効=画は変わらない)
        box = layout.box()
        box.label(text=t("Far crush relief:"), icon="MOD_SMOOTH")
        col = box.column(align=True)
        col.prop(scene, "fpm_far_relief", text=t("Amount"), slider=True)
        sub = col.column(align=True)
        sub.enabled = scene.fpm_far_relief > 0.0
        sub.prop(scene, "fpm_far_relief_radius", text=t("Radius (px)"))
        sub.prop(scene, "fpm_far_relief_threshold", text=t("Threshold"),
                 slider=True)

        # チャンネル別の線の強さ(生成済みノードへ即時反映)
        box = layout.box()
        box.label(text=t("Line strength per channel:"), icon="MOD_LINEART")
        col = box.column(align=True)
        col.prop(scene, "fpm_ch_depth", text=t("Depth"), slider=True)
        col.prop(scene, "fpm_ch_mecha", text=t("Mecha"), slider=True)
        col.prop(scene, "fpm_ch_bone", text=t("Bone"), slider=True)
        col.prop(scene, "fpm_ch_mat", text=t("Material"), slider=True)
        col.prop(scene, "fpm_ch_gen", text=t("Generate"), slider=True)

        box = layout.box()
        box.label(text=t("File Output"), icon="FILE_FOLDER")
        col = box.column(align=True)
        col.prop(scene, "fpm_file_output", text=t("Enable File Output"))

        sub = col.column(align=True)
        sub.enabled = scene.fpm_file_output
        sub.prop(scene, "fpm_file_output_path", text=t("Output path"))
        # 書き出すパスを個別に選ぶ。チェック名がそのままファイル名になる
        sub.label(text=t("Passes to write:"))
        grid = sub.grid_flow(columns=2, align=True)
        grid.prop(scene, "fpm_fo_line", text="line")
        grid.prop(scene, "fpm_fo_color", text="color")
        grid.prop(scene, "fpm_fo_light", text=t("light (diffuse)"))
        grid.prop(scene, "fpm_fo_shadow", text=t("shadow"))
        if scene.fpm_file_output and not any(
            getattr(scene, name)
            for name in ("fpm_fo_line", "fpm_fo_color",
                         "fpm_fo_light", "fpm_fo_shadow")
        ):
            col.label(text=t("No pass selected"), icon="ERROR")

        layout.operator(LINK_MAKE_FP_OT_NODE.bl_idname,
                        text=t("Generate Sample Node"), icon="NODETREE")


class FP_PT_Cameras(_FPSub, bpy.types.Panel):
    bl_label = "STEP5: Camera Batch Render"
    bl_idname = "FPM_PT_CAMERAS"
    bl_order = 5

    def draw(self, context):
        t = bpy.app.translations.pgettext
        layout = self.layout
        scene = context.scene

        cams = sorted((o for o in scene.objects if o.type == "CAMERA"),
                      key=lambda o: o.name.lower())
        if not cams:
            layout.label(text=t("No cameras in scene"), icon="INFO")
            return
        col = layout.column(align=True)
        for cam in cams:
            row = col.row(align=True)
            row.prop(cam, "fpm_cam_render", text="")
            icon = ("OUTLINER_OB_CAMERA" if cam == scene.camera
                    else "CAMERA_DATA")
            row.label(text=cam.name, icon=icon)
        layout.operator(FP_OT_RENDER_CAMERAS.bl_idname,
                        text=t("Render checked cameras"), icon="RENDER_STILL")


class FP_PT_Step4(_FPSub, bpy.types.Panel):
    bl_label = "STEP4: Manual Vertex Color"
    bl_idname = "FPM_PT_STEP4"
    bl_order = 4

    def draw(self, context):
        t = bpy.app.translations.pgettext
        layout = self.layout
        scene = context.scene

        layout.prop(scene, "fpm_color_type", text=t("Vertex Color Type"))
        layout.operator(LINK_MAKE_FP_OT_VCOLOR.bl_idname,
                        text=t("Paint Vertex Color"), icon="VPAINT_HLT")

        box = layout.box()
        box.label(text=t("Vertex-color options:"), icon="COLOR")
        col = box.column(align=True)
        col.prop(scene, "fpm_color_noise_scale",
                 text=t("Color noise scale"), slider=True)
        col.prop(scene, "fpm_min_neighbor_color_distance",
                 text=t("Min color distance"))
        col.prop(scene, "fpm_max_color_retries", text=t("Max color retries"))

        row = layout.row(align=True)
        row.prop(scene, "fpm_half_color", text="")
        row.operator(LINK_MAKE_FP_OT_HALF_FILL.bl_idname,
                     text=t("Half Fill"), icon="BRUSH_DATA")


class FPM_PT_CompositorOptions(bpy.types.Panel):
    """Panel for FreePencil options in the Compositor node editor."""

    bl_label = "FreePencil"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "FreePencil SVG"

    @classmethod
    def poll(cls, context):
        space = context.space_data
        return bool(space and space.tree_type == "CompositorNodeTree")

    def draw(self, context):
        layout = self.layout
        layout.prop(
            context.scene,
            "fpm_include_antialiasing",
            text=bpy.app.translations.pgettext("Include Anti-Aliasing Node"),
        )
