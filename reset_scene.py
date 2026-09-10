"""Take everything FreePencil wrote back out of the scene.

STEP0〜STEP3 はマテリアル・ビューレイヤー・コンポジタ・ビューポートに
書き込む。試したあとで元に戻したいときに一つずつ手で外して回るのは
現実的ではないので、まとめて外す。

見た目の控え(fp_core.capture_state)は STEP2/STEP3 が最初に走ったときに
取ってある。ビューポートの表示設定だけはここが持つ — fp_core は
ヘッドレスで動く必要があり、context.screen を触らない約束のため。
"""

import bpy

from . import fp_core
from . import svg_export

VIEW_STATE_KEY = "views"


def capture_view_state(scene, screen) -> bool:
    """3Dビューの表示設定を控える。最初の一回だけ効く。"""
    if screen is None:
        return False
    state = fp_core.load_state(scene)
    if VIEW_STATE_KEY in state:
        return False
    views = []
    for area in screen.areas:
        if area.type != 'VIEW_3D':
            continue
        shading = area.spaces[0].shading
        views.append({
            "type": shading.type,
            "render_pass": getattr(shading, "render_pass", "COMBINED"),
            "use_compositor": getattr(shading, "use_compositor", None),
        })
    state[VIEW_STATE_KEY] = views
    fp_core.save_state(scene, state)
    return True


def restore_view_state(scene, screen) -> int:
    """控えた表示設定を書き戻す。戻せたビューの数を返す。

    エリアは並べ替えられている可能性があるので、控えた順に上から当てる。
    数が合わなければ合うぶんだけ。
    """
    if screen is None:
        return 0
    views = fp_core.load_state(scene).get(VIEW_STATE_KEY)
    if not views:
        return 0
    areas = [a for a in screen.areas if a.type == 'VIEW_3D']
    done = 0
    for area, saved in zip(areas, views):
        shading = area.spaces[0].shading
        # 消したばかりの AOV を指したまま戻すことはない(控えは STEP2 が
        # 走る前の値)が、版をまたいだ .blend では通らないことがある
        for attr in ("type", "render_pass", "use_compositor"):
            value = saved.get(attr)
            if value is None or not hasattr(shading, attr):
                continue
            try:
                setattr(shading, attr, value)
            except TypeError:
                pass
        done += 1
    return done


class FP_OT_RESET(bpy.types.Operator):
    """Remove the add-on's nodes and put the scene's look back."""

    bl_idname = "fpm.reset_scene"
    bl_label = "Reset scene"
    bl_description = (
        "Remove the AOV group from every material, the compositor nodes "
        "FreePencil built, its AOV slots and the vertex colours it "
        "painted, then put the render and viewport settings back to what "
        "they were before STEP2. Your own compositor nodes are left alone"
    )
    bl_options = {'REGISTER', 'UNDO'}

    def invoke(self, context, event):
        # 頂点カラーまで消える = STEP1 からやり直しになる。押し間違いで
        # 走らせない
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        scene = context.scene
        screen = getattr(context, "screen", None)

        # 3Dビューに残っている線も、もう当てにならない
        svg_export.disable_preview()
        if getattr(scene, "fpm_svg_preview", False):
            scene.fpm_svg_preview = False

        info = fp_core.teardown(scene, context.view_layer)
        restored_views = restore_view_state(scene, screen)
        fp_core.clear_state(scene)

        t = bpy.app.translations.pgettext
        summary = (f"{t('Reset')}: "
                   f"{info['materials']} {t('materials')}, "
                   f"{info['nodes']} {t('nodes')}, "
                   f"{info['aovs']} AOV, "
                   f"{info['vcols']} {t('vertex colours')}")
        if not info["restored"] and not restored_views:
            # 控えが無い .blend(このアドオンを入れる前から在るツリーなど)
            summary += f"  |  {t('no saved state, settings left as they are')}"
        print(f"[freepencil.reset] {summary}")
        self.report({'INFO'}, summary)
        return {'FINISHED'}
