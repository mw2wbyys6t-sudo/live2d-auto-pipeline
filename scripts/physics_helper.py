#!/usr/bin/env python3
"""
物理设置辅助脚本
用于生成物理参数建议
"""

from dataclasses import dataclass


@dataclass
class PhysicsParams:
    gravity: float = 0.5
    wind: float = 0.0
    restitution: float = 0.5
    damping: float = 0.9
    point_count: int = 5


def get_physics_suggestion(part: str) -> PhysicsParams:
    """
    获取物理参数建议
    :param part: 部件类型 (hair_front, hair_back, ear, tail, etc.)
    :return: 物理参数
    """
    suggestions = {
        "hair_front": PhysicsParams(gravity=0.4, restitution=0.6, point_count=5),
        "hair_back": PhysicsParams(gravity=0.7, restitution=0.5, point_count=8),
        "ear": PhysicsParams(gravity=0.3, restitution=0.7, point_count=3),
        "tail": PhysicsParams(gravity=0.6, restitution=0.5, point_count=10),
        "ribbon": PhysicsParams(gravity=0.5, restitution=0.4, point_count=6),
    }
    
    return suggestions.get(part, PhysicsParams())


if __name__ == "__main__":
    print("Live2D 物理参数建议工具")
    print("---------------------")
    
    test_parts = ["hair_front", "hair_back", "ear", "tail"]
    
    for part in test_parts:
        params = get_physics_suggestion(part)
        print(f"\n{part}:")
        print(f"  重力: {params.gravity}")
        print(f"  风力: {params.wind}")
        print(f"  回复力: {params.restitution}")
        print(f"  阻尼: {params.damping}")
        print(f"  物理点数量: {params.point_count}")
