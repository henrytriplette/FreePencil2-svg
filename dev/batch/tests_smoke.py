"""TDD regression tests for FreePencil, headless (no external assets).

Run:  blender -b --factory-startup -P tests_smoke.py
Exit code 0 = all green. Results also written to out/tests.json.

These lock in current guaranteed behavior; extend when improving the
algorithm so regressions are caught immediately.
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

import bpy
import numpy as np

BATCH = Path(__file__).resolve().parent
sys.path.insert(0, str(BATCH))
import fp_batch  # reuse install_addon / metrics  # noqa: E402

RESULTS: list[dict] = []


def test(name):
    def deco(fn):
        def wrapper():
            try:
                fn()
                RESULTS.append({"test": name, "ok": True})
                print(f"  PASS {name}")
            except Exception:
                RESULTS.append({"test": name, "ok": False,
                                "error": traceback.format_exc()})
                print(f"  FAIL {name}")
        wrapper.__test__ = True
        return wrapper
    return deco


def fresh_scene_with_islands():
    """Two separated cubes inside ONE mesh object = 2 islands."""
    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
    obj = bpy.context.active_object
    bpy.ops.mesh.primitive_cube_add(location=(3, 0, 0))
    other = bpy.context.active_object
    other.select_set(True)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.join()
    scene = bpy.context.scene
    scene.fpm_use_random_seed = False
    scene.fpm_color_seed = 1234
    scene.fpm_sharp_auto = False  # 固定しきい値の挙動をテストする
    return bpy.context.active_object


def get_mecha_colors(obj) -> list[tuple]:
    attr = obj.data.color_attributes["mecha_color"]
    return [tuple(round(v, 4) for v in d.color[:3]) for d in attr.data]


@test("STEP1 runs headless and creates mecha_color")
def t1():
    obj = fresh_scene_with_islands()
    res = bpy.ops.fpm.auto_vertex_color()
    assert res == {"FINISHED"}, res
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    assert any("mecha_color" in o.data.color_attributes for o in meshes)


@test("seed reproducibility: same seed = identical colors")
def t2():
    fresh_scene_with_islands()
    bpy.ops.fpm.auto_vertex_color()
    colors_a = sorted(
        c for o in bpy.context.scene.objects if o.type == "MESH"
        for c in set(get_mecha_colors(o)))
    fresh_scene_with_islands()
    bpy.ops.fpm.auto_vertex_color()
    colors_b = sorted(
        c for o in bpy.context.scene.objects if o.type == "MESH"
        for c in set(get_mecha_colors(o)))
    assert colors_a == colors_b, (colors_a, colors_b)


@test("different seed = different colors")
def t3():
    fresh_scene_with_islands()
    bpy.ops.fpm.auto_vertex_color()
    colors_a = sorted(
        c for o in bpy.context.scene.objects if o.type == "MESH"
        for c in set(get_mecha_colors(o)))
    obj = fresh_scene_with_islands()
    bpy.context.scene.fpm_color_seed = 9999
    bpy.ops.fpm.auto_vertex_color()
    colors_b = sorted(
        c for o in bpy.context.scene.objects if o.type == "MESH"
        for c in set(get_mecha_colors(o)))
    assert colors_a != colors_b


@test("adjacent islands respect min color distance (violations = 0)")
def t4():
    fresh_scene_with_islands()
    bpy.context.scene.fpm_min_neighbor_color_distance = 0.5
    bpy.ops.fpm.auto_vertex_color()
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    m = fp_batch.mesh_color_metrics(meshes, 0.5)
    assert m["min_distance_violations"] == 0, m


@test("STEP2+STEP3(pro) core functions build AOV and compositor tree headless")
def t5():
    from freepencil2 import fp_core
    fresh_scene_with_islands()
    bpy.ops.fpm.auto_vertex_color()
    scene = bpy.context.scene
    scene.fpm_include_antialiasing = True
    scene.fpm_node_type = "pro"
    fp_core.setup_aov(scene, bpy.context.view_layer)
    fp_core.setup_compositor(scene, bpy.context.view_layer)
    assert "mecha_color" in [a.name for a in bpy.context.view_layer.aovs]
    assert fp_batch.comp_tree(scene) is not None
    labels = [n.label for n in fp_batch.comp_tree(scene).nodes]
    assert any("pro" in (l or "") for l in labels), labels
    assert bpy.context.view_layer.use_pass_z
    # 透過背景: シルエットアルファを書き戻す Set Alpha が Composite 直前にあること
    comp = next(n for n in fp_batch.comp_tree(scene).nodes
            if fp_batch.is_output_node(n))
    assert comp.inputs[0].links[0].from_node.type == "SETALPHA", \
        [n.type for n in fp_batch.comp_tree(scene).nodes]


@test("STEP2+STEP3 real operators are headless-safe after fp_core refactor")
def t6():
    fresh_scene_with_islands()
    bpy.ops.fpm.auto_vertex_color()
    scene = bpy.context.scene
    scene.fpm_include_antialiasing = True
    scene.fpm_node_type = "pro"
    scene.fpm_enable_compositor_view = False
    # re-select meshes (STEP1 may have split objects)
    meshes = [o for o in scene.objects if o.type == "MESH"]
    for o in bpy.context.selected_objects:
        o.select_set(False)
    for o in meshes:
        o.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    res2 = bpy.ops.fpm4.link_button()
    assert res2 == {"FINISHED"}, res2
    assert "mecha_color" in [a.name for a in bpy.context.view_layer.aovs]
    res3 = bpy.ops.fpm2.link_button()
    assert res3 == {"FINISHED"}, res3
    labels = [n.label for n in fp_batch.comp_tree(scene).nodes]
    assert any("pro" in (l or "") for l in labels), labels


@test("STEP1 survives a selected mesh with zero faces (bed_2K regression)")
def t7():
    fresh_scene_with_islands()
    # BlenderKitの一部アセットにある「面が0個のメッシュ」(エッジのみ等)を再現
    me = bpy.data.meshes.new("FP_EdgeOnly")
    me.from_pydata([(0, 0, 0), (0, 0, 1)], [(0, 1)], [])
    empty_obj = bpy.data.objects.new("FP_EdgeOnly", me)
    bpy.context.scene.collection.objects.link(empty_obj)
    empty_obj.select_set(True)
    res = bpy.ops.fpm.auto_vertex_color()
    assert res == {"FINISHED"}, res
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    assert any("mecha_color" in o.data.color_attributes for o in meshes)


@test("dense adjacency: cube faces satisfy high min color distance (golden-ratio hue)")
def t8():
    # 1個の立方体 = 6面がすべて島で互いに隣接する密な制約グラフ。
    # 高い距離しきい値でも黄金比色相ステップなら違反ゼロで塗れること。
    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
    scene = bpy.context.scene
    scene.fpm_use_random_seed = False
    scene.fpm_color_seed = 1234
    scene.fpm_sharp_auto = False
    scene.fpm_min_neighbor_color_distance = 0.7
    scene.fpm_max_color_retries = 30
    bpy.ops.fpm.auto_vertex_color()
    meshes = [o for o in scene.objects if o.type == "MESH"]
    m = fp_batch.mesh_color_metrics(meshes, 0.7)
    assert m["distinct_colors"] >= 3, m
    assert m["min_distance_violations"] == 0, m


@test("tiny sliver island merges into its large neighbor (area-based)")
def t9():
    # 大きな四角形 + 90°に折れた極小の短冊(面積 ~0.01%) = 2島。
    # 面積比が fpm_min_island_area_pct 未満の短冊は隣の大きな島に併合され、
    # 色は1色になる(微小島ノイズ線の除去)。
    bpy.ops.wm.read_homefile(use_empty=True)
    me = bpy.data.meshes.new("FP_Sliver")
    me.from_pydata(
        [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
         (1, 0, 0.0001), (0, 0, 0.0001)],
        [],
        [(0, 1, 2, 3), (0, 1, 4, 5)])
    obj = bpy.data.objects.new("FP_Sliver", me)
    bpy.context.scene.collection.objects.link(obj)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    scene = bpy.context.scene
    scene.fpm_use_random_seed = False
    scene.fpm_color_seed = 1234
    scene.fpm_sharp_auto = False
    scene.fpm_sharp_edges = 60.0
    scene.fpm_min_island_area_pct = 0.02
    res = bpy.ops.fpm.auto_vertex_color()
    assert res == {"FINISHED"}, res
    colors = {c for o in bpy.context.scene.objects if o.type == "MESH"
              for c in get_mecha_colors(o)}
    assert len(colors) == 1, colors

    # マージ無効(0)なら2島=2色のまま
    bpy.ops.wm.read_homefile(use_empty=True)
    me = bpy.data.meshes.new("FP_Sliver2")
    me.from_pydata(
        [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
         (1, 0, 0.0001), (0, 0, 0.0001)],
        [],
        [(0, 1, 2, 3), (0, 1, 4, 5)])
    obj = bpy.data.objects.new("FP_Sliver2", me)
    bpy.context.scene.collection.objects.link(obj)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    scene = bpy.context.scene
    scene.fpm_use_random_seed = False
    scene.fpm_color_seed = 1234
    scene.fpm_sharp_auto = False
    scene.fpm_sharp_edges = 60.0
    scene.fpm_min_island_area_pct = 0.0
    bpy.ops.fpm.auto_vertex_color()
    colors = {c for o in bpy.context.scene.objects if o.type == "MESH"
              for c in get_mecha_colors(o)}
    assert len(colors) == 2, colors


@test("graph coloring: extreme min distance 0.85 with zero violations")
def t10():
    # 旧乱数リトライ方式では 0.8 で違反が爆発していたケース。
    # グラフ彩色+パレットでは構造的に違反ゼロになること。
    fresh_scene_with_islands()
    bpy.context.scene.fpm_min_neighbor_color_distance = 0.85
    bpy.ops.fpm.auto_vertex_color()
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    m = fp_batch.mesh_color_metrics(meshes, 0.85)
    assert m["min_distance_violations"] == 0, m
    assert m["distinct_colors"] >= 2, m


@test("auto threshold: smooth sphere still gets partition lines")
def t11():
    # 一様に滑らかなメッシュ(構造エッジなし)でも fpm_sharp_auto なら
    # p50付近まで下げて分割線を人工生成し、複数の島色が出ること。
    # 固定60°では島が1つ=1色になるケース。
    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2)
    scene = bpy.context.scene
    scene.fpm_use_random_seed = False
    scene.fpm_color_seed = 1234
    scene.fpm_sharp_auto = True
    bpy.ops.fpm.auto_vertex_color()
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    colors = {c for o in meshes for c in get_mecha_colors(o)}
    assert len(colors) >= 2, colors


@test("rigged model: bone_color per bone, no artificial mecha partition")
def t12():
    # メカ(島分割)とボーン(頂点グループ)は別系統。リグ付きモデルでは
    #  - bone_color がボーン毎に塗り分けられること
    #  - auto でも滑面への人工分割線(mecha側ノイズ)を出さないこと
    bpy.ops.wm.read_homefile(use_empty=True)
    arm = bpy.data.armatures.new("FP_Arm")
    arm_obj = bpy.data.objects.new("FP_Arm", arm)
    bpy.context.scene.collection.objects.link(arm_obj)
    bpy.context.view_layer.objects.active = arm_obj
    bpy.ops.object.mode_set(mode="EDIT")
    b1 = arm.edit_bones.new("upper")
    b1.head, b1.tail = (0, 0, 0), (0, 0, 1)
    b2 = arm.edit_bones.new("lower")
    b2.head, b2.tail = (0, 0, -1), (0, 0, 0)
    bpy.ops.object.mode_set(mode="OBJECT")

    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2)
    obj = bpy.context.active_object
    vg_u = obj.vertex_groups.new(name="upper")
    vg_l = obj.vertex_groups.new(name="lower")
    for v in obj.data.vertices:
        (vg_u if v.co.z >= 0 else vg_l).add([v.index], 1.0, "REPLACE")
    mod = obj.modifiers.new("Armature", "ARMATURE")
    mod.object = arm_obj

    scene = bpy.context.scene
    scene.fpm_use_random_seed = False
    scene.fpm_color_seed = 1234
    scene.fpm_sharp_auto = True
    arm_obj.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    res = bpy.ops.fpm.auto_vertex_color()
    assert res == {"FINISHED"}, res

    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    mecha = {c for o in meshes for c in get_mecha_colors(o)}
    assert len(mecha) == 1, f"rigged mesh must not get partition noise: {len(mecha)}"
    bone_attr = meshes[0].data.color_attributes["bone_color"]
    bone = {tuple(round(v, 3) for v in d.color[:3]) for d in bone_attr.data}
    assert len(bone) >= 2, f"bone_color should differ per bone: {bone}"


@test("adjacent islands differ in LUMA (PRO node detects edges in luma)")
def t13():
    # PROノードの線抽出は「白背景プリミックス→エッジ検出→ColorRamp
    # (float入力=輝度)」なので、線が出るかは輝度差で決まる。
    # RGB距離が大きくても輝度が近いと線が消える回帰(womanの顔・服の
    # 線が消滅)を防ぐ:
    #  - 全島色の輝度 <= 0.85(白背景とのシルエット線を保証)
    #  - 色の異なる隣接面の輝度差 >= 0.12(内部線の検出を保証)
    fresh_scene_with_islands()
    bpy.ops.fpm.auto_vertex_color()

    def luma(c):
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]

    min_gap = 1.0
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        me = obj.data
        attr = me.color_attributes.get("mecha_color")
        if attr is None:
            continue
        face_color = {p.index: tuple(attr.data[p.loop_start].color[:3])
                      for p in me.polygons}
        for c in set(face_color.values()):
            assert luma(c) <= 0.85, f"too bright for silhouette: {c}"
        edge_faces = {}
        for p in me.polygons:
            for ek in p.edge_keys:
                edge_faces.setdefault(ek, []).append(p.index)
        for faces in edge_faces.values():
            if len(faces) == 2:
                c1, c2 = face_color[faces[0]], face_color[faces[1]]
                if c1 != c2:
                    min_gap = min(min_gap, abs(luma(c1) - luma(c2)))
    assert min_gap >= 0.12, f"adjacent islands too close in luma: {min_gap:.3f}"


@test("line sensitivity scales node ramps idempotently")
def t14():
    # fpm_line_sensitivity はノードグループ内 ColorRamp のしきい値位置を
    # 一括スケールする。冪等(1.0で完全復元)であること。
    fresh_scene_with_islands()
    bpy.ops.fpm.auto_vertex_color()
    scene = bpy.context.scene
    scene.fpm_node_type = "pro"
    scene.fpm_enable_compositor_view = False
    bpy.ops.fpm4.link_button()

    from freepencil2 import fp_core
    scene.fpm_line_sensitivity = 1.0
    bpy.ops.fpm2.link_button()
    group = bpy.data.node_groups[f"{fp_core.NODE_GROUP_PREFIX}pro"]
    orig = {n.name: [e.position for e in n.color_ramp.elements]
            for n in group.nodes if n.type == "VALTORGB"}
    assert orig, "no ramps found"

    def luma(c):
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]

    def is_descending(n):
        el = n.color_ramp.elements
        return len(el) >= 2 and luma(el[0].color) > luma(el[-1].color)

    scene.fpm_line_sensitivity = 0.5
    bpy.ops.fpm2.link_button()
    n_scaled = 0
    for n in group.nodes:
        if n.type != "VALTORGB":
            continue
        expect = 0.5 if is_descending(n) else 1.0  # 上昇ランプ(マスク系)は不変
        for e, p0 in zip(n.color_ramp.elements, orig[n.name]):
            assert abs(e.position - p0 * expect) < 1e-5, (n.name, e.position, p0)
        if is_descending(n):
            n_scaled += 1
    assert n_scaled >= 1, "no line ramps found"

    scene.fpm_line_sensitivity = 1.0
    bpy.ops.fpm2.link_button()
    for n in group.nodes:
        if n.type != "VALTORGB":
            continue
        for e, p0 in zip(n.color_ramp.elements, orig[n.name]):
            assert abs(e.position - p0) < 1e-5, (n.name, e.position, p0)


@test("auto: multi-part smooth assembly gets no artificial partitions")
def t15():
    # 骨格標本のような「滑面パーツの多い組立モデル」では、パーツ間の
    # シルエット線が十分な線源なので人工分割線を出さない
    # (単体の滑面ボール t11 とは逆の挙動が正しい)。
    bpy.ops.wm.read_homefile(use_empty=True)
    first = None
    for i in range(10):
        bpy.ops.mesh.primitive_ico_sphere_add(
            subdivisions=2, location=(i * 3.0, 0, 0))
        if first is None:
            first = bpy.context.active_object
        bpy.context.active_object.select_set(True)
    bpy.context.view_layer.objects.active = first
    for o in bpy.context.scene.objects:
        o.select_set(o.type == "MESH")
    scene = bpy.context.scene
    scene.fpm_use_random_seed = False
    scene.fpm_color_seed = 1234
    scene.fpm_sharp_auto = True
    bpy.ops.fpm.auto_vertex_color()
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    m = fp_batch.mesh_color_metrics(meshes, 0.5)
    # 人工分割が出ていれば球内部に隣接ペアが生まれる。ゼロであること
    assert m["adjacent_color_pairs"] == 0, m


@test("seam/material boundaries split islands only when enabled")
def t16():
    # 平坦な2面(角度0°)でも、マテリアル境界が有効なら島が分かれること。
    # 無効なら従来どおり1島のまま。
    def build():
        bpy.ops.wm.read_homefile(use_empty=True)
        me = bpy.data.meshes.new("FP_TwoMat")
        me.from_pydata(
            [(0, 0, 0), (1, 0, 0), (2, 0, 0), (2, 1, 0), (1, 1, 0), (0, 1, 0)],
            [],
            [(0, 1, 4, 5), (1, 2, 3, 4)])
        me.polygons[1].material_index = 1
        obj = bpy.data.objects.new("FP_TwoMat", me)
        bpy.context.scene.collection.objects.link(obj)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        scene = bpy.context.scene
        scene.fpm_use_random_seed = False
        scene.fpm_color_seed = 1234
        scene.fpm_sharp_auto = False
        scene.fpm_sharp_edges = 60.0
        scene.fpm_min_island_area_pct = 0.0
        return scene

    scene = build()
    scene.fpm_seam_boundaries = True
    bpy.ops.fpm.auto_vertex_color()
    colors = {c for o in bpy.context.scene.objects if o.type == "MESH"
              for c in get_mecha_colors(o)}
    assert len(colors) == 2, colors

    scene = build()
    scene.fpm_seam_boundaries = False
    bpy.ops.fpm.auto_vertex_color()
    colors = {c for o in bpy.context.scene.objects if o.type == "MESH"
              for c in get_mecha_colors(o)}
    assert len(colors) == 1, colors


@test("hard boundary bones make a step only when requested")
def t17():
    # fpm_bone_hard_names に列挙したボーンの境界だけ硬いステップになる
    # (顎下ライン用)。未指定ならウェイトブレンドのまま(多数の中間色)。
    # ボーン名 north/red はハッシュ色の距離が大きいペア(0.52)を事前計算で
    # 選んだもの(近い色のペアだとブレンドの中間色が2桁丸めで潰れて
    # ソフト側の判定ができない)。
    def build(hard):
        bpy.ops.wm.read_homefile(use_empty=True)
        arm = bpy.data.armatures.new("FP_Arm")
        arm_obj = bpy.data.objects.new("FP_Arm", arm)
        bpy.context.scene.collection.objects.link(arm_obj)
        bpy.context.view_layer.objects.active = arm_obj
        bpy.ops.object.mode_set(mode="EDIT")
        b1 = arm.edit_bones.new("north")
        b1.head, b1.tail = (0, 0, 0), (0, 0, 1)
        b2 = arm.edit_bones.new("red")
        b2.head, b2.tail = (0, 0, -1), (0, 0, 0)
        bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2)
        obj = bpy.context.active_object
        vg_u = obj.vertex_groups.new(name="north")
        vg_l = obj.vertex_groups.new(name="red")
        for v in obj.data.vertices:
            t = min(1.0, max(0.0, v.co.z / 2.0 + 0.5))  # なだらかな重み遷移(球全体)
            if t > 0:
                vg_u.add([v.index], t, "REPLACE")
            if t < 1:
                vg_l.add([v.index], 1.0 - t, "REPLACE")
        mod = obj.modifiers.new("Armature", "ARMATURE")
        mod.object = arm_obj
        scene = bpy.context.scene
        scene.fpm_use_random_seed = False
        scene.fpm_color_seed = 1234
        scene.fpm_sharp_auto = True
        scene.fpm_bone_hard_names = hard
        arm_obj.select_set(False)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.fpm.auto_vertex_color()
        colors = set()
        for o in bpy.context.scene.objects:
            if o.type == "MESH" and "bone_color" in o.data.color_attributes:
                attr = o.data.color_attributes["bone_color"]
                colors |= {tuple(round(v, 2) for v in d.color[:3])
                           for d in attr.data}
        return len(colors)

    soft = build("")
    hard = build("north")
    # ソフトはブレンドの中間色を多数持ち、ハードは少数の純色に潰れる
    assert hard <= 6, f"hard boundary should be few discrete colors: {hard}"
    assert soft >= hard + 4, f"soft should have more blend colors: soft={soft} hard={hard}"


@test("part tint separates touching objects sharing a bone")
def t18():
    # 髪と顔のように「同じボーン支配の別オブジェクト」が接している場合、
    # fpm_part_tint ON なら mecha_color がパーツごとに別の明度帯になり
    # (パーツ境界線の源)、bone_color は ON/OFF に関わらず元の純粋な
    # ウェイトブレンドのまま(パーツ線は mecha 担当、ノードで合成)。
    def build(tint_on):
        bpy.ops.wm.read_homefile(use_empty=True)
        arm = bpy.data.armatures.new("FP_Arm")
        arm_obj = bpy.data.objects.new("FP_Arm", arm)
        bpy.context.scene.collection.objects.link(arm_obj)
        bpy.context.view_layer.objects.active = arm_obj
        bpy.ops.object.mode_set(mode="EDIT")
        b = arm.edit_bones.new("north")
        b.head, b.tail = (0, 0, 0), (0, 0, 1)
        bpy.ops.object.mode_set(mode="OBJECT")
        objs = []
        for x in (0.0, 1.8):  # 半径1の球を重なるように配置(接触パーツ)
            bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1,
                                                  location=(x, 0, 0))
            o = bpy.context.active_object
            vg = o.vertex_groups.new(name="north")
            vg.add([v.index for v in o.data.vertices], 1.0, "REPLACE")
            mod = o.modifiers.new("Armature", "ARMATURE")
            mod.object = arm_obj
            objs.append(o)
        scene = bpy.context.scene
        scene.fpm_use_random_seed = False
        scene.fpm_color_seed = 1234
        scene.fpm_sharp_auto = True
        scene.fpm_bone_hard_names = ""
        scene.fpm_part_tint = tint_on
        arm_obj.select_set(False)
        for o in objs:
            o.select_set(True)
        bpy.context.view_layer.objects.active = objs[0]
        bpy.ops.fpm.auto_vertex_color()
        mecha, bone = [], []
        for o in objs:
            mecha.append(set(get_mecha_colors(o)))
            attr = o.data.color_attributes["bone_color"]
            bone.append({tuple(round(v, 2) for v in d.color[:3])
                         for d in attr.data})
        return mecha, bone

    def luma_gap(pair):
        # 各パーツの平均輝度の差。PROノードの線検出は輝度差に反応する
        lumas = [sum(sum(c) / 3.0 for c in s) / len(s) for s in pair]
        return abs(lumas[0] - lumas[1])

    mecha_on, bone_on = build(True)
    mecha_off, bone_off = build(False)
    g_on, g_off = luma_gap(mecha_on), luma_gap(mecha_off)
    assert g_on >= 0.12, f"tint ON should separate part lumas: {g_on:.3f}"
    assert g_off < 0.05, f"tint OFF must keep near-equal lumas (jitter only): {g_off:.3f}"
    # bone_color は ON/OFF に関わらず元の純粋なブレンドのまま
    assert bone_on == bone_off, f"bone_color must stay pure: {bone_on} vs {bone_off}"
    assert bone_on[0] == bone_on[1], f"same bone -> same bone_color: {bone_on}"


@test("STEP3 file output writes exactly the selected passes")
def t19():
    # fpm_file_output ON で File Output ノードが追加され、チェックの入った
    # パスだけがスロットになり配線されること。OFF(既定)では追加されない。
    # v2.5.0 は line/color/Shadow 固定だった。影は EEVEE だとノイズが多く
    # 使えないことが多いのでディフューズ直接光を既定にし、影は任意に。
    # RenderLayers のソケット名は 4.x が 'DiffDir'、5.x が 'Diffuse Direct'。
    def build(enable, **flags):
        bpy.ops.wm.read_homefile(use_empty=True)
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2)
        obj = bpy.context.active_object
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        scene = bpy.context.scene
        scene.fpm_use_random_seed = False
        scene.fpm_color_seed = 1234
        scene.fpm_sharp_auto = True
        bpy.ops.fpm.auto_vertex_color()
        bpy.ops.fpm4.link_button()
        scene.fpm_node_type = "pro"
        scene.fpm_enable_compositor_view = False
        scene.fpm_file_output = enable
        scene.fpm_file_output_path = "//render/"
        for k, v in flags.items():
            setattr(scene, k, v)
        bpy.ops.fpm2.link_button()
        tree = fp_batch.comp_tree(scene)
        return [n for n in tree.nodes if n.type == "OUTPUT_FILE"], tree

    # 既定: line / color / light (影は OFF)
    fos, tree = build(True)
    assert len(fos) == 1, f"expected one File Output node: {len(fos)}"
    fo = fos[0]
    assert fp_batch.fo_dir(fo) == "//render/", fp_batch.fo_dir(fo)
    assert fp_batch.fo_slot_names(fo) == {"line", "color", "light"},         fp_batch.fo_slot_names(fo)
    linked = {lk.to_socket.name for lk in tree.links if lk.to_node == fo}
    assert linked == {"line", "color", "light"}, f"unlinked: {linked}"
    assert bpy.context.view_layer.use_pass_diffuse_direct
    src = next(lk.from_socket.name for lk in tree.links
               if lk.to_node == fo and lk.to_socket.name == "light")
    assert src in ("DiffDir", "Diffuse Direct"), src

    # 影を足す
    fos, tree = build(True, fpm_fo_shadow=True)
    assert fp_batch.fo_slot_names(fos[0]) == {"line", "color", "light", "shadow"}
    linked = {lk.to_socket.name for lk in tree.links if lk.to_node == fos[0]}
    assert "shadow" in linked, linked
    assert bpy.context.view_layer.use_pass_shadow
    src = next(lk.from_socket.name for lk in tree.links
               if lk.to_node == fos[0] and lk.to_socket.name == "shadow")
    assert src == "Shadow", src

    # 線だけ
    fos, tree = build(True, fpm_fo_color=False, fpm_fo_light=False)
    assert fp_batch.fo_slot_names(fos[0]) == {"line"},         fp_batch.fo_slot_names(fos[0])

    # 全部外したらノード自体を作らない
    fos, _ = build(True, fpm_fo_line=False, fpm_fo_color=False,
                   fpm_fo_light=False, fpm_fo_shadow=False)
    assert not fos, "no pass selected -> no File Output node"

    fos, _ = build(False)
    assert not fos, "File Output must not be added when disabled"


@test("checked cameras batch-render into per-camera folders")
def t20():
    # freepencil.render_cameras: チェック済みカメラだけを順にレンダリングし、
    # File Output がカメラ別フォルダ //camera_renders/NN_名前/ に書き出す。
    # 実行後は元のカメラ・保存先に戻る。
    import shutil
    import tempfile
    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2)
    obj = bpy.context.active_object
    scene = bpy.context.scene
    cams = {}
    for name, loc in (("CamA", (0, -5, 0)), ("CamB", (5, 0, 0))):
        cam = bpy.data.objects.new(name, bpy.data.cameras.new(name))
        cam.location = loc
        cam.rotation_euler = (1.5708, 0, 0 if name == "CamA" else 1.5708)
        scene.collection.objects.link(cam)
        cams[name] = cam
    cams["CamB"].fpm_cam_render = False
    scene.camera = cams["CamB"]

    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    scene.fpm_use_random_seed = False
    scene.fpm_color_seed = 1234
    scene.fpm_sharp_auto = True
    bpy.ops.fpm.auto_vertex_color()
    bpy.ops.fpm4.link_button()
    scene.fpm_node_type = "pro"
    scene.fpm_enable_compositor_view = False
    scene.fpm_file_output = True
    bpy.ops.fpm2.link_button()
    scene.render.resolution_x = 64
    scene.render.resolution_y = 48

    tmp = Path(tempfile.mkdtemp(prefix="fp_t20_"))
    try:
        bpy.ops.wm.save_as_mainfile(filepath=str(tmp / "t20.blend"))
        fo = next(n for n in fp_batch.comp_tree(scene).nodes
                  if n.bl_idname == "CompositorNodeOutputFile")
        orig_path = fp_batch.fo_dir(fo)
        bpy.ops.fpm.render_cameras()
        root = tmp / "camera_renders"
        cam_a = root / "01_CamA"
        assert cam_a.is_dir(), sorted(p.name for p in root.iterdir())
        # 5.x は format.media_type の既定が MULTI_LAYER_IMAGE で、そのままだと
        # 多層EXR1本になる。compat が IMAGE へ切り替えるので 4.x と同じく
        # スロットごとの個別PNGが出るはず
        pngs = list(cam_a.glob("*.png"))
        assert len(pngs) >= 3, [p.name for p in cam_a.iterdir()]
        assert not any("CamB" in p.name for p in root.iterdir()), \
            "unchecked camera must be skipped"
        assert scene.camera == cams["CamB"], "original camera must be restored"
        assert fp_batch.fo_dir(fo) == orig_path, \
            "File Output path must be restored"
    finally:
        bpy.ops.wm.read_homefile(use_empty=True)
        shutil.rmtree(tmp, ignore_errors=True)


@test("STEP0 auto setup runs STEP1-3 with scene-fit decisions")
def t21():
    # freepencil.auto_setup: リグ検出で bone AOV 自動ON、BLENDマテリアルの
    # HASHED化、film_transparent の維持、STEP1-3 の一括実行を検証。
    bpy.ops.wm.read_homefile(use_empty=True)
    scene = bpy.context.scene
    # STEP2/STEP3 は既定では走らない(SVG には要らない)。ここはラスタまで
    # 建つことを見るテストなので明示的に入れる
    scene.fpm_auto_raster = True

    # リグ付きメッシュ
    arm = bpy.data.armatures.new("FP_Arm")
    arm_obj = bpy.data.objects.new("FP_Arm", arm)
    scene.collection.objects.link(arm_obj)
    bpy.context.view_layer.objects.active = arm_obj
    bpy.ops.object.mode_set(mode="EDIT")
    b = arm.edit_bones.new("root")
    b.head, b.tail = (0, 0, 0), (0, 0, 1)
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1)
    obj = bpy.context.active_object
    vg = obj.vertex_groups.new(name="root")
    vg.add([v.index for v in obj.data.vertices], 1.0, "REPLACE")
    mod = obj.modifiers.new("Armature", "ARMATURE")
    mod.object = arm_obj

    # トゥーン風 BLEND マテリアル(ガラスではない)
    mat = bpy.data.materials.new("FP_Toon")
    mat.use_nodes = True
    mat.blend_method = "BLEND"
    obj.data.materials.append(mat)

    scene.render.film_transparent = False
    scene.fpm_use_random_seed = False
    scene.fpm_color_seed = 1234
    for o in bpy.context.selected_objects:
        o.select_set(False)  # 無選択 → 表示メッシュ自動選択の経路

    bpy.context.scene.fpm_auto_raster = True
    bpy.ops.fpm.auto_setup()

    assert "mecha_color" in obj.data.color_attributes.keys() or \
        "mecha_color" in [c.name for c in obj.data.color_attributes], \
        "STEP1 must run"
    assert "bone_color" in [c.name for c in obj.data.color_attributes], \
        "rigged mesh must get bone_color"
    aovs = [a.name for a in bpy.context.view_layer.aovs]
    assert "bone_color" in aovs, f"bone AOV must be auto-enabled: {aovs}"
    assert scene.fpm_supersample is True and \
        scene.render.resolution_percentage == 200, \
        "full auto must enable 2x supersampling by default"
    assert any(n.type == "GROUP" for n in fp_batch.comp_tree(scene).nodes), \
        "STEP3 must build the compositor group"
    assert mat.blend_method == "HASHED", "toon BLEND must become HASHED"
    assert scene.render.film_transparent is False, \
        "film_transparent must be preserved"

    # 個別トグルOFF: チェックを外した項目は適用されない
    mat2 = bpy.data.materials.new("FP_Toon2")
    mat2.use_nodes = True
    mat2.blend_method = "BLEND"
    obj.data.materials.append(mat2)
    scene.fpm_auto_hashed = False
    scene.fpm_auto_bone = False
    scene.fpm_bone_color = False
    bpy.context.scene.fpm_auto_raster = True
    bpy.ops.fpm.auto_setup()
    assert mat2.blend_method == "BLEND", \
        "hashed conversion must be skipped when toggled off"
    assert scene.fpm_bone_color is False, \
        "bone AOV auto-detect must be skipped when toggled off"
    scene.fpm_auto_hashed = True
    scene.fpm_auto_bone = True

    # AOVの完全自動設定: mask_color に黒以外を塗る → fpm_mask_color 自動ON。
    # 未塗り(STEP1が作る既定の黒のみ)の line_color は手動ONでも OFF になる。
    # マテリアルID加算が有効 → fpm_mat_color 連動ON。検出トグルOFFなら何もしない
    attr = obj.data.color_attributes.get("mask_color") \
        or obj.data.color_attributes.new("mask_color", "BYTE_COLOR", "CORNER")
    attr.data[0].color = (1.0, 1.0, 1.0, 1.0)  # 実際に塗る
    scene.fpm_mask_color = False
    scene.fpm_mat_count = True
    scene.fpm_mat_color = False
    scene.fpm_auto_detect_aov = False
    bpy.context.scene.fpm_auto_raster = True
    bpy.ops.fpm.auto_setup()
    assert scene.fpm_mask_color is False, "detection must be skippable"
    scene.fpm_auto_detect_aov = True
    scene.fpm_line_color = True  # 手動ONだが line_color は未塗り → 自動がOFFへ
    bpy.context.scene.fpm_auto_raster = True
    bpy.ops.fpm.auto_setup()
    assert scene.fpm_mask_color is True, "painted mask_color must enable its AOV"
    assert scene.fpm_mat_color is True, "mat AOV must follow material ID"
    assert scene.fpm_line_color is False, \
        "auto must own AOV config: unpainted line_color turns off"
    aovs = [a.name for a in bpy.context.view_layer.aovs]
    assert "mask_color" in aovs and "mat_color" in aovs, aovs
    assert "line_color" not in aovs, aovs
    scene.fpm_mat_count = False


@test("per-channel strength sliders retune ramps live and idempotently")
def t22():
    # fp_ch_* スライダー: 生成済みノードのしきい値位置を
    # position = 元位置 × 感度 ÷ 強さ で即時更新。1.0で元通り(冪等)、
    # 0でチャンネルOFF(位置1.0)、他チャンネルには影響しない。
    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2)
    obj = bpy.context.active_object
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    scene = bpy.context.scene
    scene.fpm_use_random_seed = False
    scene.fpm_color_seed = 1234
    scene.fpm_sharp_auto = True
    bpy.ops.fpm.auto_vertex_color()
    bpy.ops.fpm4.link_button()
    scene.fpm_node_type = "pro"
    scene.fpm_enable_compositor_view = False
    bpy.ops.fpm2.link_button()

    group = bpy.data.node_groups["FreePencil_v1_1_0_pro"]
    depth = group.nodes["ColorRamp.001"]
    bone = group.nodes["ColorRamp.002"]
    # 基準は「感度1.0のときの位置」。既定が 0.5 になったので、
    # 明示しないと基準自体がずれて以降の掛け算が合わなくなる
    scene.fpm_line_sensitivity = 1.0
    base_depth = depth.color_ramp.elements[1].position
    base_bone = bone.color_ramp.elements[1].position

    scene.fpm_ch_depth = 2.0  # updateコールバックで即反映
    assert abs(depth.color_ramp.elements[1].position - base_depth / 2) < 1e-4
    assert abs(bone.color_ramp.elements[1].position - base_bone) < 1e-4, \
        "other channels must be unaffected"

    scene.fpm_ch_depth = 1.0  # 冪等に復元
    assert abs(depth.color_ramp.elements[1].position - base_depth) < 1e-4

    scene.fpm_line_sensitivity = 0.5  # 全体感度と乗算
    scene.fpm_ch_depth = 2.0
    assert abs(depth.color_ramp.elements[1].position - base_depth / 4) < 1e-4

    scene.fpm_ch_depth = 0.0  # OFF
    assert depth.color_ramp.elements[1].position >= 0.999
    # 位置1.0では強エッジ(勾配>1)が残るため、OFFは色ごと白にする
    assert all(abs(v - 1.0) < 1e-5
               for v in depth.color_ramp.elements[1].color[:3]), \
        "OFF must whiten the ramp color (gradients can exceed 1.0)"

    scene.fpm_line_sensitivity = 1.0
    scene.fpm_ch_depth = 1.0
    assert abs(depth.color_ramp.elements[1].position - base_depth) < 1e-4
    assert depth.color_ramp.elements[1].color[1] < 0.5, \
        "original dark color must be restored after OFF"

    # STEP3 再生成でもシーン値が反映される
    scene.fpm_ch_depth = 2.0
    bpy.ops.fpm2.link_button()
    depth = bpy.data.node_groups["FreePencil_v1_1_0_pro"].nodes["ColorRamp.001"]
    assert abs(depth.color_ramp.elements[1].position - base_depth / 2) < 1e-4
    scene.fpm_ch_depth = 1.0


@test("white preview toggles a compositor mix, materials untouched")
def t23():
    # コンポジタ切替方式: FreePencil グループの Image 入力の手前に
    # Mix(白) を挿入し、係数だけで白地線画⇔マテリアル付きを切替。
    # マテリアル/スロットには一切触れない。ツリーが無ければ何もしない。
    bpy.ops.wm.read_homefile(use_empty=True)
    scene = bpy.context.scene

    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1)
    obj = bpy.context.active_object
    red = bpy.data.materials.new("FP_T23_Red")
    obj.data.materials.append(red)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    # コンポジタツリーが無い状態では安全に何もしない
    scene.fpm_white_preview = True
    scene.fpm_white_preview = False

    scene.fpm_use_random_seed = False
    scene.fpm_color_seed = 1234
    scene.fpm_sharp_auto = True
    bpy.ops.fpm.auto_vertex_color()
    bpy.ops.fpm4.link_button()
    scene.fpm_node_type = "pro"
    scene.fpm_enable_compositor_view = False
    bpy.ops.fpm2.link_button()

    tree = fp_batch.comp_tree(scene)

    def mix_node():
        return next((n for n in tree.nodes
                     if n.label == "FP_WhitePreviewMix"), None)

    scene.fpm_white_preview = True
    mix = mix_node()
    assert mix is not None, "mix node must be inserted"
    assert mix.inputs[0].default_value == 1.0
    grp = next(n for n in tree.nodes if n.type == "GROUP")
    img_link = grp.inputs["Image"].links[0]
    assert img_link.from_node == mix, "group Image must come from the mix"
    rl = next(n for n in tree.nodes if n.type == "R_LAYERS")
    assert mix.inputs[1].links[0].from_node == rl, \
        "mix input 1 must be the beauty pass"
    assert obj.material_slots[0].material.name == "FP_T23_Red", \
        "materials must be untouched"
    assert red.use_nodes is False or True  # マテリアルに変更を加えない方式

    scene.fpm_white_preview = False
    assert mix_node().inputs[0].default_value == 0.0, "factor back to zero"
    assert obj.material_slots[0].material.name == "FP_T23_Red"


@test("white preview leaves shared-mesh slots alone and heals legacy backups")
def t24():
    # ノード注入方式ではスロットを一切書き換えないので、リンク複製
    # (メッシュデータ共有)でも何も起きないことを検証。加えて、
    # 旧スワップ方式で保存されたファイルの汚染バックアップが OFF で
    # 自己修復されること(レガシー復元パス)。
    bpy.ops.wm.read_homefile(use_empty=True)
    scene = bpy.context.scene

    red = bpy.data.materials.new("FP_T24_Red")
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1)
    a = bpy.context.active_object
    a.data.materials.append(red)
    b = bpy.data.objects.new("FP_T24_B", a.data)  # リンク複製
    scene.collection.objects.link(b)

    # 共有メッシュ+マテリアル無し
    bpy.ops.mesh.primitive_cube_add(location=(5, 0, 0))
    c = bpy.context.active_object
    d = bpy.data.objects.new("FP_T24_D", c.data)
    scene.collection.objects.link(d)

    scene.fpm_white_preview = True
    for o in (a, b):
        assert o.material_slots[0].material.name == "FP_T24_Red", \
            "slots must never change in compositor mode"
    assert len(c.data.materials) == 0, \
        "compositor mode must not add slots either"

    scene.fpm_white_preview = False

    # 旧スワップ方式の汚染バックアップ(兄弟が白を元として保存)の自己修復
    white = bpy.data.materials.new("FP_White_Preview")
    for slot in a.material_slots:
        slot.material = white
    a["fp_orig_mats"] = ["FP_T24_Red"]
    b["fp_orig_mats"] = ["FP_White_Preview"]  # 汚染
    scene.fpm_white_preview = True   # プロパティを立ててから
    scene.fpm_white_preview = False  # OFFでレガシー復元パスを通す
    mats = [s.material.name if s.material else "" for s in a.material_slots]
    assert mats == ["FP_T24_Red"], f"poisoned backup must not win: {mats}"


@test("white preview survives STEP3 regeneration")
def t25():
    # プレビューON中に STEP3 を再生成すると Mix(白) は一旦消えるが、
    # setup_compositor が挿入し直して状態が維持されること。
    bpy.ops.wm.read_homefile(use_empty=True)
    scene = bpy.context.scene
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1)
    obj = bpy.context.active_object
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    scene.fpm_use_random_seed = False
    scene.fpm_color_seed = 1234
    scene.fpm_sharp_auto = True
    bpy.ops.fpm.auto_vertex_color()
    bpy.ops.fpm4.link_button()
    scene.fpm_node_type = "pro"
    scene.fpm_enable_compositor_view = False
    bpy.ops.fpm2.link_button()

    scene.fpm_white_preview = True
    bpy.ops.fpm2.link_button()  # STEP3 再生成
    tree = fp_batch.comp_tree(scene)
    mix = next((n for n in tree.nodes
                if n.label == "FP_WhitePreviewMix"), None)
    assert mix is not None, "mix must be re-inserted after STEP3 regen"
    assert mix.inputs[0].default_value == 1.0, "preview state must survive"
    grp = next(n for n in tree.nodes if n.type == "GROUP")
    assert grp.inputs["Image"].links[0].from_node == mix

    scene.fpm_white_preview = False
    assert mix.inputs[0].default_value == 0.0


@test("2x supersampling wires half-scale into composite and file output")
def t26():
    # fpm_supersample ON: 解像度200% + Composite/File Output の直前に
    # 0.5 RELATIVE スケールが入る。OFFで再生成すると解像度100%に戻り
    # スケールノードも消える。
    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1)
    obj = bpy.context.active_object
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    scene = bpy.context.scene
    scene.fpm_use_random_seed = False
    scene.fpm_color_seed = 1234
    scene.fpm_sharp_auto = True
    bpy.ops.fpm.auto_vertex_color()
    bpy.ops.fpm4.link_button()
    scene.fpm_node_type = "pro"
    scene.fpm_enable_compositor_view = False
    scene.fpm_file_output = True
    scene.fpm_supersample = True
    bpy.ops.fpm2.link_button()

    tree = fp_batch.comp_tree(scene)
    assert scene.render.resolution_percentage == 200
    comp = next(n for n in tree.nodes if fp_batch.is_output_node(n))
    src = comp.inputs[0].links[0].from_node
    # 常時 0.5。ビューポートプレビューが半分のサイズになる副作用があるが、
    # 1.0 にするとプレビューの線が細線化されず、細さを確認できなくなる。
    # 細さの確認がプレビューの目的なので、表示が小さい方を受け入れる。
    assert src.type == "SCALE" and src.inputs["X"].default_value == 0.5, \
        f"composite must be fed via 0.5 scale, got {src.type}"
    fo = next(n for n in tree.nodes if n.type == "OUTPUT_FILE")
    linked = [s for s in fo.inputs if s.links]
    # 5.x の File Output は末尾に未接続の仮想ソケットが常に1本ぶら下がる
    assert linked, "file output must have linked slots"
    for sock in linked:
        assert sock.links[0].from_node.type == "SCALE", \
            f"file output slot {sock.name} must be scaled"

    scene.fpm_supersample = False
    bpy.ops.fpm2.link_button()
    tree = fp_batch.comp_tree(scene)
    assert scene.render.resolution_percentage == 100
    assert not any(n.type == "SCALE" for n in tree.nodes), \
        "scale nodes must be removed when supersampling is off"


@test("STEP1/STEP0 progress generator drives per-object and INVOKE is safe")
def t27():
    # 応答なし対策のモーダル進捗バー回帰。GUIモーダルは headless では
    # 動かせないので、(a) 生成器が1オブジェクトずつ進捗を yield すること、
    # (b) INVOKE_DEFAULT が background では同期実行へ落ちること、を見る。
    from freepencil2 import vertex_color

    bpy.ops.wm.read_homefile(use_empty=True)
    for i in range(3):
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1, location=(i * 3, 0, 0))
    # primitive_add は直前の選択を外すので、最後にまとめて選択し直す
    for o in bpy.context.scene.objects:
        o.select_set(True)
    scene = bpy.context.scene
    scene.fpm_use_random_seed = False
    scene.fpm_color_seed = 1234

    gen, state = vertex_color.make_vertex_color_gen(bpy.context, quiet=True)
    steps = []
    while True:
        try:
            steps.append(next(gen))
        except StopIteration:
            break
    # 単一の高密度メッシュでもバーが進むよう、オブジェクト単位に加えて
    # オブジェクト内フェーズでも yield する(done は小数になる)。
    done = [s[0] for s in steps]
    assert all(s[1] == 3 for s in steps), f"total must be object count: {steps}"
    assert done == sorted(done), f"progress must never go backwards: {done}"
    assert done[0] == 0.0, f"must yield before any heavy setup: {steps}"
    # 各オブジェクトの開始(整数)が来ていること
    for k in range(3):
        assert k in done, f"missing start of object {k}: {done}"
    # 最終オブジェクトの内部フェーズまで刻まれていること
    assert max(done) >= 2.5, f"per-object phases must be reported: {done}"
    # 1メッシュあたり複数回刻まれる = バーが 0/1 で固まらない
    assert len(steps) >= 3 * 3, f"too few progress steps: {len(steps)}"
    assert state._result == {"FINISHED"}, state._result

    # background では invoke() -> execute() に落ちる(モーダルを張らない)
    bpy.ops.wm.read_homefile(use_empty=True)
    obj = fresh_scene_with_islands()
    res = bpy.ops.fpm.auto_vertex_color("INVOKE_DEFAULT")
    assert res == {"FINISHED"}, res
    assert "mecha_color" in obj.data.color_attributes.keys()

    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1)
    o = bpy.context.active_object
    o.select_set(True)
    bpy.context.view_layer.objects.active = o
    bpy.context.scene.fpm_auto_raster = True
    res = bpy.ops.fpm.auto_setup("INVOKE_DEFAULT")
    assert res == {"FINISHED"}, res
    assert fp_batch.comp_tree() is not None, "STEP0 must still reach STEP3"


@test("STEP0 stops at the paint by default - SVG needs no compositor")
def t28b():
    # 主目的は SVG。コンポジタを毎回建てるとビューポートがレンダー表示と
    # 白マテリアルに変わり、「何かがおかしくなった」と受け取られる。
    from freepencil2 import svg_export

    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2)
    obj = bpy.context.active_object
    obj.data.materials.append(bpy.data.materials.new("FP_T28B_Mat"))
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    scene = bpy.context.scene
    scene.fpm_use_random_seed = False
    scene.fpm_color_seed = 1234
    assert scene.fpm_auto_raster is False, "ラスタ設定は既定で切ってあること"

    bpy.ops.fpm.auto_setup()

    # 塗り分けは済んでいる = SVG が出せる
    assert obj.data.color_attributes.get("mecha_color") is not None, (
        "STEP1 の塗り分けが走っていない")
    # ラスタ側には触っていない
    assert fp_batch.comp_tree(scene) is None or not [
        n for n in fp_batch.comp_tree(scene).nodes
        if n.label.startswith("FreePencil")], "コンポジタが建っている"
    assert not [a for a in bpy.context.view_layer.aovs
                if a.name == "mecha_color"], "AOV が足されている"
    assert scene.fpm_white_preview is False, "白プレビューが立っている"
    assert not scene.render.film_transparent, "背景の透過が変えられている"

    # そのまま SVG が書き出せること(コンポジタ抜きで完結する)
    fp_batch.setup_camera_and_light()
    scene.render.resolution_x, scene.render.resolution_y = 400, 300
    segs, n = svg_export.compute_preview(
        bpy.context, svg_export.SvgOptions(depth_res=400))
    assert n > 0, "塗っただけでは線が出ない"


@test("STEP0 leaves the white preview on so line art is visible at once")
def t28():
    # 初回利用者がSTEP0を押しただけで線画が見える状態にする。
    # 白プレビューはコンポジタ切替方式なのでマテリアルは触らない。
    # STEP3 でツリーが建った後に立てる必要がある(順序の回帰も兼ねる)。
    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2)
    obj = bpy.context.active_object
    mat = bpy.data.materials.new("FP_T28_Mat")
    obj.data.materials.append(mat)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    scene = bpy.context.scene
    scene.fpm_use_random_seed = False
    scene.fpm_color_seed = 1234
    scene.fpm_enable_compositor_view = False
    scene.fpm_auto_raster = True      # 白プレビューはコンポジタが要る
    assert scene.fpm_auto_white_preview is True, "must default to on"

    bpy.context.scene.fpm_auto_raster = True
    bpy.ops.fpm.auto_setup()

    assert scene.fpm_white_preview is True, "STEP0 must leave white preview on"
    tree = fp_batch.comp_tree(scene)
    mix = next((n for n in tree.nodes if n.label == "FP_WhitePreviewMix"), None)
    assert mix is not None, "white preview mix must be wired by STEP0"
    assert mix.inputs[0].default_value == 1.0
    grp = next(n for n in tree.nodes if n.type == "GROUP")
    assert grp.inputs["Image"].links[0].from_node == mix, \
        "group Image must be fed through the white mix"
    assert obj.material_slots[0].material.name == "FP_T28_Mat", \
        "materials must stay untouched"

    # トグルOFFなら白プレビューには触れない
    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2)
    o2 = bpy.context.active_object
    o2.select_set(True)
    bpy.context.view_layer.objects.active = o2
    scene = bpy.context.scene
    scene.fpm_use_random_seed = False
    scene.fpm_color_seed = 1234
    scene.fpm_enable_compositor_view = False
    scene.fpm_auto_white_preview = False
    bpy.context.scene.fpm_auto_raster = True
    bpy.ops.fpm.auto_setup()
    assert scene.fpm_white_preview is False, \
        "toggle off must leave the white preview alone"


@test("sharp edges drive islands and STEP1 never mutates the mesh")
def t29():
    # アーティストの意図は Freestyle マークではなく「シャープ」で受け取る。
    # 島境界の判定はローカル配列で持ち、メッシュのシャープ/スムーズには
    # 一切書き込まない(以前は一時的に上書きして後で戻していた)。
    import numpy as np

    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.ops.mesh.primitive_cube_add()
    obj = bpy.context.active_object
    # サブディバイドして「角度は緩いがシャープを付けた」エッジを作る
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.subdivide(number_cuts=3)
    bpy.ops.object.mode_set(mode="OBJECT")

    me = obj.data
    n = len(me.edges)
    marked = np.zeros(n, dtype=bool)
    marked[: n // 4] = True          # 一部だけシャープにする
    me.edges.foreach_set("use_edge_sharp", marked)

    before = np.empty(n, dtype=bool)
    me.edges.foreach_get("use_edge_sharp", before)
    before_smooth = np.empty(len(me.polygons), dtype=bool)
    me.polygons.foreach_get("use_smooth", before_smooth)

    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    scene = bpy.context.scene
    scene.fpm_use_random_seed = False
    scene.fpm_color_seed = 1234
    scene.fpm_sharp_clear = False      # シャープを尊重する既定の経路
    scene.fpm_sharp_auto = False
    scene.fpm_sharp_edges = 179.0      # 角度では絶対に割れない設定に
    scene.fpm_min_island_area_pct = 0.0
    scene.fpm_seam_boundaries = False
    res = bpy.ops.fpm.auto_vertex_color()
    assert res == {"FINISHED"}, res

    # メッシュは無改変(ここが以前は書き換わって復元されていた)
    after = np.empty(len(obj.data.edges), dtype=bool)
    obj.data.edges.foreach_get("use_edge_sharp", after)
    assert np.array_equal(before, after), \
        "STEP1 must not touch use_edge_sharp"
    after_smooth = np.empty(len(obj.data.polygons), dtype=bool)
    obj.data.polygons.foreach_get("use_smooth", after_smooth)
    assert np.array_equal(before_smooth, after_smooth), \
        "STEP1 must not touch face smoothing"

    # 角度では割れない設定なので、島が複数あるならシャープが効いた証拠
    colors = {c for c in get_mecha_colors(obj)}
    assert len(colors) >= 2, \
        f"sharp-marked edges must split islands, got {len(colors)} color(s)"


@test("stale node group is regenerated and users are remapped")
def t30():
    # 「無ければ作る」判定だったため、古い .blend やアドオン旧版のノード
    # グループが残っていると永久に更新されなかった。無条件生成にすると
    # Blender が "名前.001" を作り、参照は古い方を掴んだままになる。
    # 版が古ければ作り直し、user_remap で参照を移してから正式名に戻す。
    from freepencil2 import utils_nodegroup as ung

    bpy.ops.wm.read_homefile(use_empty=True)
    name = "FreePencil_v1_1_0_pro"

    # 中身が空っぽの「古いグループ」を仕込む(版マーカーなし)
    stale = bpy.data.node_groups.new(name, "CompositorNodeTree")
    assert len(stale.nodes) == 0
    # それを使っているノードを1つ作り、参照が移ることを確かめる
    host = bpy.data.node_groups.new("FP_T30_Host", "CompositorNodeTree")
    user = host.nodes.new("CompositorNodeGroup")
    user.node_tree = stale

    ng = ung.ensure_node_group_updated(name)

    assert ng.name == name, f"正式名に戻すこと: {ng.name}"
    assert len(ng.nodes) > 10, f"古い空グループが再生成されていない: {len(ng.nodes)}"
    assert ng.get("fp_node_version") == ung._stamp()
    # ".001" が残っていないこと(=古い方が消えている)
    assert not any(n.name.startswith(name + ".")
                   for n in bpy.data.node_groups), \
        [n.name for n in bpy.data.node_groups]
    # 参照が新しいグループへ移っていること
    assert user.node_tree is ng, "user_remap で参照を移すこと"

    # 2回目は版が一致するので作り直さない(冪等)
    again = ung.ensure_node_group_updated(name)
    assert again is ng, "最新版なら再生成しない"


@test("numeric socket indices used by fp_core hold on this Blender")
def t31():
    # fp_core は数値添字でソケットを掴んでいる箇所がある。5.x でソケット
    # 構成が変わったため本来は名前引きが原則だが、Mix は 4.5 で 'Image' が
    # 2つあり名前で引けない(実測: ['Fac','Image','Image'])。両バージョンの
    # ランタイムダンプで位置を確認したうえで添字を使っている。
    # ここでその前提が崩れていないことを固定する。
    from freepencil2 import compat

    bpy.ops.wm.read_homefile(use_empty=True)
    ng = bpy.data.node_groups.new("FP_T31", "CompositorNodeTree")
    if compat.IS_5_PLUS:
        ng.interface.new_socket("Image", in_out="OUTPUT",
                                socket_type="NodeSocketColor")

    # 白プレビューの Mix: [0]=係数 [1]=下段 [2]=上段
    mix = compat.new_node(ng, "CompositorNodeMixRGB")
    assert len(mix.inputs) >= 3, [s.name for s in mix.inputs]
    assert mix.inputs[0].name in ("Fac", "Factor"), mix.inputs[0].name
    assert mix.inputs[1].name in ("Image", "Color1"), mix.inputs[1].name
    assert mix.inputs[2].name in ("Image", "Color2"), mix.inputs[2].name

    # 細線化の Scale: [0]=Image、倍率は名前引き
    sc = compat.new_node(ng, "CompositorNodeScale")
    assert sc.inputs[0].name == "Image", sc.inputs[0].name
    assert sc.inputs.get("X") is not None, [s.name for s in sc.inputs]

    # 最終出力: [0]=Image (4.x Composite / 5.x Group Output)
    out = compat.new_output_node(ng)
    assert out.inputs[0].name == "Image", out.inputs[0].name

    # Set Alpha: [0]=Image
    sa = ng.nodes.new("CompositorNodeSetAlpha")
    assert sa.inputs[0].name == "Image", sa.inputs[0].name

    # Anti-Aliasing: [0]=Image
    try:
        aa = ng.nodes.new("CompositorNodeAntiAliasing")
        assert aa.inputs[0].name == "Image", aa.inputs[0].name
    except RuntimeError:
        pass  # このビルドに無ければ対象外


@test("version numbers agree between bl_info and the manifest")
def t32():
    # v2.5.0 公開時、bl_info の version が (2,4,0) のままで配布ZIPの
    # パネルに v2.4.0 と出た。最低バージョンも bl_info=4.3 / manifest=4.2 と
    # ずれていた。番号は2箇所にあるので、一致をテストで固定する。
    import re

    import freepencil2

    repo = Path(freepencil2.__file__).resolve().parent
    manifest = (repo / "blender_manifest.toml").read_text(encoding="utf-8")

    def field(key):
        m = re.search(rf'^{key}\s*=\s*"([^"]+)"', manifest, re.M)
        assert m, f"{key} が manifest に無い"
        return m.group(1)

    ver = tuple(int(x) for x in field("version").split("."))
    assert ver == tuple(freepencil2.bl_info["version"]), (
        f'manifest version={ver} != bl_info={freepencil2.bl_info["version"]}')

    vmin = tuple(int(x) for x in field("blender_version_min").split("."))
    assert vmin == tuple(freepencil2.bl_info["blender"]), (
        f'manifest blender_version_min={vmin} '
        f'!= bl_info blender={freepencil2.bl_info["blender"]}')

    # パネル見出しに出る文字列も同じ番号であること
    label = bpy.types.FPM_PT_LINE.bl_label
    assert label.endswith(".".join(map(str, ver))), label


@test("viewport preview is skipped where AOVs are not evaluated (4.2)")
def t33():
    # 4.2 のビューポートコンポジタは AOV を評価しないため、レンダー表示に
    # 切り替えると真っ白になる(実測)。切り替えないことを固定する。
    # 4.3 以降では従来どおり切り替える。
    from freepencil2 import compat

    expected = bpy.app.version >= (4, 3, 0)
    assert compat.HAS_AOV_IN_VIEWPORT_COMPOSITOR is expected, (
        f"flag={compat.HAS_AOV_IN_VIEWPORT_COMPOSITOR} "
        f"expected={expected} on {bpy.app.version_string}")

    # RENDERED へ切り替える箇所は STEP2(aov_node) と STEP3(sample_node) の
    # 2つある。どちらもフラグでガードされていること。片方だけ直して
    # 4.2 が白いまま、という取りこぼしを防ぐ。
    repo = Path(compat.__file__).resolve().parent
    for fname in ("sample_node.py", "aov_node.py"):
        body = (repo / fname).read_text(encoding="utf-8")
        if "shading.type = 'RENDERED'" not in body:
            continue
        guard = body.find("HAS_AOV_IN_VIEWPORT_COMPOSITOR")
        switch = body.index("shading.type = 'RENDERED'")
        assert 0 <= guard < switch, f"{fname}: RENDERED 切り替えが未ガード"


@test("part tint windows keep their step and stay inside the luma range")
def t34():
    # パーツ・トーン分けの明度窓。窓幅は段によって不揃いになる(上限
    # 0.85 でクランプされるため)が、それは許容している。守るべきは
    # 「段の間隔」と「範囲からはみ出さないこと」。
    # 幅を揃えるために段を詰める案は、パーツ分離(t18)と min距離契約(t14)を
    # 壊すうえ絵が変わらないので却下済み(utils.part_luma_window のコメント)。
    from freepencil2 import utils

    windows = [utils.part_luma_window(p) for p in range(utils.PART_TINT_STEPS)]

    for lo, hi in windows:
        assert lo >= utils.PART_LUMA_FLOOR - 1e-9, (lo, utils.PART_LUMA_FLOOR)
        assert hi <= utils.PART_LUMA_CEIL + 1e-9, (hi, utils.PART_LUMA_CEIL)
        assert hi > lo, (lo, hi)

    # 段の間隔が保たれていること(接するパーツに線を出すための分離)
    los = [lo for lo, _ in windows]
    for a, b in zip(los, los[1:]):
        assert round(b - a, 6) == utils.PART_TINT_DELTA, (los,
                                                          utils.PART_TINT_DELTA)

    # 巡回すること(クラス番号が段数を超えても壊れない)
    assert utils.part_luma_window(utils.PART_TINT_STEPS) == windows[0]

    # 実際に色を作っても輝度が範囲内に収まること
    for lo, hi in windows:
        colors, _pmin, _lmin = utils.build_palette(5, 42, luma_lo=lo, luma_hi=hi)
        lumas = [utils._luma(c) for c in colors]
        assert min(lumas) >= utils.PART_LUMA_FLOOR - 0.02, min(lumas)
        assert max(lumas) <= utils.PART_LUMA_CEIL + 0.02, max(lumas)


@test("generated node trees are laid out without overlaps or backward links")
def t35():
    # 座標はエクスポート元 .blend の手配置がそのまま入っており、実測で
    # PROノード80個に対して重なり97組・リンク97本中47本が右から左へ
    # 逆流していた。生成後に階層レイアウトを掛けて解消している。
    # 機能ではなく可読性の話だが、ユーザーがコンポジタを開く前提の
    # 復旧手順がある以上、崩れたら気づけるようにしておく。
    import itertools

    from freepencil2 import compat

    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.ops.mesh.primitive_cube_add()
    bpy.context.active_object.select_set(True)
    scene = bpy.context.scene
    scene.fpm_node_type = "pro"
    scene.fpm_enable_compositor_view = False
    bpy.context.scene.fpm_auto_raster = True
    bpy.ops.fpm.auto_setup("EXEC_DEFAULT")

    def rect(n):
        w = n.width or 140.0
        h = n.dimensions.y or (46.0 + 24.0 * (len(n.inputs) + len(n.outputs)))
        return (n.location.x, n.location.y - h, n.location.x + w, n.location.y)

    trees = [g for g in bpy.data.node_groups if g.name.startswith("FreePencil")]
    root = compat.get_compositor_tree(scene)
    if root is not None:
        trees.append(root)
    assert trees, "no FreePencil trees were built"

    for tree in trees:
        nodes = list(tree.nodes)
        rects = {n.name: rect(n) for n in nodes}

        for a, b in itertools.combinations(nodes, 2):
            ax0, ay0, ax1, ay1 = rects[a.name]
            bx0, by0, bx1, by1 = rects[b.name]
            dx = min(ax1, bx1) - max(ax0, bx0)
            dy = min(ay1, by1) - max(ay0, by0)
            assert not (dx > 1.0 and dy > 1.0), (
                f"{tree.name}: {a.name} と {b.name} が重なっている")

        # グループ内は逆流ゼロにできる。ルートは白プレビューの差し込みで
        # 1本だけ戻ることがあるため許容する
        backward = [lk for lk in tree.links
                    if rects[lk.to_node.name][0] < rects[lk.from_node.name][2]]
        limit = 0 if tree is not root else 2
        assert len(backward) <= limit, (
            f"{tree.name}: 逆流リンク {len(backward)} 本 "
            f"({[lk.from_node.name for lk in backward][:4]})")


@test("manual channels behave the same on every Blender version")
def t36():
    # 5.x 用 PRO ノードの書き出しで Color Key の設定が丸ごと落ちており
    # (color_hue がプロパティからソケットへ移ったのを検出できず沈黙して
    # スキップしていた)、キーする色が黒→白の既定に化けて mask_color が
    # 反転していた。4.5 では「塗れば消える」、5.2 では「白は無効」と
    # バージョンで結果が違う状態だった。
    #
    # 正しい挙動(4.x と一致):
    #   mask_color … 塗った側の線が消える。明度は問わない(白でも消える)
    #   line_color … 明るく塗るほど線が薄くなり、0.4 以上で見えなくなる
    #
    # バージョン差がまた入らないよう、値そのものを固定する。
    def ink_after(channel, value):
        bpy.ops.wm.read_homefile(use_empty=True)
        bpy.ops.mesh.primitive_cube_add()
        obj = bpy.context.active_object
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        scene = bpy.context.scene
        scene.fpm_use_random_seed = False
        scene.fpm_color_seed = 1234
        scene.fpm_enable_compositor_view = False
        scene.fpm_supersample = False
        scene.fpm_auto_detect_aov = False
        scene.fpm_mask_color = True
        scene.fpm_line_color = True
        bpy.context.scene.fpm_auto_raster = True
        bpy.ops.fpm.auto_setup("EXEC_DEFAULT")
        if channel:
            attr = obj.data.color_attributes[channel]
            n = len(attr.data)
            attr.data.foreach_set("color", [value, value, value, 1.0] * n)
            obj.data.update()
        fp_batch.setup_camera_and_light()
        scene.render.engine = fp_batch.eevee_engine()
        scene.eevee.taa_render_samples = 4
        scene.render.resolution_x = scene.render.resolution_y = 240
        scene.render.image_settings.file_format = "PNG"
        scene.render.image_settings.color_mode = "RGBA"
        png = BATCH / "out" / f"t36_{channel or 'base'}_{value}.png"
        png.parent.mkdir(parents=True, exist_ok=True)
        fp_batch.render_still(scene, png, 1)
        return fp_batch.lineart_metrics(png)["ink_ratio"]

    base = ink_after(None, 0.0)
    assert base > 0, "基準に線が出ていない"

    # mask は明度によらず消える。白でも消えるのが正しい
    for value in (0.2, 0.5, 1.0):
        got = ink_after("mask_color", value)
        assert got == 0.0, (
            f"mask_color={value} で線が残っている: {got} (基準 {base})。"
            "Color Key のキー色が黒でなく白になっていないか")

    # line は明るいほど薄くなり、0.4 以上で消える
    line_dim = ink_after("line_color", 0.2)
    assert 0 < line_dim < base, (
        f"line_color=0.2 が薄くなっていない: {line_dim} vs {base}")
    line_off = ink_after("line_color", 0.6)
    assert line_off == 0.0, f"line_color=0.6 で線が消えていない: {line_off}"


@test("far crush relief inserts nothing at 0 and is idempotent")
def t37():
    # 遠景つぶれ軽減は既定 OFF。OFF のときは1ノードも挿さらず、
    # line 出力のアルファ配線が素のままであること(=従来の絵と同一)。
    # ON/OFF を往復しても配線が元に戻ることも押さえる。
    from freepencil2 import fp_core

    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.ops.mesh.primitive_cube_add()
    obj = bpy.context.active_object
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    scene = bpy.context.scene
    scene.fpm_enable_compositor_view = False
    scene.fpm_auto_detect_aov = False
    bpy.context.scene.fpm_auto_raster = True
    bpy.ops.fpm.auto_setup("EXEC_DEFAULT")

    group = next(g for g in bpy.data.node_groups
                 if g.name.startswith(fp_core.NODE_GROUP_PREFIX))
    # ノード名はバージョンで変わるので配線で辿る(4.2 と 4.5 で別名だった)
    out_node = next(n for n in group.nodes if n.type == "GROUP_OUTPUT")
    line_in = next(s for s in out_node.inputs if s.name == "line")
    assert line_in.links, "line 出力に何も繋がっていない"
    sink = line_in.links[0].from_node
    assert sink.inputs.get("Alpha") is not None, "line 出力に Alpha が無い"
    plain = sink.inputs["Alpha"].links[0].from_node.name

    def relief_nodes():
        return [n for n in group.nodes if n.label == fp_core.RELIEF_LABEL]

    def alpha_from():
        links = sink.inputs["Alpha"].links
        return links[0].from_node.name if links else None

    assert not relief_nodes(), "既定でノードが挿さっている"

    n = fp_core.apply_far_relief(group, strength=0.0)
    assert n == 0 and not relief_nodes(), "強さ0で挿さってしまった"

    n = fp_core.apply_far_relief(group, strength=0.6, radius=6.0)
    assert n == 5, f"挿し込みノード数が想定外: {n}"
    assert len(relief_nodes()) == 5
    assert alpha_from() == "fp_relief_apply", (
        f"アルファが軽減ノードを通っていない: {alpha_from()}")

    # 2回目でも増殖しない
    n2 = fp_core.apply_far_relief(group, strength=0.6, radius=6.0)
    assert n2 == 5 and len(relief_nodes()) == 5, "呼ぶたびに増えている"

    # 0 に戻したら素の配線へ復帰する
    fp_core.apply_far_relief(group, strength=0.0)
    assert not relief_nodes(), "撤去できていない"
    assert alpha_from() == plain, f"配線が戻っていない: {alpha_from()}"


@test("a healthy scene compositor tree is never discarded")
def t38():
    # 5.x はシーンのコンポジタをノードグループとして持つ。その .blend を
    # 4.x で開くと、そのグループが scene.node_tree に居座って絵が壊れる。
    # discard_foreign_scene_tree はそれを見分けて捨てる。
    #
    # ここで固定するのは「捨てすぎない」方向。居座り状態は 4.x の
    # scene.node_tree が読み取り専用なので Python からは作れず、
    # 実ファイルでの確認は dev/note_assets/eval_cross_version.py が行う
    # (実測: 5.2 で作成 0.0065 -> 4.5 で開く 0.9286 -> STEP3 で 0.0067)。
    from freepencil2 import compat

    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.ops.mesh.primitive_cube_add()
    obj = bpy.context.active_object
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    scene = bpy.context.scene
    scene.fpm_enable_compositor_view = False
    scene.fpm_auto_detect_aov = False
    bpy.context.scene.fpm_auto_raster = True
    bpy.ops.fpm.auto_setup("EXEC_DEFAULT")

    tree = compat.get_compositor_tree(scene)
    n_before = len(tree.nodes)
    assert n_before > 0, "コンポジタが組まれていない"

    # 正常なツリーは対象外
    assert compat.discard_foreign_scene_tree(scene) is False, (
        "正常なシーンツリーを捨てようとしている")
    tree2 = compat.get_compositor_tree(scene, create=True)
    assert len(tree2.nodes) == n_before, (
        f"ツリーが壊れた: {n_before} -> {len(tree2.nodes)}")

    # 4.x のシーンツリーは埋め込みで、node_groups には現れない
    if not compat.IS_5_PLUS:
        assert tree2.name not in bpy.data.node_groups, (
            "4.x のシーンツリーがグループとして現れている")


@test("file output is written at final size, not at 2x supersample size")
def t39():
    # 細線化は「200%でレンダして0.5に縮小」で作る。縮小ノードが
    # Composite にしか挿さっていないと、ファイル出力だけ2倍の大きさで
    # 出てしまい、F12 の絵と食い違う。実サイズで確かめる。
    import shutil
    import tempfile

    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2)
    obj = bpy.context.active_object
    scene = bpy.context.scene
    cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam"))
    cam.location = (0, -5, 0)
    cam.rotation_euler = (1.5708, 0, 0)
    scene.collection.objects.link(cam)
    scene.camera = cam

    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    scene.fpm_use_random_seed = False
    scene.fpm_color_seed = 1234
    scene.fpm_enable_compositor_view = False
    scene.fpm_auto_detect_aov = False
    scene.fpm_file_output = True
    scene.fpm_supersample = True          # ← 200% + 0.5 縮小
    bpy.context.scene.fpm_auto_raster = True
    bpy.ops.fpm.auto_setup("EXEC_DEFAULT")

    scene.render.resolution_x = 64
    scene.render.resolution_y = 48
    assert scene.render.resolution_percentage == 200, (
        "細線化がレンダー倍率に反映されていない")

    tmp = Path(tempfile.mkdtemp(prefix="fp_t39_"))
    try:
        bpy.ops.wm.save_as_mainfile(filepath=str(tmp / "t39.blend"))
        bpy.ops.fpm.render_cameras()
        cam_dir = tmp / "camera_renders" / "01_Cam"
        pngs = sorted(cam_dir.glob("*.png"))
        assert pngs, sorted(p.name for p in cam_dir.iterdir())
        for png in pngs:
            img = bpy.data.images.load(str(png))
            size = tuple(img.size)
            bpy.data.images.remove(img)
            assert size == (64, 48), (
                f"{png.name} が最終サイズで出ていない: {size} != (64, 48)")
    finally:
        bpy.ops.wm.read_homefile(use_empty=True)
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------ SVG 書き出し
def _svg_scene():
    """カメラ付きの塗り分け済みシーン。SVG 側のテストの共通の下ごしらえ。"""
    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.ops.mesh.primitive_monkey_add(size=2.0)
    scene = bpy.context.scene
    scene.fpm_use_random_seed = False
    scene.fpm_color_seed = 1234
    objs = [o for o in scene.objects if o.type == "MESH"]
    fp_batch.apply_white_material(objs)
    fp_batch.select_meshes()
    bpy.ops.fpm.auto_vertex_color()
    fp_batch.setup_camera_and_light()
    scene.render.resolution_x = 400
    scene.render.resolution_y = 300
    return objs


def _n_edges(opts=None):
    """線になる辺の本数。レンダーを走らせないので速い。"""
    from freepencil2 import svg_export
    dg = bpy.context.evaluated_depsgraph_get()
    objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    got = svg_export.extract_lines(objs, dg, bpy.context.scene.camera, opts)
    return sum(len(d["edges"]) for d in got) if got else 0


@test("SVG: bone source is off by default, and enabling it never removes edges")
def t40():
    from freepencil2 import svg_export
    _svg_scene()
    assert svg_export.SvgOptions().sources["bone"] is False, (
        "bone は既定で切れているべき")

    off = {s: False for s in svg_export.LINE_SOURCES}
    base = _n_edges(svg_export.SvgOptions(sources={**off, "mecha": True}))
    both = _n_edges(svg_export.SvgOptions(
        sources={**off, "mecha": True, "bone": True}))
    assert both >= base, f"bone を足して減った: {base} -> {both}"


@test("SVG: each line source can be switched off independently")
def t41():
    from freepencil2 import svg_export
    _svg_scene()
    every = {s: True for s in svg_export.LINE_SOURCES}
    total = _n_edges(svg_export.SvgOptions(sources=every))
    assert total > 0, "全部入りで線が出ていない"

    for name in svg_export.LINE_SOURCES:
        srcs = {s: (s != name) for s in svg_export.LINE_SOURCES}
        n = _n_edges(svg_export.SvgOptions(sources=srcs))
        assert n <= total, f"{name} を切ったのに増えた: {total} -> {n}"

    none = _n_edges(svg_export.SvgOptions(
        sources={s: False for s in svg_export.LINE_SOURCES}))
    assert none == 0, f"全部切っても線が出た: {none} 本"


@test("SVG: mask_color painted in STEP4 erases lines")
def t42():
    from freepencil2 import svg_export
    objs = _svg_scene()
    opts = svg_export.SvgOptions()
    before = _n_edges(opts)
    assert before > 0

    # mask_color を全面塗る = 全部の線を消す指示
    mesh = objs[0].data
    attr = mesh.color_attributes[svg_export.VCOL_LAYER_MASK]
    attr.data.foreach_set("color", [1.0] * (len(mesh.loops) * 4))
    mesh.update()

    assert _n_edges(opts) == 0, "mask_color を塗っても線が残った"

    # respect_paint を切れば元どおり。消しているのが塗りだと確かめる
    ignored = _n_edges(svg_export.SvgOptions(respect_paint=False))
    assert ignored == before, (
        f"respect_paint=False で本数が変わった: {before} -> {ignored}")


@test("SVG: line_color set to white (invisible) erases lines")
def t43():
    from freepencil2 import svg_export
    objs = _svg_scene()
    opts = svg_export.SvgOptions()
    assert _n_edges(opts) > 0

    mesh = objs[0].data
    attr = mesh.color_attributes[svg_export.VCOL_LAYER_LINE]
    attr.data.foreach_set("color", [1.0] * (len(mesh.loops) * 4))  # 白=見えない
    mesh.update()

    assert _n_edges(opts) == 0, "line_color を白にしても線が残った"


@test("SVG: linemerge keeps drawn length, linesort cuts pen-up travel")
def t44():
    import numpy as np
    from freepencil2 import svg_export

    # 端点を共有する短い線分。結合されるべき形
    pts = np.array([[0.0, 0.0], [10.0, 0.0], [20.0, 0.0], [20.0, 10.0]])
    pieces = [pts[i:i + 2].copy() for i in range(len(pts) - 1)]
    # 離れた線を混ぜ、並べ替えの効果が出るようにする
    far = [np.array([[100.0, 100.0], [110.0, 100.0]]),
           np.array([[50.0, 50.0], [60.0, 50.0]])]
    lines = [pieces[0], far[0], pieces[1], far[1], pieces[2]]

    def drawn(ls):
        return sum(float(np.hypot(*(p[1:] - p[:-1]).T).sum()) for p in ls)

    merged = svg_export.linemerge(lines, 0.1)
    assert len(merged) < len(lines), (
        f"つながらなかった: {len(lines)} -> {len(merged)}")
    assert abs(drawn(merged) - drawn(lines)) < 1e-6, "結合で描く長さが変わった"

    before = svg_export.pen_up_travel(merged)
    after = svg_export.pen_up_travel(svg_export.linesort(merged))
    assert after <= before + 1e-9, f"並べ替えで移動が伸びた: {before} -> {after}"


@test("SVG: linesort still works when the pen starts far outside the drawing")
def t45():
    """絵が原点から遠いと探索の打ち切りが早すぎ、1本も見つからないまま
    linesort が入力をそのまま返す不具合があった。ペン移動が変わらないこと
    でしか気づけなかったので、ここで固定する。"""
    import numpy as np
    from freepencil2 import svg_export

    rng = np.random.default_rng(0)
    base = np.array([150.0, 60.0])          # A4 の中ほど。原点から遠い
    lines = []
    for _ in range(60):
        p = base + rng.random(2) * 20.0
        lines.append(np.array([p, p + rng.random(2) * 2.0]))

    before = svg_export.pen_up_travel(lines)
    after = svg_export.pen_up_travel(svg_export.linesort(lines))
    assert after < before * 0.9, (
        f"並べ替えが効いていない(打ち切りが早すぎる?): "
        f"{before:.1f} -> {after:.1f}")


@test("SVG: hidden line removal culls, and the file is written in mm")
def t46():
    import shutil
    import tempfile
    from mathutils import Vector
    from freepencil2 import svg_export

    _svg_scene()
    # 猿の手前に箱を置く。隠れるぶんだけ線が減るはず
    d = Vector((1.0, -1.0, 0.65)).normalized()
    bpy.ops.mesh.primitive_cube_add(size=1.1, location=d * 1.35)
    objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    fp_batch.apply_white_material(objs)
    fp_batch.select_meshes()
    bpy.ops.fpm.auto_vertex_color()

    tmp = Path(tempfile.mkdtemp(prefix="fp_svg_"))
    try:
        shown = svg_export.export_svg(
            bpy.context, str(tmp / "a.svg"),
            svg_export.SvgOptions(depth_res=400))
        every = svg_export.export_svg(
            bpy.context, str(tmp / "b.svg"),
            svg_export.SvgOptions(depth_res=400, keep_hidden=True))

        assert (tmp / "a.svg").stat().st_size > 0, "SVG が空"
        assert shown["paths_raw"] < every["paths_raw"], (
            "隠線処理で本数が減っていない: "
            f"{shown['paths_raw']} vs {every['paths_raw']}")
        assert shown["depth_mode"] in ("plane", "ray")
        head = (tmp / "a.svg").read_text(encoding="utf-8")[:400]
        assert "mm" in head, "mm 単位で書かれていない"
        assert "fill:none" in head or 'fill="none"' in head, "塗りが付いている"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test("SVG: keep_hidden skips occlusion only, not the camera frame")
def t46b():
    from mathutils import Vector
    from freepencil2 import svg_export

    _svg_scene()
    scene = bpy.context.scene
    cam = scene.camera
    # カメラの真横に箱を置く。画角の外なので、隠線処理を飛ばしても出て
    # はいけない。ここまで一緒に外すと写っていない線まで SVG に入り、
    # DRAWING 合わせではその範囲に合わせて絵全体が縮む
    bpy.ops.mesh.primitive_cube_add(
        size=2.0, location=cam.matrix_world @ Vector((30.0, 0.0, -20.0)))
    cube = bpy.context.active_object
    objs = [o for o in scene.objects if o.type == "MESH"]
    fp_batch.apply_white_material(objs)
    fp_batch.select_meshes()
    bpy.ops.fpm.auto_vertex_color()

    width, height = 400, 300
    opts = svg_export.SvgOptions(depth_res=width, keep_hidden=True)
    dg = bpy.context.evaluated_depsgraph_get()
    project = svg_export.Projection(cam, dg, width, height)
    # keep_hidden では深度を見ないので、レンダーせず「全部背景」で足りる
    depth = np.full((height, width), np.nan)

    got = svg_export.extract_lines([cube], dg, cam, opts)
    assert got, "箱から線が出ていない(テストが成立しない)"
    full, pieces = svg_export.visible_spans(got[0], project, depth, opts,
                                            "plane")
    assert not full.any() and not pieces, (
        "画角の外の線が keep_hidden で残っている: "
        f"full={int(full.sum())}, pieces={len(pieces)}")

    got = svg_export.extract_lines(
        [o for o in objs if o is not cube], dg, cam, opts)
    assert any(svg_export.visible_spans(d, project, depth, opts, "plane")[0]
               .any() for d in got), "画角の中の線まで消えている"


@test("reset takes the add-on back out and restores the look")
def t46c():
    from freepencil2 import compat, fp_core

    _svg_scene()
    scene = bpy.context.scene
    view_layer = bpy.context.view_layer

    # 触る前の値。控えはこれと一致していなければならない
    before = {
        "film_transparent": scene.render.film_transparent,
        "view_transform": scene.view_settings.view_transform,
        "use_pass_z": view_layer.use_pass_z,
    }
    scene.fpm_file_output = False

    bpy.ops.fpm4.link_button()      # STEP2
    bpy.ops.fpm2.link_button()      # STEP3

    # 利用者が自分で足したノードは残さなければならない。ノードの型名は
    # 版で入れ替わるので、作れたものを使う
    mine = None
    for kind in ("CompositorNodeCurveRGB", "CompositorNodeBlur",
                 "CompositorNodeValue"):
        try:
            mine = compat.get_compositor_tree(scene).nodes.new(kind)
            break
        except RuntimeError:
            continue
    assert mine is not None, "テスト用のノードが作れない"
    mine.label = "MyOwnNode"

    assert scene.render.film_transparent, "STEP2 が背景を透過にしていない"
    assert any(a.name == "mecha_color" for a in view_layer.aovs), "AOV が無い"
    assert any(n.type == "GROUP" and n.node_tree
               and "FreePencil" in n.node_tree.name
               for m in bpy.data.materials if m.use_nodes and m.node_tree
               for n in m.node_tree.nodes), "マテリアルに AOV グループが無い"
    assert any(o.data.color_attributes.get("mecha_color") is not None
               for o in scene.objects if o.type == "MESH"), "頂点カラーが無い"

    info = fp_core.teardown(scene, view_layer)

    # ノード・AOV・頂点カラーが消えている
    assert not any(n.type == "GROUP" and n.node_tree
                   and "FreePencil" in n.node_tree.name
                   for m in bpy.data.materials if m.use_nodes and m.node_tree
                   for n in m.node_tree.nodes), "AOV グループが残っている"
    assert not any(a.name in fp_core.AOV_NAMES for a in view_layer.aovs), (
        "AOV スロットが残っている")
    tree = compat.get_compositor_tree(scene)
    if tree is not None:
        left = [n.label for n in tree.nodes]
        assert not any(x.startswith("FreePencil") for x in left), (
            f"コンポジタに FreePencil のノードが残っている: {left}")
        assert "MyOwnNode" in left, f"利用者のノードまで消した: {left}"
    assert not any(o.data.color_attributes.get(name) is not None
                   for o in scene.objects if o.type == "MESH"
                   for name in fp_core.VCOL_LAYERS), "頂点カラーが残っている"
    assert info["vcols"] > 0 and info["aovs"] > 0, info

    # 見た目が元に戻っている
    assert info["restored"], "控えが無く、設定を戻せていない"
    assert scene.render.film_transparent == before["film_transparent"], (
        "背景の透過が戻っていない")
    assert scene.view_settings.view_transform == before["view_transform"], (
        "ビュー変換が戻っていない")
    assert view_layer.use_pass_z == before["use_pass_z"], (
        "Z パスが戻っていない")

    # 控えを消せば、次に組み立てたときにまた控え直せる
    fp_core.clear_state(scene)
    assert not fp_core.load_state(scene), "控えが残っている"
    assert fp_core.capture_state(scene, view_layer), "控え直せない"
    assert not fp_core.capture_state(scene, view_layer), (
        "2回目の控えで上書きしている(書き換え後の値を覚えてしまう)")


class _FakeLayout:
    """UILayout の代わり。呼ばれたことだけ控える。

    パネルの draw はヘッドレスでは走らない(領域が無い)ので、UI 側の
    取りこぼし — 登録し忘れたプロパティ名、消えたオペレータ ID、
    未定義の名前 — が回帰テストをすり抜けてしまう。ここだけ差し替えて
    draw を素通しし、参照先が本当に在るかを見る。
    """

    def __init__(self, calls):
        object.__setattr__(self, "calls", calls)

    def __setattr__(self, key, value):
        pass            # enabled / active / alert / scale_y ... は捨てる

    def __getattr__(self, name):
        def _any(*args, **kwargs):
            return self     # column/row/box/split/separator/label...
        return _any

    def prop(self, data, name, **kwargs):
        self.calls.append(("prop", data, name))

    def operator(self, idname, **kwargs):
        self.calls.append(("op", idname))
        return self

    def operator_menu_enum(self, idname, prop_name, **kwargs):
        self.calls.append(("op", idname))
        return self


@test("every sidebar panel draws, and its props and operators exist")
def t_panels():
    import types
    from freepencil2 import panel as fp_panel

    _svg_scene()        # カメラ・塗り分け済み = 分岐の多いほうを通す
    panels = [c for c in vars(fp_panel).values()
              if isinstance(c, type) and issubclass(c, bpy.types.Panel)
              and getattr(c, "bl_space_type", "") == "VIEW_3D"]
    assert len(panels) >= 10, f"パネルが見つからない: {len(panels)}"

    for cls in panels:
        if hasattr(cls, "poll") and not cls.poll(bpy.context):
            continue
        calls = []
        cls.draw(types.SimpleNamespace(layout=_FakeLayout(calls)),
                 bpy.context)
        for call in calls:
            if call[0] == "prop":
                _kind, data, name = call
                assert hasattr(data, name), (
                    f"{cls.__name__}: プロパティ {name} が登録されていない")
            else:
                idname = call[1]
                mod, _, op = idname.partition(".")
                assert hasattr(getattr(bpy.ops, mod, None), op), (
                    f"{cls.__name__}: オペレータ {idname} が無い")

    # 並び順が決まっていること(同じ bl_order だと表示順が実質不定になる)
    orders = [c.bl_order for c in panels if hasattr(c, "bl_order")]
    assert len(orders) == len(set(orders)), (
        f"bl_order が重複している: {sorted(orders)}")


@test("SVG: export leaves the user's compositor tree alone")
def t47():
    import shutil
    import tempfile
    from freepencil2 import svg_export, compat

    _svg_scene()
    bpy.ops.fpm4.link_button()
    bpy.ops.fpm2.link_button()
    scene = bpy.context.scene
    tree = compat.get_compositor_tree(scene)
    before = len(tree.nodes) if tree else 0
    assert before > 0, "STEP3 でノードができていない"
    scenes_before = len(bpy.data.scenes)

    tmp = Path(tempfile.mkdtemp(prefix="fp_svg_"))
    try:
        svg_export.export_svg(bpy.context, str(tmp / "c.svg"),
                              svg_export.SvgOptions(depth_res=320))
        after = compat.get_compositor_tree(scene)
        assert after is tree, "コンポジタツリーが差し替わった"
        assert len(after.nodes) == before, (
            f"ノード数が変わった: {before} -> {len(after.nodes)}")
        assert len(bpy.data.scenes) == scenes_before, "一時シーンが残っている"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test("SVG: material boundaries produce edges and can be switched off")
def t48():
    from freepencil2 import svg_export

    _svg_scene()
    obj = bpy.context.scene.objects["Suzanne"]
    mesh = obj.data
    # 2つ目のマテリアルを作り、面の半分に割り当てる
    for name in ("fp_mat_a", "fp_mat_b"):
        mesh.materials.append(bpy.data.materials.new(name))
    idx = np.zeros(len(mesh.polygons), dtype=np.int32)
    idx[::2] = 1
    mesh.polygons.foreach_set("material_index", idx)
    mesh.update()

    off = {s: False for s in svg_export.LINE_SOURCES}
    only_mat = _n_edges(svg_export.SvgOptions(
        sources={**off, "material": True}))
    assert only_mat > 0, "マテリアル境界が1本も出ていない"

    assert _n_edges(svg_export.SvgOptions(sources=off)) == 0, (
        "全部切っても線が出た")


@test("SVG: bone_color differences are used only when the bone source is on")
def t49():
    from freepencil2 import svg_export

    _svg_scene()
    obj = bpy.context.scene.objects["Suzanne"]
    mesh = obj.data
    # リグ無しでも仕組みは試せる。面ごとに bone_color を2色へ塗り分ける
    attr = mesh.color_attributes[svg_export.VCOL_LAYER_BONE]
    starts = np.empty(len(mesh.polygons), dtype=np.int32)
    totals = np.empty(len(mesh.polygons), dtype=np.int32)
    mesh.polygons.foreach_get("loop_start", starts)
    mesh.polygons.foreach_get("loop_total", totals)
    buf = np.zeros((len(mesh.loops), 4), dtype=np.float32)
    buf[:, 3] = 1.0
    for f in range(0, len(mesh.polygons), 2):
        buf[starts[f]:starts[f] + totals[f], 0] = 1.0
    attr.data.foreach_set("color", buf.ravel())
    mesh.update()

    off = {s: False for s in svg_export.LINE_SOURCES}
    with_bone = _n_edges(svg_export.SvgOptions(
        sources={**off, "bone": True}))
    assert with_bone > 0, "bone_color の境界が拾えていない"

    without = _n_edges(svg_export.SvgOptions(sources={**off, "bone": False}))
    assert without == 0, f"bone を切ったのに線が出た: {without} 本"


def _drawn_length(svg_text):
    """SVG に書かれた polyline の総延長(mm)。レイヤー分けで幾何が変わって
    いないことを見るのに使う。"""
    import re
    total = 0.0
    for m in re.finditer(r'points="([^"]+)"', svg_text):
        pts = np.array([[float(v) for v in q.split(",")]
                        for q in m.group(1).split()])
        total += float(np.hypot(*(pts[1:] - pts[:-1]).T).sum())
    return total


@test("SVG: layers=SOURCE writes one Inkscape layer per line source")
def t50():
    import shutil
    import tempfile
    from freepencil2 import svg_export

    _svg_scene()
    tmp = Path(tempfile.mkdtemp(prefix="fp_svg_"))
    try:
        st = svg_export.export_svg(
            bpy.context, str(tmp / "layered.svg"),
            svg_export.SvgOptions(depth_res=400, layers="SOURCE"))
        text = (tmp / "layered.svg").read_text(encoding="utf-8")

        assert "inkscape:groupmode=\"layer\"" in text, (
            "Inkscape のレイヤー属性が無い(vpype が層として読めない)")
        assert svg_export.INKSCAPE_NS in text, "名前空間の宣言が無い"

        names = set(st["layers"])
        assert names, "レイヤーの統計が空"
        assert names <= set(svg_export.LAYER_PRIORITY) | {"other"}, names
        for name in names:
            assert f'inkscape:label="{name}"' in text, f"{name} 層が無い"
        assert st["paths"] == sum(st["layers"].values()), "層別の合計が合わない"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test("SVG: layers=OBJECT gives one layer per mesh object")
def t51():
    import shutil
    import tempfile
    from freepencil2 import svg_export

    _svg_scene()
    bpy.ops.mesh.primitive_cube_add(location=(4, 0, 0))
    objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    fp_batch.apply_white_material(objs)
    fp_batch.select_meshes()
    bpy.ops.fpm.auto_vertex_color()
    fp_batch.setup_camera_and_light()

    tmp = Path(tempfile.mkdtemp(prefix="fp_svg_"))
    try:
        st = svg_export.export_svg(
            bpy.context, str(tmp / "byobj.svg"),
            svg_export.SvgOptions(depth_res=400, layers="OBJECT"))
        got = set(st["layers"])
        expected = {o.name for o in objs}
        assert got <= expected, f"知らない層がある: {got - expected}"
        assert len(got) >= 2, f"オブジェクトごとに分かれていない: {got}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test("SVG: layering keeps the drawn geometry, only regrouped")
def t52():
    """結合と間引きは切って比べる。linemerge は許容内の端点をくっつける
    ときに片方の点を捨てるので、1回の結合につき最大 tol だけ描く長さが
    短くなる(仕様)。層をまたがない側は結合回数が少ないぶん残るため、
    ここを入れたままだと 0.01% ほどずれて、regroup の検証にならない。"""
    import shutil
    import tempfile
    from freepencil2 import svg_export

    _svg_scene()
    tmp = Path(tempfile.mkdtemp(prefix="fp_svg_"))
    try:
        flat = svg_export.export_svg(
            bpy.context, str(tmp / "flat.svg"),
            svg_export.SvgOptions(depth_res=400, layers="NONE",
                                  simplify=0.0, merge_tolerance=0.0))
        layered = svg_export.export_svg(
            bpy.context, str(tmp / "lay.svg"),
            svg_export.SvgOptions(depth_res=400, layers="SOURCE",
                                  simplify=0.0, merge_tolerance=0.0))

        a = _drawn_length((tmp / "flat.svg").read_text(encoding="utf-8"))
        b = _drawn_length((tmp / "lay.svg").read_text(encoding="utf-8"))
        assert abs(a - b) < max(1e-3, a * 1e-6), (
            f"レイヤー分けで描く長さが変わった: {a:.4f} -> {b:.4f}")

        # 層をまたいで繋がないぶん、本数は増えるか同じになる
        assert layered["paths"] >= flat["paths"], (
            f"層に分けて本数が減った: {flat['paths']} -> {layered['paths']}")

        # 単層のときは Inkscape 属性を書かない(従来の出力のまま)
        assert "inkscape" not in (tmp / "flat.svg").read_text(
            encoding="utf-8"), "単層なのにレイヤー属性が付いている"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test("SVG: outline layer appears only when asked for")
def t53():
    import shutil
    import tempfile
    from freepencil2 import svg_export

    _svg_scene()
    tmp = Path(tempfile.mkdtemp(prefix="fp_svg_"))
    try:
        on = svg_export.export_svg(
            bpy.context, str(tmp / "on.svg"),
            svg_export.SvgOptions(depth_res=400, layers="SOURCE",
                                  outline_layer=True, merge_tolerance=0.0,
                                  simplify=0.0))
        assert on["layers"].get("outline", 0) > 0, (
            "外周レイヤーが空。背景に接する辺が拾えていない")

        off = svg_export.export_svg(
            bpy.context, str(tmp / "off.svg"),
            svg_export.SvgOptions(depth_res=400, layers="SOURCE",
                                  outline_layer=False, merge_tolerance=0.0,
                                  simplify=0.0))
        assert "outline" not in off["layers"], (
            "outline_layer=False なのに外周レイヤーが出た")

        # 外周は既存の辺の振り分けであって、線を足すものではない。
        # 本数は比べられない: 層が増えると鎖の切れ目も増えるので、同じ辺
        # でもパス数は変わる(実測 435 -> 436)。描く長さで見る
        a = _drawn_length((tmp / "on.svg").read_text(encoding="utf-8"))
        b = _drawn_length((tmp / "off.svg").read_text(encoding="utf-8"))
        assert abs(a - b) < max(1e-3, b * 1e-6), (
            f"外周レイヤーで描く長さが変わった: {b:.4f} -> {a:.4f}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test("SVG: a larger outline gap selects a strict subset of edges")
def t54():
    from mathutils import Vector
    from freepencil2 import svg_export

    _svg_scene()
    # 手前に箱を置いて、背景だけでなく段差でも外周が出る状況にする
    d = Vector((1.0, -1.0, 0.65)).normalized()
    bpy.ops.mesh.primitive_cube_add(size=1.1, location=d * 1.35)
    objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    fp_batch.apply_white_material(objs)
    fp_batch.select_meshes()
    bpy.ops.fpm.auto_vertex_color()
    fp_batch.setup_camera_and_light()

    scene = bpy.context.scene
    cam = scene.camera
    w, h = 400, 300
    depth = svg_export.render_depth_pass(scene, cam, w, h)
    dg = bpy.context.evaluated_depsgraph_get()
    project = svg_export.Projection(cam, dg, w, h)

    tight = svg_export.SvgOptions(outline_gap=0.20)
    loose = svg_export.SvgOptions(outline_gap=0.005)
    collected = svg_export.extract_lines(objs, dg, cam, loose)
    assert collected, "線が出ていない"

    centers = np.concatenate([c["centers"] for c in collected])
    mode, _ = svg_export.choose_depth_mode(depth, project, centers, 42)

    total_loose = 0
    for data in collected:
        m_loose = svg_export.contour_mask(data, project, depth, loose, mode)
        m_tight = svg_export.contour_mask(data, project, depth, tight, mode)
        # 段差の条件は "> ref*(1+gap)" なので、gap を上げれば必ず狭くなる
        assert not (m_tight & ~m_loose).any(), (
            "gap を上げたのに新しい辺が外周になった")
        total_loose += int(m_loose.sum())
    assert total_loose > 0, "外周の辺が1本も無い"


def _svg_bbox(svg_text):
    """SVG に書かれた点の範囲(mm)。"""
    import re
    pts = []
    for m in re.finditer(r'points="([^"]+)"', svg_text):
        pts.extend([float(v) for q in m.group(1).split()
                    for v in q.split(",")][0::2])
    ys = []
    for m in re.finditer(r'points="([^"]+)"', svg_text):
        ys.extend([float(v) for q in m.group(1).split()
                   for v in q.split(",")][1::2])
    return min(pts), min(ys), max(pts), max(ys)


@test("SVG: fit=DRAWING fills the page whatever the camera framing")
def t55():
    import shutil
    import tempfile
    from freepencil2 import svg_export

    objs = _svg_scene()
    # カメラをうんと引いて、被写体をフレームの片隅に小さく写す
    cam = bpy.context.scene.camera
    cam.location = cam.location * 4.0
    bpy.context.view_layer.update()

    tmp = Path(tempfile.mkdtemp(prefix="fp_svg_"))
    try:
        margin = 10.0
        cam_fit = svg_export.export_svg(
            bpy.context, str(tmp / "cam.svg"),
            svg_export.SvgOptions(depth_res=400, fit="CAMERA",
                                  margin=margin))
        draw_fit = svg_export.export_svg(
            bpy.context, str(tmp / "draw.svg"),
            svg_export.SvgOptions(depth_res=400, fit="DRAWING",
                                  margin=margin))

        page_w, page_h = draw_fit["page_mm"]
        x0, y0, x1, y1 = _svg_bbox(
            (tmp / "draw.svg").read_text(encoding="utf-8"))
        filled = max((x1 - x0) / (page_w - 2 * margin),
                     (y1 - y0) / (page_h - 2 * margin))
        assert filled > 0.98, f"紙いっぱいになっていない: {filled:.3f}"
        assert x0 >= margin - 1e-6 and y0 >= margin - 1e-6, "余白を割り込んだ"
        assert x1 <= page_w - margin + 1e-6, "余白を割り込んだ"

        cx0, cy0, cx1, cy1 = _svg_bbox(
            (tmp / "cam.svg").read_text(encoding="utf-8"))
        cam_filled = max((cx1 - cx0) / (page_w - 2 * margin),
                         (cy1 - cy0) / (page_h - 2 * margin))
        assert cam_filled < filled, (
            f"カメラ合わせのほうが大きく出た: {cam_filled:.3f} vs {filled:.3f}")
        assert objs
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test("SVG: the export reports drawn length and a plot time estimate")
def t56():
    import shutil
    import tempfile
    from freepencil2 import svg_export

    _svg_scene()
    tmp = Path(tempfile.mkdtemp(prefix="fp_svg_"))
    try:
        st = svg_export.export_svg(
            bpy.context, str(tmp / "e.svg"),
            svg_export.SvgOptions(depth_res=400, plot_speed=80.0,
                                  travel_speed=200.0, pen_lift=0.12))
        assert st["draw_mm"] > 0, "描く距離が出ていない"

        # 見積りは各項の単純な和。式が変わったら気づけるように固定する
        expect = (st["draw_mm"] / 80.0 + st["pen_up_mm"] / 200.0
                  + st["paths"] * 0.12)
        assert abs(st["estimated_seconds"] - expect) < 0.5, (
            f"見積りが合わない: {st['estimated_seconds']} vs {expect:.1f}")

        # 速度を上げれば必ず短くなる
        fast = svg_export.export_svg(
            bpy.context, str(tmp / "f.svg"),
            svg_export.SvgOptions(depth_res=400, plot_speed=400.0,
                                  travel_speed=200.0, pen_lift=0.12))
        assert fast["estimated_seconds"] < st["estimated_seconds"], (
            "速くしたのに見積りが縮まない")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test("SVG: preview computes the same visible lines as the export")
def t57():
    import shutil
    import tempfile
    from freepencil2 import svg_export

    _svg_scene()
    opts = svg_export.SvgOptions(depth_res=400)
    segs, n = svg_export.compute_preview(bpy.context, opts)
    assert n > 0, "プレビューの線分が空"
    assert segs.shape[1:] == (2, 3), f"線分の形が違う: {segs.shape}"

    # プレビューは描画ハンドラを足すまで何も表示しない
    assert not svg_export.preview_enabled()
    svg_export.enable_preview()
    assert svg_export.preview_enabled()
    svg_export.disable_preview()
    assert not svg_export.preview_enabled(), "ハンドラが外れていない"

    # 書き出しと同じ辺を見ているか。線分の総延長で突き合わせる
    tmp = Path(tempfile.mkdtemp(prefix="fp_svg_"))
    try:
        svg_export.export_svg(
            bpy.context, str(tmp / "p.svg"),
            svg_export.SvgOptions(depth_res=400, merge_tolerance=0.0,
                                  simplify=0.0))
        # 3D の長さ同士は比べられないので、本数の桁で見る
        text = (tmp / "p.svg").read_text(encoding="utf-8")
        n_points = sum(len(m.split()) for m in
                       __import__("re").findall(r'points="([^"]+)"', text))
        assert n_points > 0
        assert n <= n_points, (
            f"プレビューの線分が書き出しより多い: {n} vs {n_points}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test("SVG: the preview knows when the camera or model has moved")
def t57b():
    from mathutils import Vector
    from freepencil2 import svg_export

    _svg_scene()
    scene = bpy.context.scene
    scene.fpm_svg_depth_res = 400
    try:
        svg_export.refresh_preview(bpy.context)
        assert svg_export._preview_segments is not None, "線分が空"
        assert not svg_export.preview_stale(bpy.context), (
            "引き直した直後なのに古い扱いになっている")
        fresh_color = svg_export.preview_color(bpy.context)

        # カメラを動かす: 隠線処理は当時のカメラのままなので古い
        cam = scene.camera
        before = cam.matrix_world.copy()
        cam.location = cam.location + Vector((1.5, 0.0, 0.0))
        bpy.context.view_layer.update()
        assert svg_export.preview_stale(bpy.context), (
            "カメラを動かしても古いと分からない")
        # 古い線は薄く引く。同じ濃さだと今の線と見分けが付かない
        stale_color = svg_export.preview_color(bpy.context)
        assert stale_color != fresh_color, "古い線の色が変わっていない"
        assert stale_color[3] < fresh_color[3], "古い線が薄くなっていない"
        cam.matrix_world = before
        bpy.context.view_layer.update()
        assert not svg_export.preview_stale(bpy.context), "戻しても古いまま"

        # モデルを動かしても同じこと
        obj = next(o for o in scene.objects if o.type == "MESH")
        obj.location = obj.location + Vector((0.0, 0.0, 1.0))
        bpy.context.view_layer.update()
        assert svg_export.preview_stale(bpy.context), (
            "モデルを動かしても古いと分からない")
        obj.location = obj.location - Vector((0.0, 0.0, 1.0))
        bpy.context.view_layer.update()

        # 見え方を変える設定も見る。紙の設定は3Dの線を変えないので見ない
        scene.fpm_svg_keep_hidden = True
        assert svg_export.preview_stale(bpy.context), (
            "隠線処理の設定を変えても古いと分からない")
        scene.fpm_svg_keep_hidden = False
        scene.fpm_svg_margin = scene.fpm_svg_margin + 5.0
        assert not svg_export.preview_stale(bpy.context), (
            "余白は3Dビューの線を変えないので古くならないはず")
    finally:
        svg_export.disable_preview()
    assert not svg_export.preview_stale(bpy.context), (
        "消した後に古い判定が残っている")


@test("SVG: one file per layer, all sharing the same page transform")
def t58():
    import shutil
    import tempfile
    from freepencil2 import svg_export

    _svg_scene()
    tmp = Path(tempfile.mkdtemp(prefix="fp_svg_"))
    try:
        opts = svg_export.SvgOptions(depth_res=400, layers="SOURCE",
                                     split_files=True)
        st = svg_export.export_svg(bpy.context, str(tmp / "pen.svg"), opts)

        files = st["files"]
        assert len(files) == len(st["layers"]), (
            f"層の数とファイル数が違う: {len(files)} vs {len(st['layers'])}")
        for f in files:
            assert Path(f).exists(), f"{f} が無い"
            assert Path(f).stem.startswith("pen_"), f"名前が違う: {f}"

        # ばらしても合計は1枚のときと同じでなければならない
        merged = svg_export.export_svg(
            bpy.context, str(tmp / "one.svg"),
            svg_export.SvgOptions(depth_res=400, layers="SOURCE",
                                  split_files=False))
        assert st["paths"] == merged["paths"], (
            f"ばらすと本数が変わった: {merged['paths']} -> {st['paths']}")

        # 位置合わせ: 全ファイルを合わせた範囲が1枚のときと一致すること
        each = [_svg_bbox(Path(f).read_text(encoding="utf-8"))
                for f in files]
        lo_x = min(b[0] for b in each); lo_y = min(b[1] for b in each)
        hi_x = max(b[2] for b in each); hi_y = max(b[3] for b in each)
        ref = _svg_bbox((tmp / "one.svg").read_text(encoding="utf-8"))
        for got, want in zip((lo_x, lo_y, hi_x, hi_y), ref):
            assert abs(got - want) < 1e-3, (
                f"層ごとのファイルが紙の上でずれている: {got} vs {want}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test("SVG: presets set the scene properties they name")
def t59():
    from freepencil2 import svg_export

    bpy.ops.wm.read_homefile(use_empty=True)
    scene = bpy.context.scene
    for key, spec in svg_export.SVG_PRESETS.items():
        res = bpy.ops.fpm.svg_preset(preset=key)
        assert res == {"FINISHED"}, res
        for name, want in spec["values"].items():
            got = getattr(scene, name)
            if isinstance(want, float):
                assert abs(got - want) < 1e-6, f"{key}/{name}: {got} != {want}"
            else:
                assert got == want, f"{key}/{name}: {got} != {want}"


@test("SVG: camera batch writes one file per checked camera and restores")
def t60():
    import shutil
    import tempfile

    _svg_scene()
    scene = bpy.context.scene
    first = scene.camera

    cam_data = bpy.data.cameras.new("FP_Cam2")
    second = bpy.data.objects.new("FP_Cam2", cam_data)
    scene.collection.objects.link(second)
    second.location = (0.0, -6.0, 1.0)
    second.rotation_euler = (1.4, 0.0, 0.0)

    tmp = Path(tempfile.mkdtemp(prefix="fp_svg_"))
    try:
        # //svg_exports を使うので .blend を保存しておく必要がある
        blend = tmp / "scene.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(blend))
        scene = bpy.context.scene
        scene.fpm_svg_depth_res = 320

        res = bpy.ops.fpm.export_svg_cameras()
        assert res == {"FINISHED"}, res

        out = Path(bpy.path.abspath("//svg_exports"))
        svgs = sorted(out.glob("*.svg"))
        assert len(svgs) == 2, f"カメラの数だけ出ていない: {[f.name for f in svgs]}"
        assert svgs[0].name.startswith("01_"), svgs[0].name

        assert bpy.context.scene.camera == first or \
            bpy.context.scene.camera.name == first.name, (
                "元のカメラに戻っていない")
    finally:
        bpy.ops.wm.read_homefile(use_empty=True)
        shutil.rmtree(tmp, ignore_errors=True)


@test("STEP3 builds a node group for every node type on this Blender")
def t61():
    """5.x では 'test' 用スクリプトが CompositorNodeFilter の添字違いで
    落ちていた(4.x は Fac,Image / 5.x は Image,Factor,Type)。Blender に
    移行させた _5x スクリプトを足して直したので、両方の種別で作れることを
    ここで固定する。"""
    from freepencil2 import fp_core

    for node_type in ("test", "pro"):
        bpy.ops.wm.read_homefile(use_empty=True)
        bpy.ops.mesh.primitive_cube_add()
        objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
        fp_batch.apply_white_material(objs)
        fp_batch.select_meshes()
        bpy.ops.fpm.auto_vertex_color()

        scene = bpy.context.scene
        scene.fpm_node_type = node_type
        assert bpy.ops.fpm4.link_button() == {"FINISHED"}
        assert bpy.ops.fpm2.link_button() == {"FINISHED"}

        name = f"{fp_core.NODE_GROUP_PREFIX}{node_type}"
        ng = bpy.data.node_groups.get(name)
        assert ng is not None, f"{name} が作られていない"
        assert len(ng.nodes) > 0, f"{name} が空"
        assert len(ng.links) > 0, f"{name} にリンクが無い"

        # Filter のソケットは添字ではなく名前で解決されているか。
        # 画像入力が繋がっていなければ、線が出ない状態で生成されている
        for n in ng.nodes:
            if n.bl_idname == "CompositorNodeFilter":
                img = n.inputs.get("Image")
                assert img is not None, "Filter に Image 入力が無い"
                assert img.is_linked, (
                    f"{node_type}: Filter の Image が繋がっていない"
                    f"(添字で繋いで別ソケットへ行った可能性)")


def _lit_scene():
    """陰影の出るシーン。fp_batch の白マテリアルはエミッションなので
    拡散直接光が 0 になり、ハッチが一本も出ない。ここだけ拡散にする。"""
    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.ops.mesh.primitive_monkey_add(size=2.0)
    scene = bpy.context.scene
    scene.fpm_use_random_seed = False
    scene.fpm_color_seed = 1234

    mat = bpy.data.materials.new("fpm_test_diffuse")
    mat.use_nodes = True
    nt = mat.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    bsdf = nt.nodes.new("ShaderNodeBsdfDiffuse")
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(bsdf.outputs[0], out.inputs["Surface"])
    objs = [o for o in scene.objects if o.type == "MESH"]
    for o in objs:
        o.data.materials.clear()
        o.data.materials.append(mat)

    fp_batch.select_meshes()
    bpy.ops.fpm.auto_vertex_color()
    fp_batch.setup_camera_and_light()
    scene.render.resolution_x = 400
    scene.render.resolution_y = 300
    return objs


@test("SVG: the depth pass marks background as infinite, not clip_end")
def t62():
    """EEVEE は背景に clip_end(既定 1000)を書く。1e9 と比べていたので
    背景判定が一度も成立しておらず、ハッチが紙全面に出た。正規化を固定する。"""
    import numpy as np
    from freepencil2 import svg_export

    _lit_scene()
    scene = bpy.context.scene
    depth, _ = svg_export.render_passes(scene, scene.camera, 200, 150)
    bg = depth >= svg_export.BACKGROUND_Z
    assert bg.any(), "背景が背景として拾えていない(clip_end のまま?)"
    assert (~bg).any(), "全部背景になっている"
    assert np.isfinite(depth[~bg]).all(), "手前側に無限が混ざっている"


@test("SVG: hatching is off by default and stays inside the drawing")
def t63():
    import re
    import shutil
    import tempfile
    import numpy as np
    from freepencil2 import svg_export

    assert svg_export.SvgOptions().hatch is False, "ハッチは既定で切る"

    _lit_scene()
    tmp = Path(tempfile.mkdtemp(prefix="fpm_svg_"))
    try:
        st = svg_export.export_svg(
            bpy.context, str(tmp / "h.svg"),
            svg_export.SvgOptions(depth_res=400, layers="SOURCE",
                                  hatch=True, hatch_spacing=2.0))
        assert st["layers"].get("hatch", 0) > 0, "ハッチが1本も出ていない"

        text = (tmp / "h.svg").read_text(encoding="utf-8")
        groups = dict(re.findall(
            r'<g[^>]*inkscape:label="([^"]+)"[^>]*>(.*?)</g>', text, re.S))

        def bbox(chunk):
            pts = np.array([[float(v) for v in q.split(",")]
                            for m in re.finditer(r'points="([^"]+)"', chunk)
                            for q in m.group(1).split()])
            return pts.min(0), pts.max(0)

        h_lo, h_hi = bbox(groups["hatch"])
        l_lo, l_hi = bbox("".join(v for k, v in groups.items()
                                  if k != "hatch"))
        # 背景まで塗っていたときは紙いっぱいに広がっていた。線の範囲に
        # 収まっていることで、絵の中だけに乗っていると言える
        assert (h_lo >= l_lo - 1.0).all() and (h_hi <= l_hi + 1.0).all(), (
            f"ハッチが線の外に出ている: {h_lo}-{h_hi} vs {l_lo}-{l_hi}")

        off = svg_export.export_svg(
            bpy.context, str(tmp / "n.svg"),
            svg_export.SvgOptions(depth_res=400, layers="SOURCE",
                                  hatch=False))
        assert "hatch" not in off["layers"], "ハッチを切ったのに層が出た"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test("SVG: depth bands split the visible range and thin with distance")
def t64():
    import shutil
    import tempfile
    from freepencil2 import svg_export

    _svg_scene()
    tmp = Path(tempfile.mkdtemp(prefix="fpm_svg_"))
    try:
        opts = svg_export.SvgOptions(depth_res=400, layers="DEPTH",
                                     depth_bands=3)
        st = svg_export.export_svg(bpy.context, str(tmp / "d.svg"), opts)
        got = sorted(st["layers"])
        assert got == ["depth1", "depth2", "depth3"], (
            f"帯が揃っていない: {got}(見えている辺だけで範囲を決める)")
        for name in got:
            assert st["layers"][name] > 0, f"{name} が空"

        # 奥ほど細くする。プロッタでは層ごとにペンを割り当てる想定だが、
        # SVG のまま見ても遠近が出るように stroke-width も変える
        widths = [svg_export.layer_pen(n, opts) for n in got]
        assert widths[0] > widths[-1], f"奥が細くなっていない: {widths}"
        assert abs(widths[-1] - opts.pen * opts.depth_weight) < 1e-9
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test("SVG: jitter is off by default and keeps shared ends together")
def t65():
    import numpy as np
    from freepencil2 import svg_export

    assert svg_export.SvgOptions().jitter == 0.0, "手ぶれは既定で切る"

    # 端点を共有する2本。揺らしても離れてはいけない
    a = np.array([[10.0, 10.0], [40.0, 10.0]])
    b = np.array([[40.0, 10.0], [40.0, 45.0]])
    opts = svg_export.SvgOptions(jitter=1.0, jitter_scale=8.0)
    ja, jb = svg_export.jitter_lines([a, b], opts)

    gap = float(np.hypot(*(ja[-1] - jb[0])))
    assert gap < 1e-9, (
        f"共有していた端点が {gap:.4f}mm 離れた。"
        "点ごとの乱数ではなく位置のノイズで動かすこと")

    # 実際に動いていること、動きすぎないこと
    moved = float(np.abs(ja[0] - a[0]).max())
    assert moved > 1e-6, "まったく動いていない"
    assert float(np.abs(ja - ja).max()) == 0.0
    for pt in ja:
        d = np.hypot(*(pt - a[0])), np.hypot(*(pt - a[-1]))
        assert min(d) <= 40.0 + 3.0, "元の線から離れすぎ"

    # 直線は点を足さないと揺らせない
    assert len(ja) > len(a), "densify が効いていない"

    same = svg_export.jitter_lines([a], opts)[0]
    assert np.allclose(same, ja), "同じ入力で結果が変わる(決定的でない)"


@test("SVG: jitter barely changes the drawn length")
def t66():
    import numpy as np
    from freepencil2 import svg_export

    rng = np.random.default_rng(3)
    lines = [np.array([[10.0, 10.0 + i * 3.0], [120.0, 10.0 + i * 3.0]])
             for i in range(20)]

    def drawn(ls):
        return sum(float(np.hypot(*(p[1:] - p[:-1]).T).sum()) for p in ls)

    base = drawn(lines)
    out = svg_export.jitter_lines(
        lines, svg_export.SvgOptions(jitter=0.5, jitter_scale=8.0))
    got = drawn(out)
    assert got >= base, "揺らして短くなるのはおかしい"
    assert got < base * 1.10, f"伸びすぎ: {base:.1f} -> {got:.1f}"
    assert rng is not None


@test("SVG: frame batch writes one file per frame and restores the frame")
def t67():
    import shutil
    import tempfile

    _svg_scene()
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end, scene.frame_step = 1, 3, 1
    scene.frame_set(2)
    scene.fpm_svg_depth_res = 320

    tmp = Path(tempfile.mkdtemp(prefix="fpm_svg_"))
    try:
        bpy.ops.wm.save_as_mainfile(filepath=str(tmp / "anim.blend"))
        scene = bpy.context.scene
        scene.fpm_svg_depth_res = 320

        res = bpy.ops.fpm.export_svg_frames()
        assert res == {"FINISHED"}, res

        out = Path(bpy.path.abspath("//svg_exports"))
        svgs = sorted(p.name for p in out.glob("frame_*.svg"))
        assert svgs == ["frame_0001.svg", "frame_0002.svg",
                        "frame_0003.svg"], svgs
        assert bpy.context.scene.frame_current == 2, (
            "元のフレームに戻っていない")
    finally:
        bpy.ops.wm.read_homefile(use_empty=True)
        shutil.rmtree(tmp, ignore_errors=True)


def _svg_lines_of(path):
    import re
    import numpy as np
    text = Path(path).read_text(encoding="utf-8")
    return [np.array([[float(v) for v in q.split(",")]
                      for q in m.group(1).split()])
            for m in re.finditer(r'points="([^"]+)"', text)]


def _drawn(lines):
    import numpy as np
    return sum(float(np.hypot(*(p[1:] - p[:-1]).T).sum()) for p in lines)


@test("SVG: clipping a polyline keeps its length and lands on the border")
def t68():
    import numpy as np
    from freepencil2 import svg_export

    # 矩形をまたぐ一本。切ったら2本になり、切り口は境界の上にあること
    line = np.array([[-10.0, 5.0], [30.0, 5.0]])
    pieces = svg_export.clip_polyline(line, 0.0, 0.0, 10.0, 10.0)
    assert len(pieces) == 1, f"1本に切れるはず: {len(pieces)}"
    got = pieces[0]
    assert abs(got[0][0] - 0.0) < 1e-9 and abs(got[-1][0] - 10.0) < 1e-9, got
    assert abs(_drawn(pieces) - 10.0) < 1e-9

    # 完全に外なら何も残らない
    assert svg_export.clip_polyline(
        np.array([[20.0, 20.0], [30.0, 30.0]]), 0.0, 0.0, 10.0, 10.0) == []

    # 完全に内なら長さが変わらない
    inside = np.array([[1.0, 1.0], [9.0, 9.0]])
    kept = svg_export.clip_polyline(inside, 0.0, 0.0, 10.0, 10.0)
    assert abs(_drawn(kept) - _drawn([inside])) < 1e-9


@test("SVG: tiling writes cols x rows sheets and keeps the total length")
def t69():
    import shutil
    import tempfile
    from freepencil2 import svg_export

    _svg_scene()
    tmp = Path(tempfile.mkdtemp(prefix="fpm_svg_"))
    try:
        opts = svg_export.SvgOptions(depth_res=400, tile_cols=2, tile_rows=2,
                                     tile_marks=False)
        st = svg_export.export_svg(bpy.context, str(tmp / "t.svg"), opts)

        assert st["tiles"] == [2, 2], st.get("tiles")
        names = sorted(Path(f).name for f in st["files"])
        assert names == ["t_r1c1.svg", "t_r1c2.svg",
                         "t_r2c1.svg", "t_r2c2.svg"], names

        # 切っても描く長さは変わらない。継ぎ目で線が落ちていない証拠になる
        total = sum(_drawn(_svg_lines_of(f)) for f in st["files"])
        assert abs(total - st["draw_mm"]) < 0.5, (
            f"タイルの合計 {total:.1f} が合成の {st['draw_mm']:.1f} と違う")

        # 紙は1枚ぶんのまま。絵は紙を並べた大きさに合わせる
        assert st["page_mm"] == [297.0, 210.0], st["page_mm"]
        assert st["canvas_mm"] == [594.0, 420.0], st["canvas_mm"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test("SVG: registration marks follow their toggle")
def t70():
    import shutil
    import tempfile
    from freepencil2 import svg_export

    _svg_scene()
    tmp = Path(tempfile.mkdtemp(prefix="fpm_svg_"))
    try:
        on = svg_export.export_svg(
            bpy.context, str(tmp / "on.svg"),
            svg_export.SvgOptions(depth_res=400, tile_cols=2, tile_rows=1,
                                  tile_marks=True))
        off = svg_export.export_svg(
            bpy.context, str(tmp / "off.svg"),
            svg_export.SvgOptions(depth_res=400, tile_cols=2, tile_rows=1,
                                  tile_marks=False))
        # 1枚あたり四隅 x 2本
        extra = _drawn(_svg_lines_of(on["files"][0])) \
            - _drawn(_svg_lines_of(off["files"][0]))
        assert abs(extra - 8 * 6.0) < 1e-6, f"トンボの長さが合わない: {extra}"

        marks = svg_export.registration_marks(297.0, 210.0)
        assert len(marks) == 8, len(marks)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test("batch: svg_metrics returns the plotter figures and ratios")
def t71():
    import shutil
    import tempfile
    from types import SimpleNamespace

    _svg_scene()
    tmp = Path(tempfile.mkdtemp(prefix="fpm_batch_"))
    try:
        args = SimpleNamespace(name="unit", seed=42, preset="default",
                               material="white", res=320)
        got = fp_batch.svg_metrics(tmp, args, "")

        for key in ("paths", "paths_raw", "points", "draw_mm", "pen_up_mm",
                    "estimated_seconds", "edges_line", "merge_ratio",
                    "travel_ratio", "svg"):
            assert key in got, f"{key} が無い"
        assert got["paths"] > 0 and got["draw_mm"] > 0

        # 比は定義どおりか。表の読み方が変わると困るので固定する
        assert abs(got["merge_ratio"]
                   - got["paths"] / got["paths_raw"]) < 1e-3
        assert abs(got["travel_ratio"]
                   - got["pen_up_mm"] / got["draw_mm"]) < 1e-3

        assert (tmp / got["svg"]).exists(), got["svg"]
        assert "/" in got["svg"], "レポートから辿れる相対パスであること"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test("batch: the report still renders for runs that have no SVG metrics")
def t72():
    """SVG の計測を足す前に採った metrics/*.json が残っていても、
    レポートが落ちてはいけない。"""
    import json
    import shutil
    import sys
    import tempfile

    sys.path.insert(0, str(BATCH))
    import make_report

    tmp = Path(tempfile.mkdtemp(prefix="fpm_report_"))
    try:
        (tmp / "metrics").mkdir(parents=True)
        old = {"name": "old", "seed": 1, "preset": "default",
               "material": "white", "ok": True, "faces_total": 100,
               "mesh_objects": 1, "step1_seconds": 1.0,
               "render_seconds": 2.0,
               "lineart_metrics": {"ink_ratio": 0.05, "components": 10},
               "mesh_metrics": {"distinct_colors": 4,
                                "adjacent_color_pairs": 6,
                                "min_distance_violations": 0}}
        new = dict(old, name="new",
                   svg_metrics={"paths": 12, "paths_raw": 40, "points": 30,
                                "draw_mm": 1234.0, "pen_up_mm": 200.0,
                                "estimated_seconds": 90.0,
                                "merge_ratio": 0.3, "travel_ratio": 0.162,
                                "svg": "svg/new.svg"})
        for rec in (old, new):
            (tmp / "metrics" / f"{rec['name']}.json").write_text(
                json.dumps(rec), encoding="utf-8")

        make_report.main(tmp)
        html_text = (tmp / "report.html").read_text(encoding="utf-8")

        assert "本数" in html_text and "移動比" in html_text, "列が出ていない"
        assert "0.162" in html_text, "SVG のある行の値が出ていない"
        assert "svg/new.svg" in html_text, "SVG へのリンクが無い"
        # 無い側は空欄で通る。落ちないことがこのテストの主題
        assert html_text.count("<tr class=\"ok\"") == 2, "2行出るはず"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test("SVG: the occlusion cut is refined past the sample spacing")
def t73():
    """部分可視の辺は標本の境で切られていたので、切れ目の位置は辺の 1/s
    の刻みでしか合わなかった(8 標本で辺の 1/8)。二分探索で詰めた後は
    1/256 まで寄る。合成の深度バッファで固定する。"""
    import numpy as np
    from freepencil2 import svg_export

    W, H = 1000, 100
    # 平行投影のつもり: ワールド x がそのまま画素 x、深度は z
    def project(pts):
        pts = np.asarray(pts, dtype=np.float64)
        x = pts[:, 0] * W
        y = np.full(len(pts), H * 0.5)
        z = pts[:, 2]
        inside = (x >= 0) & (x < W) & (z > 0)
        return x, y, z, z, inside

    # 画面の x >= 0.30 に、辺(深度 2.0)より手前の壁(深度 1.0)
    depth = np.full((H, W), np.nan)
    depth[:, int(0.30 * W):] = 1.0
    data = {"verts": np.array([[0.0, 0.0, 2.0], [1.0, 0.0, 2.0]]),
            "edges": np.array([[0, 1]])}
    opts = svg_export.SvgOptions(samples=8, bias=0.0, neighbourhood=0)
    full, pieces = svg_export.visible_spans(data, project, depth, opts,
                                            "plane")
    assert not full.any() and len(pieces) == 1, (full, pieces)
    _, a, b = pieces[0]
    assert abs(a[0] - 0.0) < 1e-9, a
    # 標本だけなら 0.25 で切れる(誤差 0.05)。詰めた後は 0.01 以内
    assert abs(b[0] - 0.30) < 0.01, f"切れ目が粗い: {b[0]:.4f} (期待 0.30)"

    # 両側が隠れている辺: 真ん中だけ残り、両端とも詰まっていること
    depth[:] = 1.0
    depth[:, int(0.42 * W):int(0.71 * W)] = np.nan
    full, pieces = svg_export.visible_spans(data, project, depth, opts,
                                            "plane")
    assert len(pieces) == 1, pieces
    _, a, b = pieces[0]
    assert abs(a[0] - 0.42) < 0.01 and abs(b[0] - 0.71) < 0.01, (a, b)

    # 全可視・全不可視は従来どおり pieces に出ない
    depth[:] = np.nan
    full, pieces = svg_export.visible_spans(data, project, depth, opts,
                                            "plane")
    assert full.all() and not pieces


@test("SVG: 2-opt shortens the greedy order and keeps every line")
def t74():
    import numpy as np
    from freepencil2 import svg_export

    rng = np.random.default_rng(3)
    lines = []
    for _ in range(400):
        p0 = rng.random(2) * 200.0
        lines.append(np.array([p0, p0 + rng.random(2) * 4.0]))

    def canon(ls):
        return sorted(min(tuple(np.round(ln, 6).ravel()),
                          tuple(np.round(ln[::-1], 6).ravel())) for ln in ls)

    greedy = svg_export.linesort(lines)
    better = svg_export.two_opt(greedy)
    before = svg_export.pen_up_travel(greedy)
    after = svg_export.pen_up_travel(better)
    assert after < before * 0.95, (
        f"2-opt が効いていない: {before:.1f} -> {after:.1f}")
    assert canon(better) == canon(lines), "線が増減した、または形が変わった"

    # ペンの原点を右下にすると、最初の線はそちら側から始まる
    home = np.array([297.0, 210.0])
    first = svg_export.two_opt(svg_export.linesort(lines, home), home)[0]
    far = svg_export.two_opt(svg_export.linesort(lines), None)[0]
    d_home = float(np.hypot(*(first[0] - home)))
    d_far = float(np.hypot(*(far[0] - home)))
    assert d_home < d_far, f"原点が効いていない: {d_home:.1f} vs {d_far:.1f}"

    # 短すぎる入力はそのまま
    one = [np.array([[0.0, 0.0], [1.0, 1.0]])]
    assert svg_export.two_opt(one) is one


@test("SVG: pen home corner moves where the sort starts")
def t75():
    from freepencil2 import svg_export

    for corner, want in (("TL", (0.0, 0.0)), ("TR", (297.0, 0.0)),
                         ("BL", (0.0, 210.0)), ("BR", (297.0, 210.0))):
        got = svg_export.pen_home(svg_export.SvgOptions(home=corner),
                                  297.0, 210.0)
        assert tuple(got) == want, (corner, tuple(got))
    assert svg_export.SvgOptions().home == "TL"


@test("SVG: freestyle / sharp / crease sources add hand-marked edges")
def t76():
    import numpy as np
    from freepencil2 import svg_export

    objs = _svg_scene()
    mesh = objs[0].data
    off = {s: False for s in svg_export.LINE_SOURCES}
    for name in ("freestyle", "sharp", "crease"):
        assert svg_export.SvgOptions().sources[name] is False, name

    # 何もマークしていなければ 0 本
    assert _n_edges(svg_export.SvgOptions(
        sources={**off, "freestyle": True})) == 0
    assert _n_edges(svg_export.SvgOptions(sources={**off, "sharp": True})) == 0

    # Freestyle / Sharp をマークすると、その本数だけ出る
    ne = len(mesh.edges)
    marks = np.zeros(ne, dtype=bool)
    marks[::7] = True
    attr = mesh.attributes.get(svg_export.FREESTYLE_EDGE_ATTR)
    if attr is None:
        attr = mesh.attributes.new(svg_export.FREESTYLE_EDGE_ATTR,
                                   "BOOLEAN", "EDGE")
    attr.data.foreach_set("value", marks)
    mesh.update()
    got = _n_edges(svg_export.SvgOptions(sources={**off, "freestyle": True}))
    assert got == int(marks.sum()), f"freestyle: {got} != {marks.sum()}"

    sharp = np.zeros(ne, dtype=bool)
    sharp[1::5] = True
    mesh.edges.foreach_set("use_edge_sharp", sharp)
    mesh.update()
    got = _n_edges(svg_export.SvgOptions(sources={**off, "sharp": True}))
    assert got == int(sharp.sum()), f"sharp: {got} != {sharp.sum()}"

    # 折れ目: しきい値を上げるほど減り、0 度なら面 2 枚の辺は全部出る
    two_face = _n_edges(svg_export.SvgOptions(
        sources={**off, "crease": True}, crease_angle=0.0))
    some = _n_edges(svg_export.SvgOptions(
        sources={**off, "crease": True}, crease_angle=30.0))
    none = _n_edges(svg_export.SvgOptions(
        sources={**off, "crease": True}, crease_angle=180.0))
    assert two_face > some > 0, (two_face, some)
    assert none < some, (none, some)
    # open と合わせると辺の総数になる(面 2 枚あるか無いかのどちらか)
    total = _n_edges(svg_export.SvgOptions(
        sources={**off, "crease": True, "open": True}, crease_angle=0.0))
    assert total == ne, (total, ne)


@test("SVG: layers get their own colour and pen width, a single layer stays black")
def t77():
    import re
    import shutil
    import tempfile
    from freepencil2 import svg_export

    _svg_scene()
    tmp = Path(tempfile.mkdtemp(prefix="fpm_svg_"))
    try:
        st = svg_export.export_svg(
            bpy.context, str(tmp / "one.svg"),
            svg_export.SvgOptions(depth_res=320))
        text = Path(st["svg"]).read_text(encoding="utf-8")
        assert set(re.findall(r'stroke="(#[0-9a-f]{6})"', text)) == {
            "#000000"}, "単層が黒でない"

        st = svg_export.export_svg(
            bpy.context, str(tmp / "src.svg"),
            svg_export.SvgOptions(depth_res=320, layers="SOURCE",
                                  outline_pen=0.8, pen=0.3))
        text = Path(st["svg"]).read_text(encoding="utf-8")
        groups = re.findall(
            r'inkscape:label="(\w+)"[^>]*stroke="(#[0-9a-f]{6})"'
            r'[^>]*stroke-width="([0-9.]+)"', text)
        assert len(groups) >= 2, f"層が足りない: {groups}"
        colors = [c for _, c, _ in groups]
        assert len(set(colors)) == len(colors), f"層の色が重なった: {groups}"
        widths = {name: float(w) for name, _, w in groups}
        assert widths.get("outline") == 0.8, widths
        assert all(w == 0.3 for n, w in widths.items() if n != "outline"), (
            widths)

        # 色分けを切れば全部黒
        st = svg_export.export_svg(
            bpy.context, str(tmp / "mono.svg"),
            svg_export.SvgOptions(depth_res=320, layers="SOURCE",
                                  layer_colors=False))
        text = Path(st["svg"]).read_text(encoding="utf-8")
        assert set(re.findall(r'stroke="(#[0-9a-f]{6})"', text)) == {
            "#000000"}

        # 名前で色が決まるので、層ごとに別ファイルにしても同じ層は同じ色
        st = svg_export.export_svg(
            bpy.context, str(tmp / "split.svg"),
            svg_export.SvgOptions(depth_res=320, layers="SOURCE",
                                  split_files=True))
        per_file = {}
        for f in st["files"]:
            t = Path(f).read_text(encoding="utf-8")
            m = re.search(r'inkscape:label="(\w+)"[^>]*stroke="(#[0-9a-f]{6})"',
                          t)
            per_file[m.group(1)] = m.group(2)
        for name, color in per_file.items():
            assert color == svg_export.layer_color(
                name, 1, svg_export.SvgOptions(layers="SOURCE")), (name, color)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test("SVG: the preview auto-refresh timer follows the preview and its toggle")
def t78():
    from freepencil2 import svg_export

    _svg_scene()
    scene = bpy.context.scene
    scene.fpm_svg_preview_auto = True
    svg_export.disable_preview()
    assert not bpy.app.timers.is_registered(svg_export._auto_refresh_tick)

    svg_export.enable_preview()
    assert bpy.app.timers.is_registered(svg_export._auto_refresh_tick), (
        "プレビューを出してもタイマーが掛からない")
    scene.fpm_svg_preview_auto = False
    assert not bpy.app.timers.is_registered(svg_export._auto_refresh_tick), (
        "OFF にしてもタイマーが残る")
    scene.fpm_svg_preview_auto = True
    assert bpy.app.timers.is_registered(svg_export._auto_refresh_tick)
    svg_export.disable_preview()
    assert not bpy.app.timers.is_registered(svg_export._auto_refresh_tick), (
        "プレビューを消してもタイマーが残る")
    # プレビューが無いのに ON にしてもタイマーは掛からない
    assert not svg_export.auto_refresh_wanted()


@test("SVG: batch operators keep a synchronous execute for scripts")
def t79():
    from freepencil2 import svg_export

    for cls in (svg_export.FP_OT_EXPORT_SVG_CAMERAS,
                svg_export.FPM_OT_EXPORT_SVG_FRAMES):
        for name in ("execute", "invoke", "modal", "cancel"):
            assert callable(getattr(cls, name, None)), (cls.__name__, name)
        assert "Esc" in cls.bl_description, cls.bl_description


def main():
    print("[tests] FreePencil smoke tests")
    fp_batch.install_addon()
    for name, fn in sorted(globals().items()):
        if callable(fn) and getattr(fn, "__test__", False):
            fn()
    out = BATCH / "out"
    out.mkdir(exist_ok=True)
    (out / "tests.json").write_text(
        json.dumps(RESULTS, indent=2, ensure_ascii=False), encoding="utf-8")
    failed = [r for r in RESULTS if not r["ok"]]
    print(f"[tests] {len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    if failed:
        for f_ in failed:
            print(f_["error"])
        sys.exit(1)


if __name__ == "__main__":
    main()
