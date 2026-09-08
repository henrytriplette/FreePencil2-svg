# note / GitHub 用アセットの生成

配布ページに載せる画像を、すべてスクリプトから作り直せるようにしたもの。
手作業の加工はしない。**一時ディレクトリには置かない**（v2.5.0 公開時、
スクラッチパッドに置いた生成物とスクリプトを一式失った）。

出力先は既定で `dev/note_assets/out/`。このフォルダは配布ZIPからも
git からも除外される。

## 作例レンダー

```bat
blender -b --factory-startup --python render_samples.py -- ^
  --blend <asset.blend> --name mecha --out out --res 1600
```

同じカメラで2枚出る。

- `<name>_line.png` — 最終線画（白マテリアル + PROコンポジタ）
- `<name>_paint.png` — 塗り分けの状態（mecha_color 頂点カラーをそのまま表示）

そのあと透過PNGを白背景に載せてトリミングする。

```bat
python compose_samples.py
```

- `01_<name>_line.png` — 線画（幅1600）
- `03_<name>_paint.png` — 塗り分け
- `04_paint_to_line.png` — 「塗り分け → 線画」の対比図（キャプション付き）

## UI スクリーンショット

GUI の Blender を2フェーズで動かす。フェーズ1で撮影用の .blend を作り、
フェーズ2でそれを開いて撮る。**ファイルを渡して起動するとスプラッシュが
出ない**ので、この順序が要る。

```bat
blender -b --factory-startup --python ui_prepare.py -- ^
  --blend <asset.blend> --save out\ui_scene.blend

blender --factory-startup -p 0 0 2400 1400 out\ui_scene.blend ^
  --python ui_shots.py -- --out out

python compose_ui.py
```

撮影は日本語UI・UIスケール1.25。実際にインストールされている拡張機能を
有効化して撮るので、**同期を先に済ませること**（`scripts\sync_extension.bat`）。

## 計測

```bat
blender -b --factory-startup --python time_step0.py -- --blend <asset.blend> --name mecha
```

STEP0 を押してから終わるまでの実時間。記事に載せる「処理時間」はこれ。
STEP1 単体の時間ではない。

## SVG 書き出し

線を作るコアは本体の `svg_export.py` にある。このスクリプトはアセットの
下ごしらえ（読み込み・正規化・地面板の除去・デモシーン）だけを持ち、
書き出し自体はコアを呼ぶ。**バッチとUIが同一コードパス**なので、片方だけ
直して線がずれることがない。

```bat
blender -b --factory-startup --python export_svg_lines.py -- --demo
blender -b --factory-startup --python export_svg_lines.py -- ^
  --blend <asset.blend> --name mecha
```

`out\<name>_lines.svg` が出る。mm 単位・塗りなし・一定線幅。

実測(KD250、104万面、1600px、A4横):

| 段階 | 本数 | ペン移動(mm) |
|---|---|---|
| 分岐で切った鎖 | 26927 | 183948 |
| `linemerge`(既定 0.1mm) | 3286 | 41389 |
| `linesort` | 3286 | 2997 |

`--merge-tolerance` は絵の大きさではなくペン幅で決めること。紙に対して
絵が小さいと、無関係な端点まで許容内に入って本数だけが減る(地面板を
消す前、絵が 37mm 幅だったときは 685 本まで落ちていたが、0.44mm 相当の
過剰な結合だった。同じ倍率を今の絵に当てると 982 本になる)。

線の出どころは `--sources` で選ぶ（既定は bone 以外すべて）。

```bat
blender -b --factory-startup --python export_svg_lines.py -- ^
  --blend <asset.blend> --name mecha --sources mecha,material,silhouette
```

`--ignore-paint` で STEP4 の `mask_color` / `line_color` を無視できる。

出力の `edges_by_source` は出どころ別の本数だが、**重なりがあるので合計は
`edges_line` より多くなる**（1本の辺が塗り分け境界かつ外形線であることは
普通にある）。評価用アセットは `apply_white_material` で単一マテリアルに
されるので、`material` はこのパイプラインでは 0 になる。

切り分け用のスイッチ。

- `--keep-hidden` … 隠線処理を飛ばす（消えすぎ／消えなさすぎの判定）
- `--no-occluder` … `--demo` の手前の箱を置かない。消えた線はすべて
  自己遮蔽が原因になるので、`--bias` の詰めはこの状態で見る
- `--keep-ground` … 地面板の自動除去をしない。既定では「平ら、かつ
  本体より大きい」板を落とす（KD250 では `Plane` 1面が本体の4.5倍あり、
  カメラのフィットが引っ張られて本体が豆粒になっていた）。落としたものは
  出力の `dropped` に名前が出る
- `--exclude <正規表現>` … 名前で除く
- `--neighbourhood` … 深度参照の近傍半径。1以上は近傍の最大値を採るので
  緩い。CAD 由来の密なモデルでは中身が外板を透ける（104万面で実測:
  61553→32835本に減り、内部の機械が消えた）。既定は 0

Z パスが「平面距離」か「光線距離」かは実測で決めている（面の中心を投影して
深度バッファと突き合わせ、一致した本数が多いほうを採る）。推測で書かないこと。
出力の `depth_mode_hits` がその内訳。

reloop・layout・HPGL 出力が要るときだけ外から vpype を通す。

```bat
vpype read out\demo_lines.svg reloop linesort write plot.svg
```

## 改変チェック

```bat
blender -b --factory-startup --python verify_mutation.py -- --blend <asset.blend>
```

STEP0 がモデルの何を書き換えるかを前後比較で出す。
「モデルを改変しない」と書いてよいかの判断に使う。推測で書かないこと。
