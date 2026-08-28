#!/usr/bin/env python3
"""
自动命名脚本
用于生成符合 Live2D 规范的图层名和参数名
"""

def generate_layer_name(category: str, position: str = None, index: int = None) -> str:
    """
    生成图层名
    :param category: 类别 (hair, face, eye, mouth, etc.)
    :param position: 位置 (front, back, l, r, etc.)
    :param index: 序号
    :return: 规范图层名
    """
    parts = [category]
    if position:
        parts.append(position)
    if index is not None:
        parts.append(f"{index:02d}")
    return "_".join(parts)


def generate_param_name(base: str, position: str = None) -> str:
    """
    生成参数名
    :param base: 基础名称 (Angle, EyeOpen, MouthOpen, etc.)
    :param position: 位置 (L, R, X, Y, Z, etc.)
    :return: 规范参数名
    """
    name = f"Param{base}"
    if position:
        name += position
    return name


if __name__ == "__main__":
    print("Live2D 自动命名工具")
    print("-----------------")
    
    # 测试图层命名
    print("\n图层命名示例：")
    print(generate_layer_name("hair", "front", 1))
    print(generate_layer_name("eye", "l", None))
    print(generate_layer_name("mouth", "a", None))
    
    # 测试参数命名
    print("\n参数命名示例：")
    print(generate_param_name("Angle", "X"))
    print(generate_param_name("EyeOpen", "L"))
    print(generate_param_name("MouthOpen", "Y"))
