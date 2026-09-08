"""FreePencil test ノードグループ (Blender 5.x 用)。

4.x 用スクリプトで 4.5 に作らせたツリーを 5.2 で開き、Blender 自身に
移行させたうえで書き出したもの。pro_5x と同じ作り方。

4.x 用スクリプトを 5.x で直接動かすと、CompositorNodeFilter の入力
ソケットの並びが違う(4.x: Fac, Image / 5.x: Image, Factor, Type)ため、
添字で書かれた代入とリンクが壊れる。
"""
import bpy


def _sock(coll, idx, name):
    if 0 <= idx < len(coll) and coll[idx].name == name:
        return coll[idx]
    for s in coll:
        if s.name == name:
            return s
    return None


def _in(node, idx, name):
    return _sock(node.inputs, idx, name)


def create_node_tree_freepencil_v1_1_0_test():
    ng = bpy.data.node_groups.new('FreePencil_v1_1_0_test', 'CompositorNodeTree')
    ng.interface.new_socket(name='Image', in_out='OUTPUT', socket_type='NodeSocketColor')
    ng.interface.new_socket(name='alpha', in_out='INPUT', socket_type='NodeSocketFloat')
    ng.interface.new_socket(name='mecha_color', in_out='INPUT', socket_type='NodeSocketColor')
    n_0 = ng.nodes.new('ShaderNodeMix')
    n_0.data_type = 'RGBA'
    n_0.clamp_factor = False
    n_0.factor_mode = 'UNIFORM'
    n_0.name = 'Mix'
    n_0.label = 'freepencil'
    n_0.location = (-161.46719360351562, -3.7871856689453125)
    n_0.hide = False
    n_0.width = 137.07122802734375
    n_0.blend_type = 'MIX'
    n_0.data_type = 'RGBA'
    _s = _in(n_0, 1, 'Factor')
    if _s is not None:
        _s.default_value = (0.5, 0.5, 0.5)
    _s = _in(n_0, 2, 'A')
    if _s is not None:
        _s.default_value = 0.0
    _s = _in(n_0, 3, 'B')
    if _s is not None:
        _s.default_value = 0.0
    _s = _in(n_0, 4, 'A')
    if _s is not None:
        _s.default_value = (0.0, 0.0, 0.0)
    _s = _in(n_0, 5, 'B')
    if _s is not None:
        _s.default_value = (0.0, 0.0, 0.0)
    _s = _in(n_0, 6, 'A')
    if _s is not None:
        _s.default_value = (1.0, 1.0, 1.0, 1.0)
    _s = _in(n_0, 8, 'A')
    if _s is not None:
        _s.default_value = (0.0, 0.0, 0.0)
    _s = _in(n_0, 9, 'B')
    if _s is not None:
        _s.default_value = (0.0, 0.0, 0.0)

    n_1 = ng.nodes.new('CompositorNodeFilter')
    n_1.name = 'Filter'
    n_1.label = 'freepencil'
    n_1.location = (19.167556762695312, -0.9434661865234375)
    n_1.hide = False
    n_1.width = 100.0
    _s = _in(n_1, 1, 'Factor')
    if _s is not None:
        _s.default_value = 1.0
    _s = _in(n_1, 2, 'Type')
    if _s is not None:
        _s.default_value = 'Sobel'

    n_2 = ng.nodes.new('NodeGroupOutput')
    n_2.name = 'Group Output'
    n_2.label = ''
    n_2.location = (451.4671630859375, 0.0)
    n_2.hide = False

    n_3 = ng.nodes.new('NodeGroupInput')
    n_3.name = 'Group Input'
    n_3.label = ''
    n_3.location = (-361.4671936035156, 0.0)
    n_3.hide = False

    n_4 = ng.nodes.new('ShaderNodeValToRGB')
    n_4.name = 'ColorRamp'
    n_4.label = 'freepencil'
    n_4.location = (173.86622619628906, 12.110574722290039)
    n_4.hide = False
    # ===== Shader ColorRamp for n_4 =====
    # Original: 2 elements
    ramp = n_4.color_ramp
    
    # Blender の仕様: ColorRamp は要素を 0 個にできない
    # 戦略: 既定要素を残して上書きする
    
    # 2 要素の場合: デフォルト要素を直接上書き
    ramp.elements[0].position = 0.0000000000
    ramp.elements[0].color = (1.000000, 1.000000, 1.000000, 1.000000)
    
    ramp.elements[1].position = 0.1000000015
    ramp.elements[1].color = (0.000000, 0.000000, 0.000000, 1.000000)
    # ColorRamp 設定
    ramp.interpolation = 'CONSTANT'
    ramp.color_mode = 'RGB'
    ramp.hue_interpolation = 'NEAR'
    

    n_5 = ng.nodes.new('ShaderNodeMath')
    n_5.name = 'Math'
    n_5.label = 'freepencil'
    n_5.location = (109.16755676269531, -30.943466186523438)
    n_5.hide = True
    n_5.operation = 'MULTIPLY'
    _s = _in(n_5, 1, 'Value')
    if _s is not None:
        _s.default_value = 0.5773502588272095
    _s = _in(n_5, 2, 'Value')
    if _s is not None:
        _s.default_value = 0.5

    n_6 = ng.nodes.new('ShaderNodeVectorMath')
    n_6.name = 'Vector Math'
    n_6.label = ''
    n_6.location = (59.16755676269531, -30.943466186523438)
    n_6.hide = True
    n_6.operation = 'DOT_PRODUCT'
    _s = _in(n_6, 1, 'Vector')
    if _s is not None:
        _s.default_value = (0.5773502588272095, 0.5773502588272095, 0.5773502588272095)
    _s = _in(n_6, 2, 'Vector')
    if _s is not None:
        _s.default_value = (0.0, 0.0, 0.0)
    _s = _in(n_6, 3, 'Scale')
    if _s is not None:
        _s.default_value = 1.0

    n_7 = ng.nodes.new('CompositorNodeSeparateColor')
    n_7.name = 'Separate Color'
    n_7.label = ''
    n_7.location = (-171.46719360351562, -3.7871856689453125)
    n_7.hide = False
    _s = _in(n_7, 0, 'Image')
    if _s is not None:
        _s.default_value = (1.0, 1.0, 1.0, 1.0)

    n_8 = ng.nodes.new('CompositorNodeSetAlpha')
    n_8.name = 'Set Alpha'
    n_8.label = ''
    n_8.location = (-171.46719360351562, -3.7871856689453125)
    n_8.hide = False
    _s = _in(n_8, 2, 'Type')
    if _s is not None:
        _s.default_value = 'Replace Alpha'

    def _link(f, fi, fsock, t, ti, tsock):
        a = _sock(f.outputs, fi, fsock)
        b = _in(t, ti, tsock)
        if a is not None and b is not None:
            ng.links.new(a, b)

    # links:
    _link(n_3, 0, 'alpha', n_0, 0, 'Factor')
    _link(n_4, 0, 'Color', n_2, 0, 'Image')
    _link(n_3, 1, 'mecha_color', n_0, 7, 'B')
    _link(n_5, 0, 'Value', n_4, 0, 'Factor')
    _link(n_1, 0, 'Image', n_6, 0, 'Vector')
    _link(n_6, 1, 'Value', n_5, 0, 'Value')
    _link(n_0, 2, 'Result', n_8, 0, 'Image')
    _link(n_7, 3, 'Alpha', n_8, 1, 'Alpha')
    _link(n_8, 0, 'Image', n_1, 0, 'Image')

    return ng

# usage: ng = create_node_tree_freepencil_v1_1_0_test()

create_node_tree = create_node_tree_freepencil_v1_1_0_test  # backward-compat alias
