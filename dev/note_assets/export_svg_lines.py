"""塗り分けの境界を直接ベクタとして取り出し、SVG にする試作。

レンダー画像をトレースするのではなく、線の定義そのもの
――「隣り合う面の mecha_color が違う辺」――から線を作る。コンポジタの
Sobel は幅を持つ帯を描くので、それを追跡するとペンプロッタでは輪郭を
二重になぞってしまう。辺そのものを出せば常に1本の中心線になる。

線になる辺は3種類。

  color  … 面2枚の mecha_color が違う（塗り分け法の線そのもの）
  open   … 面が2枚ない（開いた縁・非多様体）
  sil    … カメラから見て表裏が入れ替わる（外形線）

隠線処理は Z パスとの照合で行う。辺を等分した各点について、カメラからの
距離と深度バッファの値を比べ、手前にあるものだけ残す。

  blender -b --factory-startup --python export_svg_lines.py -- --demo
  blender -b --factory-startup --python export_svg_lines.py -- \
      --blend <asset.blend> --name mecha

出力は dev/note_assets/out/<name>_lines.svg。mm 単位・塗りなし・一定線幅。
プロッタに要る後処理(端点の結合・間引き・描画順)はこの中でやる。vpype の
linemerge/linesort に相当するが、vpype 本体は Shapely と scipy を要求する
ので入れていない(4.2〜5.2 を1パッケージで配る方針と両立しない)。
reloop や layout、HPGL 出力が要るときだけ外から vpype を通す:

  vpype read out/demo_lines.svg reloop linesort write plot.svg

試作なので以下は割り切っている。
- コンポジタツリーを作り直す（このスクリプトは使い捨てのシーンで動かす）
- モディファイア適用後のメッシュを見るので、サブディビジョンが入ると
  面色が補間されて境界がぼやける
- 既定の --bias/--neighbourhood は実機モデル(104万面のCAD)で透けが
  出ない値に寄せてある。緩めると中身が外板を透ける
- 5.x では未検証（手元に 4.3/4.5 しか無い）
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import bpy
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "batch"))
sys.path.insert(0, str(HERE))

import fp_batch          # noqa: E402

BACKGROUND_Z = 1e9       # Z パスの背景。EEVEE は 1e10 を書く
A4 = (210.0, 297.0)


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


# ---------------------------------------------------------------- シーン
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

    アセットに付いてくる地面は「平ら」かつ「モデル本体より大きい」。
    この2つを同時に満たす板だけを対象にする。実測(KD250)では
    1.41 x 1.41 x 0.00 の1面ポリゴンで、本体の 0.31 の4.5倍あった。
    黙って消すと線が減った理由が分からなくなるので、返り値で名前を出す。

    カメラのフィットは hide_render を見るので、単に隠すだけでは
    フレームが地面に引っ張られたままになる。使い捨てのシーンなので消す。
    """
    import re

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


# ---------------------------------------------------------------- 抽出
def face_color_keys(mesh):
    """面ごとの mecha_color を比較用の整数キーにする。

    色は面単位で決まり、utils.apply_face_colors が角へ展開している。
    なので面の先頭ループを1つ読めば足りる。
    """
    attr = mesh.color_attributes.get("mecha_color")
    if attr is None:
        return None
    n_loops = len(mesh.loops)
    if attr.domain != "CORNER" or n_loops == 0:
        return None

    buf = np.empty(n_loops * 4, dtype=np.float32)
    attr.data.foreach_get("color", buf)
    cols = buf.reshape(n_loops, 4)[:, :3]

    starts = np.empty(len(mesh.polygons), dtype=np.int32)
    mesh.polygons.foreach_get("loop_start", starts)
    face_cols = cols[starts.astype(np.intp)]

    # BYTE_COLOR なので実質8bitだが、丸め差で別色に見えないよう量子化する
    q = np.clip(np.rint(face_cols * 4095.0), 0, 4095).astype(np.int64)
    return (q[:, 0] << 24) | (q[:, 1] << 12) | q[:, 2]


def line_edges(obj, depsgraph, cam):
    """線になる辺を集めて、頂点のワールド座標と一緒に返す。"""
    from mathutils import Vector

    from freepencil2 import mesh_islands

    eval_obj = obj.evaluated_get(depsgraph)
    mesh = eval_obj.to_mesh()
    try:
        if len(mesh.polygons) == 0 or len(mesh.edges) == 0:
            return None

        keys = face_color_keys(mesh)
        if keys is None:
            return None

        # 辺 -> 面2枚の対応はアドオン本体と同じものを使う。ここで別実装を
        # 持つと、線の位置がレンダーとずれても気づけない
        topo = mesh_islands.MeshTopology(mesh)
        ne = topo.n_edges
        nf = topo.n_faces

        color = np.zeros(ne, dtype=bool)
        two = topo.two_face
        color[two] = keys[topo.face_a[two]] != keys[topo.face_b[two]]
        open_edge = ~two

        # 外形線: カメラから見た面の表裏が辺の左右で入れ替わる。
        # 法線をワールドへ移すより、カメラをローカルへ移すほうが安い
        normals = np.empty(nf * 3, dtype=np.float32)
        mesh.polygons.foreach_get("normal", normals)
        normals = normals.reshape(nf, 3).astype(np.float64)

        inv = eval_obj.matrix_world.inverted()
        if cam.data.type == "ORTHO":
            view = np.array(inv.to_3x3()
                            @ (cam.matrix_world.to_3x3()
                               @ Vector((0.0, 0.0, -1.0))))
            facing = normals @ view
        else:
            cam_local = np.array(inv @ cam.matrix_world.translation)
            facing = np.einsum("ij,ij->i", normals,
                               topo.center.astype(np.float64) - cam_local)
        front = facing < 0.0
        sil = np.zeros(ne, dtype=bool)
        sil[two] = front[topo.face_a[two]] != front[topo.face_b[two]]

        keep = color | open_edge | sil
        if not keep.any():
            return None

        ev = np.empty(ne * 2, dtype=np.int32)
        mesh.edges.foreach_get("vertices", ev)
        ev = ev.reshape(ne, 2)[keep]

        nv = len(mesh.vertices)
        co = np.empty(nv * 3, dtype=np.float32)
        mesh.vertices.foreach_get("co", co)
        co = co.reshape(nv, 3).astype(np.float64)

        m = np.array(eval_obj.matrix_world, dtype=np.float64)
        world = co @ m[:3, :3].T + m[:3, 3]
        centers = topo.center.astype(np.float64) @ m[:3, :3].T + m[:3, 3]

        kind = np.where(color[keep], 0, np.where(open_edge[keep], 1, 2))
        return {"verts": world, "edges": ev, "kind": kind, "centers": centers}
    finally:
        eval_obj.to_mesh_clear()


# ---------------------------------------------------------------- 投影
class Projection:
    """ワールド座標 -> ピクセル座標と、カメラからの距離。"""

    def __init__(self, scene, depsgraph, width: int, height: int):
        cam = scene.camera
        self.width = width
        self.height = height
        self.view = np.array(cam.matrix_world.inverted(), dtype=np.float64)
        self.proj = np.array(
            cam.calc_matrix_camera(depsgraph, x=width, y=height),
            dtype=np.float64)

    def __call__(self, pts: np.ndarray) -> tuple:
        """(x_px, y_px_上原点, 平面距離, 光線距離, 画面内か) を返す。"""
        n = len(pts)
        homo = np.empty((n, 4), dtype=np.float64)
        homo[:, :3] = pts
        homo[:, 3] = 1.0
        cam_space = homo @ self.view.T
        clip = cam_space @ self.proj.T

        w = clip[:, 3]
        ok = np.abs(w) > 1e-12
        w = np.where(ok, w, 1.0)
        x_px = (clip[:, 0] / w * 0.5 + 0.5) * self.width
        y_px = (0.5 - clip[:, 1] / w * 0.5) * self.height

        depth_plane = -cam_space[:, 2]
        depth_ray = np.linalg.norm(cam_space[:, :3], axis=1)

        inside = (ok & (x_px >= 0) & (x_px < self.width)
                  & (y_px >= 0) & (y_px < self.height) & (depth_plane > 0))
        return x_px, y_px, depth_plane, depth_ray, inside


# ---------------------------------------------------------------- 深度
def render_depth(scene, out_dir: Path, width: int, height: int) -> np.ndarray:
    """Z パスを EXR に書かせて読み戻す。返り値は下原点の (H, W)。"""
    from freepencil2 import compat

    scene.view_layers[0].use_pass_z = True

    scene.render.engine = fp_batch.eevee_engine()
    scene.render.resolution_x = width
    scene.render.resolution_y = height
    scene.render.resolution_percentage = 100
    scene.render.filter_size = 0.0      # 深度を隣の画素と混ぜない
    try:
        scene.eevee.taa_render_samples = 1
    except AttributeError:
        pass

    depth_dir = out_dir / "_depth"
    depth_dir.mkdir(parents=True, exist_ok=True)
    for old in depth_dir.glob("*.exr"):
        old.unlink()

    tree = compat.get_compositor_tree(scene, create=True)
    for node in list(tree.nodes):
        tree.nodes.remove(node)

    # パスを有効にしてからノードを作る(でないとソケットが生えていない)
    rl = compat.new_node(tree, "CompositorNodeRLayers")
    sock = compat.render_layer_socket(rl, ("Depth", "Z"))
    if sock is None:
        raise RuntimeError("Render Layers に Depth 出力が無い")

    fo = compat.new_node(tree, "CompositorNodeOutputFile")
    compat.file_output_set_dir(fo, str(depth_dir))
    compat.file_output_clear_slots(fo)
    compat.file_output_add_slot(fo, "depth", file_format="OPEN_EXR",
                                color_mode="BW")
    fo.format.color_depth = "32"
    tree.links.new(sock, fo.inputs[-1])

    bpy.ops.render.render(write_still=False)

    written = sorted(depth_dir.glob("*.exr"))
    if not written:
        raise RuntimeError(f"深度EXRが書かれていない: {depth_dir}")

    img = bpy.data.images.load(str(written[-1]))
    try:
        img.colorspace_settings.name = "Non-Color"
        w, h = img.size
        buf = np.empty(w * h * img.channels, dtype=np.float32)
        img.pixels.foreach_get(buf)
        return buf.reshape(h, w, img.channels)[:, :, 0].astype(np.float64)
    finally:
        bpy.data.images.remove(img)


def sample_depth(depth: np.ndarray, x_px, y_px, radius: int):
    """近傍の最大深度。背景(=巨大値)は混ぜず、全部背景なら NaN。"""
    h, w = depth.shape
    col = np.clip(x_px.astype(np.int64), 0, w - 1)
    row = np.clip((h - 1) - y_px.astype(np.int64), 0, h - 1)

    best = np.full(len(col), -np.inf)
    for dy in range(-radius, radius + 1):
        rr = np.clip(row + dy, 0, h - 1)
        for dx in range(-radius, radius + 1):
            cc = np.clip(col + dx, 0, w - 1)
            v = depth[rr, cc]
            np.maximum(best, np.where(v >= BACKGROUND_Z, -np.inf, v), out=best)
    return np.where(np.isneginf(best), np.nan, best)


def pick_depth_mode(depth, project, centers: np.ndarray, rng) -> tuple:
    """Z パスが「平面距離」か「光線距離」かを実測で決める。

    面の中心を投影して深度バッファと比べる。手前にある面なら値が一致する
    はずなので、一致した本数が多いほうが正しい解釈。推測で決めない。
    """
    if len(centers) > 20000:
        centers = centers[rng.choice(len(centers), 20000, replace=False)]
    x_px, y_px, plane, ray, inside = project(centers)
    buf = sample_depth(depth, x_px, y_px, 0)
    ok = inside & ~np.isnan(buf)
    if not ok.any():
        return "plane", {"plane": 0, "ray": 0}

    hits = {}
    for name, cand in (("plane", plane), ("ray", ray)):
        rel = np.abs(buf[ok] - cand[ok]) / np.maximum(cand[ok], 1e-9)
        hits[name] = int((rel < 0.005).sum())
    return ("ray" if hits["ray"] > hits["plane"] else "plane"), hits


# ---------------------------------------------------------------- 可視判定
def visible_spans(data, project, depth, args, mode: str):
    """辺を等分して可視判定し、(全可視の辺, 部分可視の線分) に分ける。"""
    verts, edges = data["verts"], data["edges"]
    m = len(edges)
    if m == 0:
        return np.zeros(0, dtype=bool), []
    if args.keep_hidden:
        return np.ones(m, dtype=bool), []

    v0 = verts[edges[:, 0]]
    v1 = verts[edges[:, 1]]
    s = max(1, args.samples)
    t = (np.arange(s, dtype=np.float64) + 0.5) / s
    pts = (v0[:, None, :] + (v1 - v0)[:, None, :] * t[None, :, None]
           ).reshape(m * s, 3)

    x_px, y_px, plane, ray, inside = project(pts)
    expected = ray if mode == "ray" else plane
    buf = sample_depth(depth, x_px, y_px, args.neighbourhood)

    # 線は面の上にあるので必ず自己遮蔽する。相対バイアスで逃がす
    vis = inside & (np.isnan(buf) | (expected <= buf * (1.0 + args.bias)))
    vis = vis.reshape(m, s)

    full = vis.all(axis=1)
    none = ~vis.any(axis=1)

    # 部分可視は外形線をまたぐ辺くらいなので、ここだけ Python で刻む
    pieces = []
    for i in np.flatnonzero(~full & ~none):
        row = vis[i]
        j = 0
        while j < s:
            if not row[j]:
                j += 1
                continue
            k = j
            while k + 1 < s and row[k + 1]:
                k += 1
            d = v1[i] - v0[i]
            pieces.append((v0[i] + d * (j / s), v0[i] + d * ((k + 1) / s)))
            j = k + 1
    return full, pieces


# ---------------------------------------------------------------- 連結
def chain_edges(edges: np.ndarray) -> list:
    """全可視の辺を頂点でつないで折れ線にする。分岐では切る。

    ここでは分岐で切るだけ。切れ端をつなぎ直すのは linemerge、描く順を
    決めるのは linesort が引き受ける。
    """
    adj = defaultdict(list)
    for ei, (a, b) in enumerate(edges):
        adj[int(a)].append((int(b), ei))
        adj[int(b)].append((int(a), ei))

    used = set()
    chains = []

    def walk(start: int) -> list:
        path = [start]
        cur = start
        while True:
            nxt = next(((o, ei) for o, ei in adj[cur] if ei not in used), None)
            if nxt is None:
                break
            other, ei = nxt
            used.add(ei)
            path.append(other)
            cur = other
            if cur == start or len(adj[cur]) != 2:
                break
        return path

    # 端点・分岐点から伸ばす。残ったぶんが閉じた輪
    for verts in ([v for v, links in adj.items() if len(links) != 2],
                  list(adj)):
        for v in verts:
            while any(ei not in used for _, ei in adj[v]):
                path = walk(v)
                if len(path) > 1:
                    chains.append(path)
    return chains


def rdp(pts: np.ndarray, eps: float) -> np.ndarray:
    """Douglas-Peucker。再帰だと深い折れ線でスタックが尽きるので反復で回す。"""
    n = len(pts)
    if n < 3 or eps <= 0.0:
        return pts
    keep = np.zeros(n, dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        seg = pts[j] - pts[i]
        length = float(np.hypot(seg[0], seg[1]))
        sub = pts[i + 1:j]
        if length < 1e-12:
            d = np.hypot(sub[:, 0] - pts[i, 0], sub[:, 1] - pts[i, 1])
        else:
            d = np.abs(seg[0] * (pts[i, 1] - sub[:, 1])
                       - (pts[i, 0] - sub[:, 0]) * seg[1]) / length
        k = int(np.argmax(d))
        if d[k] > eps:
            mid = i + 1 + k
            keep[mid] = True
            stack.append((i, mid))
            stack.append((mid, j))
    return pts[keep]


# ------------------------------------------------- プロッタ向けの後処理
# vpype の linemerge / linesort に相当する2つだけを自前で持つ。vpype 本体は
# Shapely と scipy(どちらもコンパイル済みホイール)を要求するので、4.2〜5.2 を
# 1パッケージで配る方針とは両立しない。ここで要るのは端点をつなぐ処理と
# 描画順の並べ替えだけで、どちらも numpy で足りる。
def _grid_key(pt, cell: float) -> tuple:
    return (int(np.floor(pt[0] / cell)), int(np.floor(pt[1] / cell)))


def linemerge(lines: list, tol: float) -> list:
    """端点が tol 以内で一致する折れ線をつなぐ。

    線は共有頂点から作っているので、隣り合う鎖の端点は本来ぴったり同じ値に
    なる。丸めのぶんだけ許容して拾えば、分岐で切った鎖が元どおり長くなる。
    """
    if tol <= 0.0 or len(lines) < 2:
        return lines

    cell = max(tol, 1e-9)
    grid = defaultdict(list)
    for i, ln in enumerate(lines):
        grid[_grid_key(ln[0], cell)].append((i, 0))
        grid[_grid_key(ln[-1], cell)].append((i, 1))

    used = np.zeros(len(lines), dtype=bool)
    tol2 = tol * tol

    def find(pt):
        kx, ky = _grid_key(pt, cell)
        best, best_d = None, tol2
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j, end in grid.get((kx + dx, ky + dy), ()):
                    if used[j]:
                        continue
                    q = lines[j][0] if end == 0 else lines[j][-1]
                    d = (q[0] - pt[0]) ** 2 + (q[1] - pt[1]) ** 2
                    if d <= best_d:
                        best, best_d = (j, end), d
        return best

    out = []
    for i in range(len(lines)):
        if used[i]:
            continue
        used[i] = True
        pts = lines[i]
        # 末端から伸ばし、反転してもう一度。2回で両側が伸びる
        for _ in range(2):
            parts = [pts]
            tail = pts[-1]
            while True:
                hit = find(tail)
                if hit is None:
                    break
                j, end = hit
                used[j] = True
                seg = lines[j] if end == 0 else lines[j][::-1]
                parts.append(seg[1:])
                tail = seg[-1]
            pts = np.concatenate(parts) if len(parts) > 1 else parts[0]
            pts = pts[::-1]
        out.append(pts)
    return out


def pen_up_travel(lines: list) -> float:
    """ペンを上げて移動する距離の合計(mm)。原点から描き始める前提。"""
    cur = np.zeros(2)
    total = 0.0
    for ln in lines:
        total += float(np.hypot(*(ln[0] - cur)))
        cur = ln[-1]
    return total


def linesort(lines: list) -> list:
    """次に描く線を貪欲に選び直す。線の向きは反転してよい。

    総当たりは 3万本で 1e9 回になるので、端点を一様グリッドに入れて
    近いセルから外へ広げる。最良値がリングの下限を下回った時点で打ち切る。
    """
    n = len(lines)
    if n < 2:
        return lines

    starts = np.array([ln[0] for ln in lines])
    ends = np.array([ln[-1] for ln in lines])
    span = np.vstack([starts, ends])
    extent = float((span.max(axis=0) - span.min(axis=0)).max())
    cell = max(extent / max(1, int(np.sqrt(n))), 1e-9)

    grid = defaultdict(list)
    for i in range(n):
        grid[_grid_key(starts[i], cell)].append((i, 0))
        grid[_grid_key(ends[i], cell)].append((i, 1))

    # 探索の打ち切りリングは「絵の広がり」ではなく「今のペン位置から
    # 一番遠い占有セルまで」で決める。ペンは紙の原点から出発するので、
    # 絵が紙の中央にあると前者では届かず、1本も見つからないまま終わる
    occupied = np.array(list(grid.keys()))
    k_lo, k_hi = occupied.min(axis=0), occupied.max(axis=0)

    used = np.zeros(n, dtype=bool)
    cur = np.zeros(2)
    out = []
    for _ in range(n):
        kx, ky = _grid_key(cur, cell)
        max_ring = int(max(abs(kx - k_lo[0]), abs(kx - k_hi[0]),
                           abs(ky - k_lo[1]), abs(ky - k_hi[1])))
        best, best_d = None, np.inf
        for r in range(max_ring + 1):
            if r == 0:
                cells = [(kx, ky)]
            else:
                cells = ([(kx + dx, ky - r) for dx in range(-r, r + 1)]
                         + [(kx + dx, ky + r) for dx in range(-r, r + 1)]
                         + [(kx - r, ky + dy) for dy in range(-r + 1, r)]
                         + [(kx + r, ky + dy) for dy in range(-r + 1, r)])
            for c in cells:
                for j, end in grid.get(c, ()):
                    if used[j]:
                        continue
                    q = starts[j] if end == 0 else ends[j]
                    d = (q[0] - cur[0]) ** 2 + (q[1] - cur[1]) ** 2
                    if d < best_d:
                        best, best_d = (j, end), d
            # リング r の外側はすべて r*cell 以上離れている
            if best is not None and (r * cell) ** 2 >= best_d:
                break
        if best is None:
            break
        j, end = best
        used[j] = True
        ln = lines[j] if end == 0 else lines[j][::-1]
        out.append(ln)
        cur = ln[-1]

    out.extend(lines[j] for j in np.flatnonzero(~used))
    return out


# ---------------------------------------------------------------- SVG
def write_svg(path: Path, polylines: list, width: int, height: int,
              args) -> dict:
    """ピクセル座標の折れ線を mm に移して書く。塗りなし・一定線幅。"""
    page_w, page_h = (A4[1], A4[0]) if width >= height else A4
    scale = min((page_w - 2 * args.margin) / width,
                (page_h - 2 * args.margin) / height)
    off_x = (page_w - width * scale) * 0.5
    off_y = (page_h - height * scale) * 0.5

    mm_lines = []
    for pl in polylines:
        mm = np.empty_like(pl)
        mm[:, 0] = off_x + pl[:, 0] * scale
        mm[:, 1] = off_y + pl[:, 1] * scale
        mm_lines.append(mm)

    stats = {"paths_raw": len(mm_lines),
             "pen_up_mm_raw": round(pen_up_travel(mm_lines), 1)}

    # つないでから間引く。逆にすると継ぎ目で折れが残る
    merged = linemerge(mm_lines, args.merge_tolerance)
    stats["paths_merged"] = len(merged)

    simplified = [s for s in (rdp(ln, args.simplify) for ln in merged)
                  if len(s) >= 2]
    stats["pen_up_mm_merged"] = round(pen_up_travel(simplified), 1)

    if not args.no_sort:
        simplified = linesort(simplified)
    stats["pen_up_mm"] = round(pen_up_travel(simplified), 1)

    body = []
    points_out = 0
    for mm in simplified:
        points_out += len(mm)
        coords = " ".join(f"{x:.3f},{y:.3f}" for x, y in mm)
        body.append(f'<polyline points="{coords}"/>')

    svg = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" version="1.1"\n'
        f'     width="{page_w}mm" height="{page_h}mm"\n'
        f'     viewBox="0 0 {page_w} {page_h}">\n'
        f'<g fill="none" stroke="#000000" stroke-width="{args.pen}"\n'
        '   stroke-linecap="round" stroke-linejoin="round">\n'
        + "\n".join(body)
        + "\n</g>\n</svg>\n"
    )
    path.write_text(svg, encoding="utf-8")
    stats.update({"page_mm": [page_w, page_h], "paths": len(body),
                  "points": points_out})
    return stats


# ---------------------------------------------------------------- main
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

    # 地面板は STEP1 の前に落とす。残すとカメラのフィットが地面に
    # 引っ張られ、本体が紙の中央で豆粒になる
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

    width = args.res
    height = int(round(args.res * 3 / 4))
    rec["resolution"] = [width, height]

    t2 = time.time()
    depth = render_depth(scene, out_dir, width, height)
    rec["depth_seconds"] = round(time.time() - t2, 2)

    depsgraph = bpy.context.evaluated_depsgraph_get()
    project = Projection(scene, depsgraph, width, height)
    rng = np.random.default_rng(args.seed)

    t3 = time.time()
    collected = []
    for obj in objs:
        if obj.type != "MESH" or obj.hide_render:
            continue
        data = line_edges(obj, depsgraph, scene.camera)
        if data is not None:
            collected.append(data)
    if not collected:
        raise SystemExit("線になる辺が1本も無い。STEP1 が走っていない可能性")

    mode, hits = pick_depth_mode(
        depth, project, np.concatenate([d["centers"] for d in collected]), rng)
    rec["depth_mode"] = mode
    rec["depth_mode_hits"] = hits

    polylines = []
    kinds = np.zeros(3, dtype=np.int64)
    n_edges = 0
    for data in collected:
        kinds += np.bincount(data["kind"], minlength=3)
        n_edges += len(data["edges"])
        verts = data["verts"]

        full, pieces = visible_spans(data, project, depth, args, mode)
        for path in chain_edges(data["edges"][full]):
            x_px, y_px, _, _, _ = project(verts[np.asarray(path)])
            polylines.append(np.stack([x_px, y_px], axis=1))

        if pieces:
            x_px, y_px, _, _, _ = project(np.asarray(pieces).reshape(-1, 3))
            polylines.extend(
                list(np.stack([x_px, y_px], axis=1).reshape(-1, 2, 2)))

    rec["edges_line"] = int(n_edges)
    rec["edges_by_kind"] = {"color": int(kinds[0]), "open": int(kinds[1]),
                            "silhouette": int(kinds[2])}
    rec["chains"] = len(polylines)
    rec["extract_seconds"] = round(time.time() - t3, 2)

    svg_path = out_dir / f"{args.name}_lines.svg"
    rec.update(write_svg(svg_path, polylines, width, height, args))
    rec["svg"] = str(svg_path)
    rec["total_seconds"] = round(time.time() - t0, 2)
    print("[svg] " + json.dumps(rec, ensure_ascii=False))


if __name__ == "__main__":
    main()
