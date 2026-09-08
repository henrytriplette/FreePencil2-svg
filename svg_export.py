"""SVG 書き出し(ペンプロッタ用)のコアと、それを呼ぶオペレータ。

レンダー画像をトレースせず、線の定義そのもの――隣り合う面の mecha_color が
違う辺――からベクタを作る。コンポジタの Sobel は幅を持つ帯を描くので、
それを追跡するとプロッタが輪郭を二重になぞってしまう。辺そのものを出せば
常に1本の中心線になる。

線になる辺は3種類。

  color  … 面2枚の mecha_color が違う(塗り分け法の線そのもの)
  open   … 面が2枚ない(開いた縁・非多様体)
  sil    … カメラから見て表裏が入れ替わる(外形線)

隠線処理は Z パスとの照合。辺を等分した各点について、カメラからの距離と
深度バッファを比べ、手前にあるものだけ残す。

fp_core.py と同じ方針で、bpy.ops を使わないコア関数とオペレータを分けて
いる。dev/note_assets/export_svg_lines.py は同じコアを import するので、
バッチとUIで線がずれることがない。
"""

import logging
from collections import defaultdict
from pathlib import Path

import bpy
import numpy as np

from . import compat
from . import mesh_islands

logger = logging.getLogger(__name__)

VCOL_LAYER_MECHA = "mecha_color"
VCOL_LAYER_MASK = "mask_color"
VCOL_LAYER_LINE = "line_color"
VCOL_LAYER_BONE = "bone_color"

BACKGROUND_Z = 1e9          # Z パスの背景。EEVEE は 1e10 を書く

# STEP4 の塗りをどこで「塗った」と見なすか。mask_color は「明るさは問わない」
# 仕様なので最大チャンネル、line_color は「白で見えなくなる」ので最小
# チャンネルを見る。中間はプロッタでは表現できないので 0.5 で二値化する
PAINT_THRESHOLD = 0.5

# 線の出どころ。ラスタ経路の fp_ch_* に対応する
LINE_SOURCES = ("mecha", "material", "bone", "open", "silhouette")

# 用紙(mm)。長辺・短辺の順で持ち、向きは絵の縦横比から決める
PAGE_SIZES = {
    "A5": (148.0, 210.0),
    "A4": (210.0, 297.0),
    "A3": (297.0, 420.0),
    "LETTER": (215.9, 279.4),
}


class SvgOptions:
    """書き出しの設定。シーンプロパティからも直接値からも作れる。"""

    __slots__ = ("page", "margin", "pen", "merge_tolerance", "simplify",
                 "samples", "bias", "neighbourhood", "depth_res",
                 "sort", "keep_hidden", "seed", "sources", "respect_paint")

    def __init__(self, page="A4", margin=10.0, pen=0.3, merge_tolerance=0.1,
                 simplify=0.05, samples=8, bias=0.001, neighbourhood=0,
                 depth_res=1600, sort=True, keep_hidden=False, seed=42,
                 sources=None, respect_paint=True):
        # bone だけ既定で切ってある。ラスタ経路の fp_ch_bone は 1.0 だが、
        # ボーン境界はプロッタでは線が増えすぎるので出どころとしては任意
        self.sources = dict(mecha=True, material=True, bone=False,
                            open=True, silhouette=True)
        if sources:
            self.sources.update(sources)
        self.respect_paint = respect_paint
        self.page = page
        self.margin = margin
        self.pen = pen
        self.merge_tolerance = merge_tolerance
        self.simplify = simplify
        self.samples = samples
        self.bias = bias
        self.neighbourhood = neighbourhood
        self.depth_res = depth_res
        self.sort = sort
        self.keep_hidden = keep_hidden
        self.seed = seed

    @classmethod
    def from_scene(cls, scene):
        g = getattr
        return cls(
            page=g(scene, "fp_svg_page", "A4"),
            margin=g(scene, "fp_svg_margin", 10.0),
            pen=g(scene, "fp_svg_pen", 0.3),
            merge_tolerance=g(scene, "fp_svg_merge_tolerance", 0.1),
            simplify=g(scene, "fp_svg_simplify", 0.05),
            samples=g(scene, "fp_svg_samples", 8),
            bias=g(scene, "fp_svg_bias", 0.001),
            neighbourhood=g(scene, "fp_svg_neighbourhood", 0),
            depth_res=g(scene, "fp_svg_depth_res", 1600),
            sort=g(scene, "fp_svg_sort", True),
            keep_hidden=g(scene, "fp_svg_keep_hidden", False),
            seed=g(scene, "fp_color_seed", 42),
            sources={s: bool(g(scene, f"fp_svg_src_{s}", s != "bone"))
                     for s in LINE_SOURCES},
            respect_paint=g(scene, "fp_svg_respect_paint", True),
        )


def eevee_engine() -> str:
    """EEVEE のエンジン識別子。4.x は BLENDER_EEVEE_NEXT、5.x で戻った。"""
    ids = bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items.keys()
    for cand in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
        if cand in ids:
            return cand
    return "BLENDER_EEVEE"


def target_objects(context, selected_only: bool = False) -> list:
    """書き出す対象のメッシュ。レンダーで出ないものは最初から外す。"""
    src = (context.selected_objects if selected_only
           else context.view_layer.objects)
    return [o for o in src if o.type == "MESH" and not o.hide_render]


# ---------------------------------------------------------------- 抽出
def _corner_rgb(mesh, name):
    """角ごとの RGB。無ければ None。"""
    attr = mesh.color_attributes.get(name)
    n_loops = len(mesh.loops)
    if attr is None or attr.domain != "CORNER" or n_loops == 0:
        return None
    buf = np.empty(n_loops * 4, dtype=np.float32)
    attr.data.foreach_get("color", buf)
    return buf.reshape(n_loops, 4)[:, :3]


def face_color_keys(mesh, name=VCOL_LAYER_MECHA):
    """面ごとの色を比較用の整数キーにする。

    mecha_color / bone_color は面単位で決まり、utils.apply_face_colors が
    角へ展開している。なので面の先頭ループを1つ読めば足りる。
    """
    cols = _corner_rgb(mesh, name)
    if cols is None:
        return None

    starts = np.empty(len(mesh.polygons), dtype=np.int32)
    mesh.polygons.foreach_get("loop_start", starts)
    face_cols = cols[starts.astype(np.intp)]

    # BYTE_COLOR なので実質8bitだが、丸め差で別色に見えないよう量子化する
    q = np.clip(np.rint(face_cols * 4095.0), 0, 4095).astype(np.int64)
    return (q[:, 0] << 24) | (q[:, 1] << 12) | q[:, 2]


def vertex_paint_level(mesh, name, reduce: str):
    """STEP4 の塗りを頂点ごとの値にならす。

    mask_color と line_color はユーザーがブラシで塗るので、面の中で値が
    一様とは限らない。面の先頭ループだけ見る mecha_color とは扱いが違う。
    角の値を頂点ごとに平均して、辺の判定はその両端で行う。
    """
    rgb = _corner_rgb(mesh, name)
    if rgb is None:
        return None
    val = rgb.max(axis=1) if reduce == "max" else rgb.min(axis=1)

    n_loops = len(mesh.loops)
    loop_vert = np.empty(n_loops, dtype=np.int32)
    mesh.loops.foreach_get("vertex_index", loop_vert)

    nv = len(mesh.vertices)
    acc = np.zeros(nv, dtype=np.float64)
    cnt = np.zeros(nv, dtype=np.int64)
    np.add.at(acc, loop_vert, val)
    np.add.at(cnt, loop_vert, 1)
    return acc / np.maximum(cnt, 1)


def _attr_boundary(keys, topo, ne: int) -> np.ndarray:
    """面ごとの属性が辺の左右で違うか。"""
    out = np.zeros(ne, dtype=bool)
    if keys is None:
        return out
    two = topo.two_face
    out[two] = keys[topo.face_a[two]] != keys[topo.face_b[two]]
    return out


def _silhouette(mesh, topo, eval_obj, cam, ne: int) -> np.ndarray:
    """カメラから見た面の表裏が辺の左右で入れ替わるか(外形線)。

    法線をワールドへ移すより、カメラをローカルへ移すほうが安い。
    """
    from mathutils import Vector

    nf = topo.n_faces
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
    out = np.zeros(ne, dtype=bool)
    two = topo.two_face
    out[two] = front[topo.face_a[two]] != front[topo.face_b[two]]
    return out


def _paint_removed(mesh, ev: np.ndarray) -> np.ndarray:
    """STEP4 の塗りで消される辺。

    mask_color は「塗ったところの線が消える(明るさは問わない)」、
    line_color は「白いほど薄く、白で見えなくなる」。プロッタは濃淡を
    出せないので、どちらも辺の両端の平均で二値化する。ここを見ないと
    ビューポートで消したはずの線が SVG に残る。
    """
    removed = np.zeros(len(ev), dtype=bool)
    for name, reduce in ((VCOL_LAYER_MASK, "max"), (VCOL_LAYER_LINE, "min")):
        level = vertex_paint_level(mesh, name, reduce)
        if level is None:
            continue
        edge_level = 0.5 * (level[ev[:, 0]] + level[ev[:, 1]])
        removed |= edge_level > PAINT_THRESHOLD
    return removed


def line_edges(obj, depsgraph, cam, opts: "SvgOptions" = None):
    """線になる辺を集めて、頂点のワールド座標と一緒に返す。"""
    if opts is None:
        opts = SvgOptions()

    eval_obj = obj.evaluated_get(depsgraph)
    mesh = eval_obj.to_mesh()
    try:
        if len(mesh.polygons) == 0 or len(mesh.edges) == 0:
            return None

        # 辺 -> 面2枚の対応はアドオン本体と同じものを使う。ここで別実装を
        # 持つと、線の位置がレンダーとずれても気づけない
        topo = mesh_islands.MeshTopology(mesh)
        ne = topo.n_edges

        ev = np.empty(ne * 2, dtype=np.int32)
        mesh.edges.foreach_get("vertices", ev)
        ev = ev.reshape(ne, 2)

        # 出どころごとに求めて足し合わせる。ラスタ経路が複数チャンネルの
        # エッジを重ねるのと同じ考え方
        src = opts.sources
        parts = {}
        if src.get("mecha", True):
            parts["mecha"] = _attr_boundary(
                face_color_keys(mesh, VCOL_LAYER_MECHA), topo, ne)
        if src.get("material", True):
            parts["material"] = _attr_boundary(topo.material, topo, ne)
        if src.get("bone", False):
            parts["bone"] = _attr_boundary(
                face_color_keys(mesh, VCOL_LAYER_BONE), topo, ne)
        if src.get("open", True):
            parts["open"] = ~topo.two_face
        if src.get("silhouette", True):
            parts["silhouette"] = _silhouette(mesh, topo, eval_obj, cam, ne)

        if not parts:
            return None
        keep = np.zeros(ne, dtype=bool)
        for m in parts.values():
            keep |= m

        if opts.respect_paint:
            keep &= ~_paint_removed(mesh, ev)

        if not keep.any():
            return None

        # 出どころ別の本数。重なりがあるので合計は総数より多くなる
        counts = {k: int((m & keep).sum()) for k, m in parts.items()}

        nv = len(mesh.vertices)
        co = np.empty(nv * 3, dtype=np.float32)
        mesh.vertices.foreach_get("co", co)
        co = co.reshape(nv, 3).astype(np.float64)

        m4 = np.array(eval_obj.matrix_world, dtype=np.float64)
        world = co @ m4[:3, :3].T + m4[:3, 3]
        centers = topo.center.astype(np.float64) @ m4[:3, :3].T + m4[:3, 3]

        return {"verts": world, "edges": ev[keep], "centers": centers,
                "counts": counts}
    finally:
        eval_obj.to_mesh_clear()


def extract_lines(objs, depsgraph, cam, opts: "SvgOptions" = None) -> list:
    """対象メッシュぶんの line_edges をまとめる。"""
    out = []
    for obj in objs:
        data = line_edges(obj, depsgraph, cam, opts)
        if data is not None:
            out.append(data)
    return out


# ---------------------------------------------------------------- 投影
class Projection:
    """ワールド座標 -> ピクセル座標と、カメラからの距離。"""

    def __init__(self, cam, depsgraph, width: int, height: int):
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
def render_depth_pass(scene, cam, width: int, height: int) -> np.ndarray:
    """Z パスを EXR に書かせて読み戻す。返り値は下原点の (H, W)。

    ユーザーのコンポジタを壊さないよう、使い捨ての一時シーンを作って
    そこでレンダーする。オブジェクトは複製せずコレクションを参照する
    だけなので、巨大シーンでもメモリは増えない。
    """
    tmp = bpy.data.scenes.new("FP_SVG_Depth")
    depth_dir = Path(bpy.app.tempdir) / "fp_svg_depth"
    try:
        for child in scene.collection.children:
            tmp.collection.children.link(child)
        for ob in scene.collection.objects:
            tmp.collection.objects.link(ob)
        tmp.camera = cam
        tmp.frame_set(scene.frame_current)

        tmp.render.engine = eevee_engine()
        tmp.render.resolution_x = width
        tmp.render.resolution_y = height
        tmp.render.resolution_percentage = 100
        tmp.render.filter_size = 0.0        # 深度を隣の画素と混ぜない
        try:
            tmp.eevee.taa_render_samples = 1
        except AttributeError:
            pass
        tmp.view_layers[0].use_pass_z = True

        depth_dir.mkdir(parents=True, exist_ok=True)
        for old in depth_dir.glob("*.exr"):
            try:
                old.unlink()
            except OSError:
                pass

        # パスを有効にしてからノードを作る(でないとソケットが生えていない)
        tree = compat.get_compositor_tree(tmp, create=True)
        for node in list(tree.nodes):
            tree.nodes.remove(node)

        rl = compat.new_node(tree, "CompositorNodeRLayers")
        rl.scene = tmp
        sock = compat.render_layer_socket(rl, ("Depth", "Z"))
        if sock is None:
            raise RuntimeError("Render Layers has no Depth output")

        fo = compat.new_node(tree, "CompositorNodeOutputFile")
        compat.file_output_set_dir(fo, str(depth_dir))
        compat.file_output_clear_slots(fo)
        compat.file_output_add_slot(fo, "depth", file_format="OPEN_EXR",
                                    color_mode="BW")
        fo.format.color_depth = "32"
        tree.links.new(sock, fo.inputs[-1])

        bpy.ops.render.render(scene=tmp.name, write_still=False)

        written = sorted(depth_dir.glob("*.exr"))
        if not written:
            raise RuntimeError(f"no depth EXR written to {depth_dir}")

        img = bpy.data.images.load(str(written[-1]))
        try:
            img.colorspace_settings.name = "Non-Color"
            w, h = img.size
            buf = np.empty(w * h * img.channels, dtype=np.float32)
            img.pixels.foreach_get(buf)
            return buf.reshape(h, w, img.channels)[:, :, 0].astype(np.float64)
        finally:
            bpy.data.images.remove(img)
    finally:
        bpy.data.scenes.remove(tmp)


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


def choose_depth_mode(depth, project, centers: np.ndarray, seed: int) -> tuple:
    """Z パスが「平面距離」か「光線距離」かを実測で決める。

    面の中心を投影して深度バッファと比べる。手前にある面なら値が一致する
    はずなので、一致した本数が多いほうが正しい解釈。推測で決めない。
    """
    rng = np.random.default_rng(seed)
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


def visible_spans(data, project, depth, opts: SvgOptions, mode: str):
    """辺を等分して可視判定し、(全可視の辺, 部分可視の線分) に分ける。"""
    verts, edges = data["verts"], data["edges"]
    m = len(edges)
    if m == 0:
        return np.zeros(0, dtype=bool), []
    if opts.keep_hidden:
        return np.ones(m, dtype=bool), []

    v0 = verts[edges[:, 0]]
    v1 = verts[edges[:, 1]]
    s = max(1, opts.samples)
    t = (np.arange(s, dtype=np.float64) + 0.5) / s
    pts = (v0[:, None, :] + (v1 - v0)[:, None, :] * t[None, :, None]
           ).reshape(m * s, 3)

    x_px, y_px, plane, ray, inside = project(pts)
    expected = ray if mode == "ray" else plane
    buf = sample_depth(depth, x_px, y_px, opts.neighbourhood)

    # 線は面の上にあるので必ず自己遮蔽する。相対バイアスで逃がす
    vis = inside & (np.isnan(buf) | (expected <= buf * (1.0 + opts.bias)))
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
    """辺を頂点でつないで折れ線にする。分岐では切る。

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


def visible_polylines(collected: list, project, depth, opts: SvgOptions,
                      mode: str) -> tuple:
    """見えている線をピクセル座標の折れ線にする。統計も返す。"""
    polylines = []
    counts = defaultdict(int)
    n_edges = 0
    for data in collected:
        for k, v in data["counts"].items():
            counts[k] += v
        n_edges += len(data["edges"])
        verts = data["verts"]

        full, pieces = visible_spans(data, project, depth, opts, mode)
        for path in chain_edges(data["edges"][full]):
            x_px, y_px, _, _, _ = project(verts[np.asarray(path)])
            polylines.append(np.stack([x_px, y_px], axis=1))

        if pieces:
            x_px, y_px, _, _, _ = project(np.asarray(pieces).reshape(-1, 3))
            polylines.extend(
                list(np.stack([x_px, y_px], axis=1).reshape(-1, 2, 2)))

    # 重なりがあるので、出どころ別の合計は edges_line より多くなる
    stats = {"edges_line": int(n_edges), "edges_by_source": dict(counts)}
    return polylines, stats


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

    tol は絵の大きさではなくペン幅で決めること。紙に対して絵が小さいと、
    無関係な端点まで許容内に入って本数だけが減る。
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


# ---------------------------------------------------------------- SVG
def page_mm(page: str, width: int, height: int) -> tuple:
    """用紙の (幅, 高さ)。絵が横長なら横置きにする。"""
    short, long_ = PAGE_SIZES.get(page, PAGE_SIZES["A4"])
    return (long_, short) if width >= height else (short, long_)


def build_svg(polylines_px: list, width: int, height: int,
              opts: SvgOptions) -> tuple:
    """ピクセル座標の折れ線を mm に移し、SVG 文字列と統計を返す。"""
    page_w, page_h = page_mm(opts.page, width, height)
    scale = min((page_w - 2 * opts.margin) / width,
                (page_h - 2 * opts.margin) / height)
    off_x = (page_w - width * scale) * 0.5
    off_y = (page_h - height * scale) * 0.5

    mm_lines = []
    for pl in polylines_px:
        mm = np.empty_like(pl)
        mm[:, 0] = off_x + pl[:, 0] * scale
        mm[:, 1] = off_y + pl[:, 1] * scale
        mm_lines.append(mm)

    stats = {"paths_raw": len(mm_lines),
             "pen_up_mm_raw": round(pen_up_travel(mm_lines), 1)}

    # つないでから間引く。逆にすると継ぎ目で折れが残る
    merged = linemerge(mm_lines, opts.merge_tolerance)
    stats["paths_merged"] = len(merged)

    lines = [s for s in (rdp(ln, opts.simplify) for ln in merged)
             if len(s) >= 2]
    if opts.sort:
        lines = linesort(lines)
    stats["pen_up_mm"] = round(pen_up_travel(lines), 1)

    body = []
    points = 0
    for mm in lines:
        points += len(mm)
        coords = " ".join(f"{x:.3f},{y:.3f}" for x, y in mm)
        body.append(f'<polyline points="{coords}"/>')

    svg = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" version="1.1"\n'
        f'     width="{page_w}mm" height="{page_h}mm"\n'
        f'     viewBox="0 0 {page_w} {page_h}">\n'
        f'<g fill="none" stroke="#000000" stroke-width="{opts.pen}"\n'
        '   stroke-linecap="round" stroke-linejoin="round">\n'
        + "\n".join(body)
        + "\n</g>\n</svg>\n"
    )
    stats.update({"page_mm": [page_w, page_h], "paths": len(body),
                  "points": points})
    return svg, stats


# ------------------------------------------------------------ まとめ役
def export_svg(context, filepath: str, opts: SvgOptions,
               objs=None) -> dict:
    """抽出から書き出しまで。オペレータもバッチもここを呼ぶ。"""
    scene = context.scene
    cam = scene.camera
    if cam is None:
        raise RuntimeError("No active camera in the scene")

    if objs is None:
        objs = target_objects(context)
    if not objs:
        raise RuntimeError("No mesh objects to export")

    width = max(16, int(opts.depth_res))
    height = max(16, int(round(width * scene.render.resolution_y
                               / max(1, scene.render.resolution_x))))

    depth = render_depth_pass(scene, cam, width, height)

    depsgraph = context.evaluated_depsgraph_get()
    project = Projection(cam, depsgraph, width, height)

    collected = extract_lines(objs, depsgraph, cam, opts)
    if not collected:
        raise RuntimeError(
            "No lines found. Run STEP0 or STEP1 first, or enable more "
            "line sources")

    mode, hits = choose_depth_mode(
        depth, project, np.concatenate([d["centers"] for d in collected]),
        opts.seed)

    polylines, stats = visible_polylines(collected, project, depth, opts, mode)
    svg, svg_stats = build_svg(polylines, width, height, opts)

    Path(filepath).write_text(svg, encoding="utf-8")

    stats.update(svg_stats)
    stats.update({"depth_mode": mode, "depth_mode_hits": hits,
                  "resolution": [width, height], "objects": len(objs),
                  "svg": filepath})
    return stats


# ---------------------------------------------------------------- 操作
class FP_OT_EXPORT_SVG(bpy.types.Operator):
    """Export the color-separation boundaries as a plotter-ready SVG."""

    bl_idname = "freepencil.export_svg"
    bl_label = "Export SVG"
    bl_description = ("Write the line art as vector paths for a pen plotter "
                      "(mm, no fill, constant stroke width)")
    bl_options = {'REGISTER'}

    filepath: bpy.props.StringProperty(subtype='FILE_PATH')
    filename_ext = ".svg"
    filter_glob: bpy.props.StringProperty(default="*.svg", options={'HIDDEN'})

    selected_only: bpy.props.BoolProperty(
        name="Selected only",
        description="Export only the selected meshes",
        default=False)

    @classmethod
    def poll(cls, context):
        return context.scene.camera is not None

    def invoke(self, context, event):
        if not self.filepath:
            base = bpy.data.filepath
            name = Path(base).stem if base else "freepencil"
            self.filepath = str(Path(bpy.path.abspath("//") or ".")
                                / f"{name}.svg")
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        if not self.filepath:
            self.report({'ERROR'}, "No output path")
            return {'CANCELLED'}
        path = Path(bpy.path.abspath(self.filepath))
        if path.suffix.lower() != ".svg":
            path = path.with_suffix(".svg")

        opts = SvgOptions.from_scene(context.scene)
        objs = target_objects(context, self.selected_only)
        try:
            stats = export_svg(context, str(path), opts, objs)
        except RuntimeError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

        msg = (f"{stats['paths']} paths, {stats['points']} points, "
               f"pen-up {stats['pen_up_mm']:.0f} mm -> {path.name}")
        logger.info(msg)
        self.report({'INFO'}, msg)
        return {'FINISHED'}
