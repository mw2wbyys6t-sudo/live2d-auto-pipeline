"""motion3.json 生成器：只给真正有键形驱动的参数排曲线。

三个刻意的取舍：

* **动画范围 = 键形范围**。曲线在 ``drivable_parameters()`` 给出的首末键值之间
  摆动，而不是参数声明的 Min/Max —— 超出已编译键形的区间内核只能外推，
  那正是「文件看着对、画面乱掉」的来源。
* **密集线性采样，不用贝塞尔**。这样「运行时在 t 时刻应当取到的值」可以直接由
  Segments 线性插值得到，验收测试就能逐帧比对文件与内核观测值，而不是只判断
  「画面好像动了」。观感需要贝塞尔时再升级，判据不变。
* **首尾取值相同**，配合 ``Meta.Loop`` 才能无缝循环（各次谐波周期取整数圈）。
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

# 每条曲线用哪组谐波（权重, 圈数）。圈数必须是整数，否则循环处会跳变。
HARMONIC_SETS = (
    ((1.0, 1), (0.35, 2)),
    ((1.0, 1), (0.25, 3)),
    ((1.0, 2), (0.30, 5)),
    ((1.0, 1), (0.40, 4)),
)


class Motion3Builder:
    """按参数真实键形区间生成 idle 曲线并写出 motion3.json。"""

    def __init__(self, fps: float = 30.0, duration: float = 6.0) -> None:
        if fps <= 0 or duration <= 0:
            raise ValueError("fps 与 duration 必须为正")
        self.fps = float(fps)
        self.duration = float(duration)

    # ------------------------------------------------------------------
    # 曲线
    # ------------------------------------------------------------------

    def sample_times(self) -> List[float]:
        steps = int(round(self.duration * self.fps))
        return [round(i / self.fps, 6) for i in range(steps + 1)]

    def wave_at(self, time: float, harmonics: Sequence[tuple]) -> float:
        """[-1, 1] 的归一化摆动；谐波权重之和归一到 1。"""
        total_weight = sum(weight for weight, _cycles in harmonics)
        value = 0.0
        for weight, cycles in harmonics:
            value += weight * math.sin(2.0 * math.pi * cycles * (time / self.duration))
        return value / total_weight

    def build_curve(self, keys: Sequence[float],
                    harmonics: Sequence[tuple]) -> List[float]:
        """键形区间上的线性 Segments 数组 ``[t0, v0, 0, t, v, 0, t, v, ...]``。

        段编码按官方 Haru 样例实测对账（6 个官方文件与 Meta 逐项吻合）：
        前两个浮点是初始点（无前缀），其后**每段**先写段类型再写该段的点——
        0=线性(1 点)、1=贝塞尔(3 点：两控制点 + 终点)、2=阶跃(1 点)。
        生成器只用线性段，所以是 ``0, t, v`` 三元组重复。
        """
        low, high = float(min(keys)), float(max(keys))
        if not high > low:
            raise ValueError(f"键形区间退化（{low}..{high}），无法生成曲线")
        default = self.rest_value(keys)
        times = self.sample_times()
        waves = [self.wave_at(t, harmonics) for t in times]
        # 多次谐波叠加后峰值不再是 1，按自身峰值归一，曲线才会真正扫到
        # 键形的两端（否则动作只用了行程的一部分，却看不出来）。
        peak = max(abs(w) for w in waves)
        if peak <= 0.0:
            raise ValueError("谐波叠加后恒为 0，无法生成曲线")
        segments: List[float] = []
        for time, wave in zip(times, waves):
            wave /= peak
            # 正负半周分别按到 max / min 的距离缩放：非对称参数（如 0..1 的
            # 嘴部开合）才不会一半行程被浪费或越界。
            value = (default + (high - default) * wave if wave >= 0.0
                     else default + (default - low) * wave)
            if segments:
                segments.append(0)            # 段类型：线性
            segments.append(round(time, 6))
            segments.append(round(value, 6))
        return segments

    @staticmethod
    def rest_value(keys: Sequence[float]) -> float:
        """静止姿态 = 最接近 0 的那个键值（管线按 min/default/max 生成键形）。"""
        return float(min(keys, key=lambda k: abs(float(k))))

    # ------------------------------------------------------------------
    # 组装
    # ------------------------------------------------------------------

    def build_idle(self, drivable: Dict[str, Sequence[float]]) -> Optional[dict]:
        """drivable 为空时返回 None —— 没有可动的东西就不该产出动画文件。"""
        curves: List[Dict[str, Any]] = []
        for index, parameter_id in enumerate(sorted(drivable)):
            keys = [float(k) for k in drivable[parameter_id]]
            if len(keys) < 2:
                continue      # 单键形没有可插值的行程
            if max(keys) == min(keys):
                continue      # 键形全等 = 这个参数形变不出位移，排进曲线只会是噪声
            curves.append({
                "Target": "Parameter",
                "Id": parameter_id,
                "Segments": self.build_curve(
                    keys, HARMONIC_SETS[index % len(HARMONIC_SETS)]),
            })
        if not curves:
            return None

        # Segments 布局固定为 [t0,v0] + N 个 (0,t,v) 线性段：
        # 点数 = 段数 + 1（初始点），与官方 Meta 的计数语义一致。
        segment_count = sum((len(curve["Segments"]) - 2) // 3
                            for curve in curves)
        point_count = sum((len(curve["Segments"]) - 2) // 3 + 1
                          for curve in curves)
        # Meta 的字段集与计数语义按官方 Haru 样例核实
        # （tools/probe_motion3_meta_semantics.py）。漏掉 TotalPointCount 会让
        # 内核在 LoadModelJson 里直接段错误 —— 它按这个数分配缓冲。
        return {
            "Version": 3,
            "Meta": {
                "Duration": round(self.duration, 6),
                "Fps": self.fps,
                "Loop": True,
                "AreBeziersRestricted": True,
                "CurveCount": len(curves),
                "TotalSegmentCount": segment_count,
                "TotalPointCount": point_count,
                "UserDataCount": 0,
                "TotalUserDataSize": 0,
            },
            "Curves": curves,
        }

    # ------------------------------------------------------------------
    # 落盘
    # ------------------------------------------------------------------

    def export_to_directory(self, output_dir: str, character_name: str,
                            motions: Dict[str, dict]) -> Dict[str, List[dict]]:
        """写出 motion 文件，返回可直接塞进 FileReferences.Motions 的清单。

        必须按 Cubism Editor 的方式**带缩进**写，不能压成单行：实测
        （tools/bisect_motion_encoding.py + tools/diag_extra_motion.py）同一份
        内容 —— tab 缩进与 2 空格缩进能被 live2d-py 0.7.0.4 的加载路径接受并
        真的驱动参数；紧凑单行（无论逗号后有没有空格、无论每个元素是否换行）
        一律 `Load extra motion failed`，参数一动不动。官方 Haru 的 motion 文件
        本身就是 tab 缩进的。机制未证实，但改的是它的二进制，产物侧只能照官方
        写法来。
        """
        out = Path(output_dir) / "motions"
        out.mkdir(parents=True, exist_ok=True)
        manifest: Dict[str, List[dict]] = {}
        for group, document in motions.items():
            filename = f"{character_name}_{group}_00.motion3.json"
            (out / filename).write_text(
                json.dumps(document, ensure_ascii=False, indent="\t"),
                encoding="utf-8")
            manifest.setdefault(group, []).append({
                "File": f"motions/{filename}",
                # 淡入淡出用 Editor 默认的 0.5 秒，与官方 Haru 的写法一致
                "FadeInTime": 0.5,
                "FadeOutTime": 0.5,
            })
        return manifest
