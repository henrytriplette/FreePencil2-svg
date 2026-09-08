"""SVG 書き出しをヘッドレスで回す。アセットの下ごしらえ担当。

線を作るコアは本体の `freepencil2/svg_export.py` にあり、ここはそれを
呼ぶだけ。バッチとUIが同一コードパスなので、片方だけ直して線がずれる
ということが起きない(fp_core.py と同じ方針)。

このスクリプトが持っているのは、評価用アセットを扱うための部分だけ。

- アセットの読み込みと正規化(render_samples.stage_model)
- 地面板の除去。アセットに付いてくる地面は「平ら」かつ「本体より
  大きい」。残すとカメラのフィットが引っ張られ、本体が紙の中央で
  豆粒になる。アドオン側は実シーンを勝手にいじらないので、ここでやる
- 猿＋手前の箱のデモシーン(隠線処理の確認用)

  blender -b --factory-startup --python export_svg_lines.py -- --demo
  blender -b --factory-startup --python export_svg_lines.py -- \
      --blend <asset.blend> --name mecha

出力は dev/note_assets/out/<name>_lines.svg。mm 単位・塗りなし・一定線幅。
プロッタに要る後処理(端点の結合・間引き・描画順)はコア側で完結する。
reloop や layout、HPGL 出力が要るときだけ外から vpype を通す:

  vpype read out/demo_lines.svg reloop linesort write plot.svg
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import bpy
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "batch"))
sys.path.insert(0, str(HERE))

import fp_batch          # noqa: E402


def parse_args() -> argparse.Namespace:
    argv = sys.argv
    argv = argv[argv.index("--") + 1:] if "--" in argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--blend", help="アセット .blend。省略時は --demo が要る")
    p.add_argument("--demo", action="store_true",
                   help="猿＋手前の箱で隠線を試すシーンを組む")
    p.add_argument("--name", default="demo")
    p.add_argument("--out", default=str(HERE / "out"))
    p.add_argument("--res", type=int, default=1600,
                   help="深度バッファの長辺。線の位置精度はこれで決まる")
    p.add_argument("--samples", type=int, default=8,
                   help="1辺あたりの可視判定サンプル数")
    p.add_argument("--bias", type=float, default=0.001,
                   help="深度比較の相対バイアス。線は面上にあるので要る")
    p.add_argument("--neighbourhood", type=int, default=0,
                   help="深度参照の近傍半径(px)。0 で最近傍のみ。"
                        "1以上にすると近傍の最大値を採るので、細かい形状の"
                        "多いモデルでは中身が外板を透ける")
    p.add_argument("--page", default="A4", choices=["A5", "A4", "A3", "LETTER"])
    p.add_argument("--pen", type=float, default=0.3, help="線幅(mm)")
    p.add_argument("--margin", type=float, default=10.0, help="余白(mm)")
    p.add_argument("--simplify", type=float, default=0.05,
                   help="折れ線の間引き許容量(mm)。0 で無効")
    p.add_argument("--merge-tolerance", type=float, default=0.1,
                   help="端点がこの距離(mm)以内なら1本に繋ぐ。0 で無効")
    p.add_argument("--no-sort", action="store_true",
                   help="描画順の並べ替え(ペン移動の短縮)をしない")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--keep-hidden", action="store_true",
                   help="隠線処理を飛ばす(切り分け用)")
    p.add_argument("--no-occluder", action="store_true",
                   help="--demo の手前の箱を置かない(自己遮蔽の確認用)")
    p.add_argument("--keep-ground", action="store_true",
                   help="地面板の自動除去をしない")
    p.add_argument("--exclude", default="",
                   help="この正規表現に名前が一致するメッシュを除く")
    return p.parse_args(argv)


def demo_scene(occluder: bool = True) -> None:
    """猿の手前に箱を置く。隠線が効いているか一目で分かる配置にする。

    箱を外すと遮蔽物が無くなるので、消えた線はすべて自己遮蔽が原因になる。
    バイアスの詰めはこの状態で見る。
    """
    from mathutils import Vector

    bpy.ops.mesh.primitive_monkey_add(size=2.0, location=(0, 0, 0))
    if not occluder:
        return

    # カメラは fp_batch と同じ向きから寄るので、その線上に箱を置けば隠れる
    direction = Vector((1.0, -1.0, 0.65)).normalized()
    bpy.ops.mesh.primitive_cube_add(size=1.1, location=direction * 1.35)
    bpy.context.active_object.rotation_euler = (0.3, 0.5, 0.2)


def world_bbox_dims(obj) -> np.ndarray:
    """ワールド空間でのバウンディングボックスの各辺の長さ。"""
    from mathutils import Vector

    pts = np.array([list(obj.matrix_world @ Vector(c)) for c in obj.bound_box])
    return pts.max(axis=0) - pts.min(axis=0)


def drop_scenery(objs: list, exclude: str, keep_ground: bool) -> tuple:
    """地面板を落とす。落としたものは必ず名前で報告する。

    「平ら」かつ「モデル本体より大きい」板だけを対象にする。実測(KD250)では
    1.41 x 1.41 x 0.00 の1面ポリゴンで、本体の 0.31 の4.5倍あった。
    黙って消すと線が減った理由が分からなくなるので、返り値で名前を出す。
    """
    meshes = [o for o in objs if o.type == "MESH"]
    dropped = []

    if exclude:
        pat = re.compile(exclude)
        for o in list(meshes):
            if pat.search(o.name):
                meshes.remove(o)
                dropped.append(o.name)

    if not keep_ground and len(meshes) > 1:
        dims = {o: world_bbox_dims(o) for o in meshes}
        for o in list(meshes):
            d = dims[o]
            longest = float(d.max())
            if longest <= 0.0 or float(d.min()) > 0.01 * longest:
                continue          # 平らでない
            others = [dims[x].max() for x in meshes if x is not o]
            if others and longest >= 1.5 * float(max(others)):
                meshes.remove(o)
                dropped.append(o.name)

    if dropped:
        names = set(dropped)
        for o in [x for x in objs if x.name in names]:
            objs.remove(o)
            bpy.data.objects.remove(o, do_unlink=True)
    return objs, dropped


def main() -> None:
    args = parse_args()
    if not args.blend and not args.demo:
        raise SystemExit("--blend か --demo のどちらかが要る")

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    rec: dict = {"name": args.name, "blender": bpy.app.version_string}
    t0 = time.time()

    bpy.ops.wm.read_homefile(use_empty=True)
    fp_batch.install_addon()
    scene = bpy.context.scene

    if args.demo:
        demo_scene(not args.no_occluder)
        objs = [o for o in scene.objects if o.type == "MESH"]
    else:
        import render_samples
        objs, _others = render_samples.stage_model(Path(args.blend))

    # 地面板は STEP1 の前に落とす。残すとカメラのフィットが引っ張られる
    objs, dropped = drop_scenery(objs, args.exclude, args.keep_ground)
    rec["dropped"] = dropped
    rec["mesh_objects"] = len(objs)
    rec["faces_total"] = int(sum(len(o.data.polygons) for o in objs))

    fp_batch.apply_white_material(objs)
    scene.fp_use_random_seed = False
    scene.fp_color_seed = args.seed

    fp_batch.select_meshes()
    t1 = time.time()
    bpy.ops.freepencil.auto_vertex_color()
    rec["step1_seconds"] = round(time.time() - t1, 2)

    fp_batch.setup_camera_and_light()
    # 深度の縦横比はシーンのレンダー設定から取られる
    scene.render.resolution_x = args.res
    scene.render.resolution_y = int(round(args.res * 3 / 4))

    from freepencil2 import svg_export

    opts = svg_export.SvgOptions(
        page=args.page, margin=args.margin, pen=args.pen,
        merge_tolerance=args.merge_tolerance, simplify=args.simplify,
        samples=args.samples, bias=args.bias,
        neighbourhood=args.neighbourhood, depth_res=args.res,
        sort=not args.no_sort, keep_hidden=args.keep_hidden, seed=args.seed)

    t2 = time.time()
    stats = svg_export.export_svg(
        bpy.context, str(out_dir / f"{args.name}_lines.svg"), opts, objs)
    rec["export_seconds"] = round(time.time() - t2, 2)

    rec.update(stats)
    rec["total_seconds"] = round(time.time() - t0, 2)
    print("[svg] " + json.dumps(rec, ensure_ascii=False))


if __name__ == "__main__":
    main()
