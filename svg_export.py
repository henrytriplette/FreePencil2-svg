"""SVG 書き出し(ペンプロッタ用)のコアと、それを呼ぶオペレータ。

レンダー画像をトレースせず、線の定義そのもの――隣り合う面の mecha_color が
違う辺――からベクタを作る。コンポジタの Sobel は幅を持つ帯を描くので、
それを追跡するとプロッタが輪郭を二重になぞってしまう。辺そのものを出せば
常に1本の中心線になる。

線の出どころは5つあり、個別に ON/OFF できる(LINE_SOURCES)。

  mecha      … 面2枚の mecha_color が違う(塗り分け法の線そのもの)
  material   … マテリアルが変わる
  bone       … bone_color が違う(既定 OFF。プロッタでは線が増えすぎる)
  open       … 面が2枚ない(開いた縁・非多様体)
  silhouette … カメラから見て表裏が入れ替わる(外形線)

STEP4 の mask_color / line_color も見る。ビューポートで消した線が SVG に
残らないようにするため。

レイヤーに分けて書き出すと、vpype や Inkscape がレイヤーとして読むので、
ペンを分けられる。出どころ別に分けるときは、深度バッファで「実際に絵の縁に
なっている辺」を outline 層へ振り分ける(contour_mask)。silhouette は薄板の
多いモデルでは内側にも出るので、外周が要るときはこちらを使う。

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

# レイヤーに分けるときの優先順。1本の辺が複数の出どころに当てはまるのは
# 普通なので(塗り分け境界かつ外形線、など)、どれか1つに決める必要がある。
#
# 注意: silhouette は「絵の外周」ではない。隣り合う面の表裏が入れ替わる辺
# すべてなので、薄板の多い CAD では内側にも大量に出る(実測モデルでは
# silhouette 層に格子やパネルの線が入り、外周だけにはならなかった)。
# 外周が要るなら outline を使うこと。こちらは深度バッファで実際に絵の縁に
# なっている辺だけを拾う(contour_mask)。優先順で outline を先頭に置いて
# いるのは、太いペンを割り当てたくなるのがここだから
LAYER_PRIORITY = ("outline", "silhouette", "mecha", "material",
                  "bone", "open")

INKSCAPE_NS = "http://www.inkscape.org/namespaces/inkscape"

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
                 "sort", "keep_hidden", "seed", "sources", "respect_paint",
                 "layers", "outline_layer", "outline_gap",
                 "fit", "plot_speed", "travel_speed", "pen_lift",
                 "split_files")

    def __init__(self, page="A4", margin=10.0, pen=0.3, merge_tolerance=0.1,
                 simplify=0.05, samples=8, bias=0.001, neighbourhood=0,
                 depth_res=1600, sort=True, keep_hidden=False, seed=42,
                 sources=None, respect_paint=True, layers="NONE",
                 outline_layer=True, outline_gap=0.02, fit="DRAWING",
                 plot_speed=80.0, travel_speed=200.0, pen_lift=0.12,
                 split_files=False):
        self.split_files = split_files
        self.fit = fit
        self.plot_speed = plot_speed
        self.travel_speed = travel_speed
        self.pen_lift = pen_lift
        self.layers = layers
        self.outline_layer = outline_layer
        self.outline_gap = outline_gap
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
            layers=g(scene, "fp_svg_layers", "NONE"),
            outline_layer=g(scene, "fp_svg_outline_layer", True),
            outline_gap=g(scene, "fp_svg_outline_gap", 0.02),
            fit=g(scene, "fp_svg_fit", "DRAWING"),
            plot_speed=g(scene, "fp_svg_plot_speed", 80.0),
            travel_speed=g(scene, "fp_svg_travel_speed", 200.0),
            pen_lift=g(scene, "fp_svg_pen_lift", 0.12),
            split_files=g(scene, "fp_svg_split_files", False),
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

        # レイヤー分け用に、辺ごとの出どころを1つへ畳む。優先度の低いほうから
        # 書いていき、高いもので上書きする
        labels = np.full(ne, -1, dtype=np.int8)
        for prio in range(len(LAYER_PRIORITY) - 1, -1, -1):
            m = parts.get(LAYER_PRIORITY[prio])
            if m is not None:
                labels[m] = prio

        nv = len(mesh.vertices)
        co = np.empty(nv * 3, dtype=np.float32)
        mesh.vertices.foreach_get("co", co)
        co = co.reshape(nv, 3).astype(np.float64)

        m4 = np.array(eval_obj.matrix_world, dtype=np.float64)
        world = co @ m4[:3, :3].T + m4[:3, 3]
        centers = topo.center.astype(np.float64) @ m4[:3, :3].T + m4[:3, 3]

        return {"verts": world, "edges": ev[keep], "centers": centers,
                "counts": counts, "labels": labels[keep], "object": obj.name}
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

    # 部分可視は外形線をまたぐ辺くらいなので、ここだけ Python で刻む。
    # どの辺から出たかも返す(レイヤー分けで出どころが要る)
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
            pieces.append((int(i),
                           v0[i] + d * (j / s), v0[i] + d * ((k + 1) / s)))
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


def contour_mask(data, project, depth, opts: SvgOptions,
                 mode: str, offset_px: float = 2.0) -> np.ndarray:
    """深度バッファを見て、外周(背景か大きな段差に接する辺)を拾う。

    silhouette とは別物。silhouette は「隣り合う面の表裏が入れ替わる辺」で、
    薄板の多いモデルでは内側にも大量に出る。こちらは画面上で辺の左右を
    覗き、片側が背景か、手前の面よりずっと奥なら外周と見なす。つまり
    「実際に絵の縁になっている辺」だけが残る。

    新しい線は足さない。既にある辺のどれが外周かを見分けるだけ。
    """
    verts, edges = data["verts"], data["edges"]
    m = len(edges)
    if m == 0:
        return np.zeros(0, dtype=bool)

    v0 = verts[edges[:, 0]]
    v1 = verts[edges[:, 1]]
    x0, y0, _, _, _ = project(v0)
    x1, y1, _, _, _ = project(v1)

    # 画面上での辺の向きに直交する方向へずらして左右を覗く
    dx, dy = x1 - x0, y1 - y0
    length = np.hypot(dx, dy)
    length = np.where(length < 1e-9, 1.0, length)
    nx = -dy / length * offset_px
    ny = dx / length * offset_px

    out = np.zeros(m, dtype=bool)
    for t in (0.25, 0.5, 0.75):
        p = v0 + (v1 - v0) * t
        x, y, plane, ray, inside = project(p)
        ref = ray if mode == "ray" else plane

        near = sample_depth(depth, x + nx, y + ny, 0)
        far = sample_depth(depth, x - nx, y - ny, 0)

        # NaN = その画素が背景。片側が背景なら、そこは絵の縁
        background = np.isnan(near) | np.isnan(far)
        limit = ref * (1.0 + opts.outline_gap)
        jump = ((np.nan_to_num(near, nan=np.inf) > limit)
                | (np.nan_to_num(far, nan=np.inf) > limit))
        out |= inside & (background | jump)
    return out


def _layer_keys(data, opts: SvgOptions, n: int) -> np.ndarray:
    """辺ごとのレイヤー名。分けない場合は全部同じ名前になる。"""
    if opts.layers == "SOURCE":
        names = np.array(LAYER_PRIORITY + ("other",))
        idx = np.where(data["labels"] < 0, len(LAYER_PRIORITY),
                       data["labels"])
        return names[idx]
    if opts.layers == "OBJECT":
        return np.full(n, data["object"], dtype=object)
    return np.full(n, "lines", dtype=object)


def visible_polylines(collected: list, project, depth, opts: SvgOptions,
                      mode: str) -> tuple:
    """見えている線を、レイヤーごとのピクセル座標の折れ線にする。

    鎖はレイヤーをまたがずに作る。またいで繋ぐと、外形線と内側の線が
    1本になってペンを分けられなくなる。分けない設定(layers=NONE)では
    全部が同じレイヤーなので、従来どおり最長まで繋がる。
    """
    groups = defaultdict(list)
    counts = defaultdict(int)
    n_edges = 0
    for data in collected:
        for k, v in data["counts"].items():
            counts[k] += v
        edges = data["edges"]
        n_edges += len(edges)
        verts = data["verts"]

        full, pieces = visible_spans(data, project, depth, opts, mode)
        keys = _layer_keys(data, opts, len(edges))
        if opts.layers == "SOURCE" and opts.outline_layer:
            # 外周は出どころではなく見え方で決まるので、ここで上書きする
            keys[contour_mask(data, project, depth, opts, mode)] = "outline"

        for name in dict.fromkeys(keys[full].tolist()):
            sel = full & (keys == name)
            for path in chain_edges(edges[sel]):
                x_px, y_px, _, _, _ = project(verts[np.asarray(path)])
                groups[name].append(np.stack([x_px, y_px], axis=1))

        if pieces:
            pts = np.asarray([p for _, a, b in pieces for p in (a, b)])
            x_px, y_px, _, _, _ = project(pts)
            xy = np.stack([x_px, y_px], axis=1).reshape(-1, 2, 2)
            for (edge_i, _, _), seg in zip(pieces, xy):
                groups[keys[edge_i]].append(seg)

    # 重なりがあるので、出どころ別の合計は edges_line より多くなる
    stats = {"edges_line": int(n_edges), "edges_by_source": dict(counts)}
    return dict(groups), stats


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


def _layer_order(names) -> list:
    """レイヤーの並びを決める。出どころ別なら優先順、それ以外は名前順。"""
    known = [n for n in LAYER_PRIORITY if n in names]
    rest = sorted(n for n in names if n not in LAYER_PRIORITY)
    return known + rest


def _page_transform(groups, page_w: float, page_h: float,
                    width: int, height: int, opts: SvgOptions) -> tuple:
    """ピクセル座標 -> 紙(mm)の拡大率と原点。

    CAMERA … カメラのフレームを紙に合わせる。構図がそのまま出る代わり、
             被写体が小さく写っていれば紙の上でも小さいままになる
    DRAWING… 実際に描かれた範囲を紙いっぱいに合わせる。余白が一定になり、
             結合の許容量(mm)も絵に対して素直に効く
    """
    if opts.fit == "DRAWING":
        pts = [pl for v in groups.values() for pl in v]
        if pts:
            allp = np.concatenate(pts)
            lo, hi = allp.min(axis=0), allp.max(axis=0)
            span = np.maximum(hi - lo, 1e-9)
            scale = min((page_w - 2 * opts.margin) / span[0],
                        (page_h - 2 * opts.margin) / span[1])
            off_x = (page_w - span[0] * scale) * 0.5 - lo[0] * scale
            off_y = (page_h - span[1] * scale) * 0.5 - lo[1] * scale
            return scale, off_x, off_y

    scale = min((page_w - 2 * opts.margin) / width,
                (page_h - 2 * opts.margin) / height)
    return scale, (page_w - width * scale) * 0.5, \
        (page_h - height * scale) * 0.5


def drawn_length(lines) -> float:
    """ペンを下ろして描く距離の合計(mm)。"""
    total = 0.0
    for ln in lines:
        if len(ln) > 1:
            total += float(np.hypot(*(ln[1:] - ln[:-1]).T).sum())
    return total


def estimate_seconds(draw_mm: float, pen_up_mm: float, paths: int,
                     opts: SvgOptions) -> float:
    """プロット時間のざっくり見積り。

    描く距離と移動距離をそれぞれの速度で割り、ペンの上げ下ろし1回ぶんの
    固定費を本数だけ足す。機種ごとの加減速は見ていないので目安。
    """
    draw_speed = max(opts.plot_speed, 1e-6)
    move_speed = max(opts.travel_speed, 1e-6)
    return (draw_mm / draw_speed + pen_up_mm / move_speed
            + paths * opts.pen_lift)


def build_svg(groups, width: int, height: int, opts: SvgOptions,
              transform=None) -> tuple:
    """レイヤーごとの折れ線を mm に移し、SVG 文字列と統計を返す。

    結合も並べ替えもレイヤーの中だけで行う。またいで結合するとペンを
    分けられなくなるし、並べ替えをまたぐとペンの持ち替えが増える。
    """
    if isinstance(groups, list):        # 単層で呼ばれた場合
        groups = {"lines": groups}

    page_w, page_h = page_mm(opts.page, width, height)
    # 層ごとに別ファイルへ書くときは、全部の層から出した変換を渡してもらう。
    # ファイルごとに計算し直すと、層の位置が紙の上でずれて重ならない
    if transform is None:
        transform = _page_transform(groups, page_w, page_h,
                                    width, height, opts)
    scale, off_x, off_y = transform

    raw_total = sum(len(v) for v in groups.values())
    stats = {"paths_raw": raw_total, "layers": {}}

    body = []
    points = 0
    paths = 0
    pen_up = 0.0
    draw_mm = 0.0
    merged_total = 0

    for i, name in enumerate(_layer_order(groups.keys()), start=1):
        mm_lines = []
        for pl in groups[name]:
            mm = np.empty_like(pl)
            mm[:, 0] = off_x + pl[:, 0] * scale
            mm[:, 1] = off_y + pl[:, 1] * scale
            mm_lines.append(mm)

        # つないでから間引く。逆にすると継ぎ目で折れが残る
        merged = linemerge(mm_lines, opts.merge_tolerance)
        merged_total += len(merged)
        lines = [s for s in (rdp(ln, opts.simplify) for ln in merged)
                 if len(s) >= 2]
        if opts.sort:
            lines = linesort(lines)
        pen_up += pen_up_travel(lines)
        draw_mm += drawn_length(lines)

        rows = []
        for mm in lines:
            points += len(mm)
            coords = " ".join(f"{x:.3f},{y:.3f}" for x, y in mm)
            rows.append(f'<polyline points="{coords}"/>')
        paths += len(rows)
        stats["layers"][name] = len(rows)

        if opts.layers == "NONE":
            attrs = ""
        else:
            # vpype と Inkscape はこの2属性でレイヤーとして読む
            attrs = (f' inkscape:groupmode="layer" inkscape:label="{name}"'
                     f' id="layer{i}"')
        body.append(
            f'<g{attrs} fill="none" stroke="#000000"'
            f' stroke-width="{opts.pen}"\n'
            '   stroke-linecap="round" stroke-linejoin="round">\n'
            + "\n".join(rows) + "\n</g>")

    ns = "" if opts.layers == "NONE" else f'\n     xmlns:inkscape="{INKSCAPE_NS}"'
    svg = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" version="1.1"{ns}\n'
        f'     width="{page_w}mm" height="{page_h}mm"\n'
        f'     viewBox="0 0 {page_w} {page_h}">\n'
        + "\n".join(body)
        + "\n</svg>\n"
    )
    stats.update({"page_mm": [page_w, page_h], "paths": paths,
                  "paths_merged": merged_total, "points": points,
                  "pen_up_mm": round(pen_up, 1),
                  "draw_mm": round(draw_mm, 1),
                  "estimated_seconds": round(
                      estimate_seconds(draw_mm, pen_up, paths, opts), 1)})
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

    groups, stats = visible_polylines(collected, project, depth, opts, mode)

    page_w, page_h = page_mm(opts.page, width, height)
    transform = _page_transform(groups, page_w, page_h, width, height, opts)

    base = Path(filepath)
    written = []
    if opts.split_files and opts.layers != "NONE" and len(groups) > 1:
        # ペンごとに1枚。位置を合わせるため変換は全層ぶんから作って共有する
        svg_stats = {"paths": 0, "points": 0, "paths_raw": 0,
                     "paths_merged": 0, "pen_up_mm": 0.0, "draw_mm": 0.0,
                     "estimated_seconds": 0.0, "layers": {},
                     "page_mm": [page_w, page_h]}
        for name in _layer_order(groups.keys()):
            one, st = build_svg({name: groups[name]}, width, height, opts,
                                transform)
            out = base.with_name(f"{base.stem}_{name}{base.suffix}")
            out.write_text(one, encoding="utf-8")
            written.append(str(out))
            for k in ("paths", "points", "paths_raw", "paths_merged",
                      "pen_up_mm", "draw_mm", "estimated_seconds"):
                svg_stats[k] += st[k]
            svg_stats["layers"].update(st["layers"])
        for k in ("pen_up_mm", "draw_mm", "estimated_seconds"):
            svg_stats[k] = round(svg_stats[k], 1)
    else:
        svg, svg_stats = build_svg(groups, width, height, opts, transform)
        base.write_text(svg, encoding="utf-8")
        written.append(str(base))

    stats.update(svg_stats)
    stats.update({"depth_mode": mode, "depth_mode_hits": hits,
                  "resolution": [width, height], "objects": len(objs),
                  "svg": written[0], "files": written})
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

        mins = stats["estimated_seconds"] / 60.0
        summary = (
            f"{stats['paths']} paths, {stats['points']} points"
            f"|draw {stats['draw_mm']:.0f} mm, "
            f"travel {stats['pen_up_mm']:.0f} mm"
            f"|approx {mins:.1f} min at {opts.plot_speed:.0f} mm/s")
        context.scene.fp_svg_last_result = summary

        msg = summary.replace("|", "; ") + f" -> {path.name}"
        logger.info(msg)
        self.report({'INFO'}, msg)
        return {'FINISHED'}


# ------------------------------------------------------ ビューポート表示
# 書き出す前に線を見られるようにする。SVG を開き直さないと結果が分からない
# のが一番の手間だったので、3Dビューに直接引く。投影する前の3D線分をその
# まま描くだけなので、線の作り方はここに重複しない。
#
# 隠線処理はレンダーカメラから見て計算しているため、正しく見えるのは
# カメラビューのときだけ。回すと隠れ方は合わなくなる(線の位置は合う)。
_preview_segments = None      # (N, 2, 3) のワールド座標
_preview_handle = None
_preview_info = ""


def compute_preview(context, opts: SvgOptions = None) -> tuple:
    """見えている線を3D線分として返す。書き出しと同じ経路を通る。"""
    scene = context.scene
    cam = scene.camera
    if cam is None:
        raise RuntimeError("No active camera in the scene")
    if opts is None:
        opts = SvgOptions.from_scene(scene)

    objs = target_objects(context)
    if not objs:
        raise RuntimeError("No mesh objects to preview")

    width = max(16, int(opts.depth_res))
    height = max(16, int(round(width * scene.render.resolution_y
                               / max(1, scene.render.resolution_x))))

    depth = render_depth_pass(scene, cam, width, height)
    depsgraph = context.evaluated_depsgraph_get()
    project = Projection(cam, depsgraph, width, height)

    collected = extract_lines(objs, depsgraph, cam, opts)
    if not collected:
        raise RuntimeError("No lines found. Run STEP0 or STEP1 first")

    mode, _ = choose_depth_mode(
        depth, project,
        np.concatenate([d["centers"] for d in collected]), opts.seed)

    segs = []
    for data in collected:
        verts, edges = data["verts"], data["edges"]
        full, pieces = visible_spans(data, project, depth, opts, mode)
        if full.any():
            e = edges[full]
            segs.append(np.stack([verts[e[:, 0]], verts[e[:, 1]]], axis=1))
        if pieces:
            segs.append(np.asarray([[a, b] for _, a, b in pieces]))

    if not segs:
        return np.zeros((0, 2, 3)), 0
    out = np.concatenate(segs).astype(np.float32)
    return out, len(out)


def _draw_preview():
    """3Dビューのコールバック。POST_VIEW で線分をそのまま引く。"""
    import gpu
    from gpu_extras.batch import batch_for_shader

    if _preview_segments is None or len(_preview_segments) == 0:
        return
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    batch = batch_for_shader(
        shader, 'LINES',
        {"pos": _preview_segments.reshape(-1, 3)})
    gpu.state.line_width_set(1.5)
    gpu.state.depth_test_set('NONE')
    gpu.state.blend_set('ALPHA')
    shader.bind()
    shader.uniform_float("color", (0.05, 0.05, 0.05, 0.9))
    batch.draw(shader)
    gpu.state.blend_set('NONE')
    gpu.state.line_width_set(1.0)


def preview_enabled() -> bool:
    return _preview_handle is not None


def enable_preview() -> None:
    global _preview_handle
    if _preview_handle is None:
        _preview_handle = bpy.types.SpaceView3D.draw_handler_add(
            _draw_preview, (), 'WINDOW', 'POST_VIEW')


def disable_preview() -> None:
    """ハンドラを外す。アドオンの unregister からも呼ぶこと。"""
    global _preview_handle, _preview_segments
    if _preview_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_preview_handle, 'WINDOW')
        _preview_handle = None
    _preview_segments = None


def refresh_preview(context) -> str:
    """線を計算し直して覚える。戻り値は表示用の一行。"""
    global _preview_segments, _preview_info
    segs, n = compute_preview(context)
    _preview_segments = segs
    _preview_info = f"{n} segments"
    for area in getattr(context.screen, "areas", ()):
        if area.type == 'VIEW_3D':
            area.tag_redraw()
    return _preview_info


def preview_info() -> str:
    return _preview_info


class FP_OT_SVG_PREVIEW(bpy.types.Operator):
    """Compute the vector lines and show them in the viewport."""

    bl_idname = "freepencil.svg_preview"
    bl_label = "Refresh preview"
    bl_description = ("Work out the lines that would be exported and draw "
                      "them in the 3D view. Look through the camera: hidden "
                      "line removal is computed for the render camera")
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return context.scene.camera is not None

    def execute(self, context):
        try:
            info = refresh_preview(context)
        except RuntimeError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        enable_preview()
        context.scene.fp_svg_preview = True
        self.report({'INFO'}, f"Preview: {info}")
        return {'FINISHED'}


class FP_OT_SVG_PREVIEW_CLEAR(bpy.types.Operator):
    """Stop drawing the preview."""

    bl_idname = "freepencil.svg_preview_clear"
    bl_label = "Clear preview"
    bl_description = "Remove the preview lines from the 3D view"
    bl_options = {'REGISTER'}

    def execute(self, context):
        disable_preview()
        context.scene.fp_svg_preview = False
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
        return {'FINISHED'}


# ------------------------------------------------------------ プリセット
# プロパティが増えたので、よく使う組み合わせに名前を付ける。ここを弄れば
# パネルの選択肢もそのまま増える
SVG_PRESETS = {
    "FINE": {
        "label": "Fine pen",
        "values": {"fp_svg_pen": 0.3, "fp_svg_merge_tolerance": 0.1,
                   "fp_svg_simplify": 0.05, "fp_svg_layers": "NONE",
                   "fp_svg_depth_res": 1600, "fp_svg_samples": 8},
    },
    "BOLD_OUTLINE": {
        "label": "Bold outline, 2 pens",
        "values": {"fp_svg_pen": 0.5, "fp_svg_merge_tolerance": 0.2,
                   "fp_svg_simplify": 0.08, "fp_svg_layers": "SOURCE",
                   "fp_svg_outline_layer": True, "fp_svg_outline_gap": 0.10,
                   "fp_svg_split_files": True, "fp_svg_depth_res": 1600},
    },
    "DRAFT": {
        "label": "Quick draft",
        "values": {"fp_svg_pen": 0.5, "fp_svg_merge_tolerance": 0.3,
                   "fp_svg_simplify": 0.25, "fp_svg_layers": "NONE",
                   "fp_svg_depth_res": 800, "fp_svg_samples": 4,
                   "fp_svg_src_open": False},
    },
}


class FP_OT_SVG_PRESET(bpy.types.Operator):
    """Apply a set of SVG export settings."""

    bl_idname = "freepencil.svg_preset"
    bl_label = "Preset"
    bl_description = "Apply a ready-made combination of export settings"
    bl_options = {'REGISTER', 'UNDO'}

    preset: bpy.props.EnumProperty(
        name="Preset",
        items=[(k, v["label"], v["label"]) for k, v in SVG_PRESETS.items()],
        default="FINE")

    def execute(self, context):
        spec = SVG_PRESETS.get(self.preset)
        if spec is None:
            self.report({'ERROR'}, f"Unknown preset: {self.preset}")
            return {'CANCELLED'}
        for name, value in spec["values"].items():
            setattr(context.scene, name, value)
        self.report({'INFO'}, f"Preset: {spec['label']}")
        return {'FINISHED'}


# ------------------------------------------------------- カメラ一括出力
class FP_OT_EXPORT_SVG_CAMERAS(bpy.types.Operator):
    """Export an SVG for every camera ticked in STEP5."""

    bl_idname = "freepencil.export_svg_cameras"
    bl_label = "Export checked cameras"
    bl_description = ("Write one SVG per checked camera into "
                      "//svg_exports/. Uses the same camera ticks as STEP5")
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return bool(bpy.data.filepath)

    def execute(self, context):
        import os
        import re

        scene = context.scene
        if not bpy.data.filepath:
            self.report({'ERROR'}, "Save the .blend file first")
            return {'CANCELLED'}

        cameras = sorted(
            (o for o in scene.objects
             if o.type == "CAMERA" and getattr(o, "fp_cam_render", True)),
            key=lambda o: o.name.lower())
        if not cameras:
            self.report({'ERROR'}, "No cameras checked")
            return {'CANCELLED'}

        root = bpy.path.abspath("//svg_exports")
        os.makedirs(root, exist_ok=True)
        opts = SvgOptions.from_scene(scene)
        objs = target_objects(context)

        original = scene.camera
        done, failed = 0, []
        try:
            for index, camera in enumerate(cameras, start=1):
                scene.camera = camera
                context.view_layer.update()
                safe = re.sub(r'[\\/:*?"<>|]', "_", camera.name)
                out = os.path.join(root, f"{index:02d}_{safe}.svg")
                try:
                    export_svg(context, out, opts, objs)
                    done += 1
                except RuntimeError as exc:
                    # 1台こけても残りは出す。どれが駄目だったかは報告する
                    logger.exception("SVG export failed for %s", camera.name)
                    failed.append(f"{camera.name}: {exc}")
        finally:
            scene.camera = original
            context.view_layer.update()

        if failed:
            self.report({'WARNING'},
                        f"{done}/{len(cameras)} cameras -> {root} "
                        f"({len(failed)} failed: {failed[0]})")
        else:
            self.report({'INFO'}, f"{done} cameras -> {root}")
        return {'FINISHED'}
