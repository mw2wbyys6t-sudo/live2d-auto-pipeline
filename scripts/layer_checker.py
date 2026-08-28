#!/usr/bin/env python3
"""
图层检查脚本
用于检查 PSD 图层是否符合规范
"""

import re


def check_layer_name(name: str) -> tuple[bool, str]:
    """
    检查图层名是否符合规范
    :param name: 图层名
    :return: (是否符合, 问题描述)
    """
    # 允许的命名模式
    patterns = [
        r"^hair_(front|back|side)_(l|r|)?_\d{2}$",
        r"^hair_(front|back|side)_\d{2}$",
        r"^face_(base|shadow)$",
        r"^eye_(l|r)_(white|iris|pupil)$",
        r"^mouth_(base|a|i|u|e|o)$",
        r"^body_(base|shadow)$",
        r"^clothes_.*$"
    ]
    
    for pattern in patterns:
        if re.match(pattern, name):
            return True, ""
    
    return False, "图层名不符合规范"


def check_layer_list(layers: list[str]) -> list[tuple[str, str]]:
    """
    检查多个图层
    :param layers: 图层名列表
    :return: 问题列表 [(图层名, 问题), ...]
    """
    issues = []
    for layer in layers:
        valid, issue = check_layer_name(layer)
        if not valid:
            issues.append((layer, issue))
    return issues


if __name__ == "__main__":
    print("Live2D 图层检查工具")
    print("-----------------")
    
    test_layers = [
        "hair_front_01",
        "hair_back_02",
        "face_base",
        "eye_l_white",
        "mouth_a",
        "bad_layer_name!",
        "eye_left"
    ]
    
    issues = check_layer_list(test_layers)
    
    if issues:
        print("\n发现问题：")
        for layer, issue in issues:
            print(f"  - {layer}: {issue}")
    else:
        print("\n所有图层名符合规范！")
