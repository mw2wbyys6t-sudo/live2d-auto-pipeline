"""motion3.json 生成器：只给真正有键形驱动的参数排曲线。

三个刻意的取舍：

* **动画范围不越出键形区间**。曲线在 ``drivable_parameters()`` 给出的首末键值
  之内摆动，而不是参数声明的 Min/Max —— 超出已编译键形的区间内核只能外推，
  那正是「文件看着对、画面乱掉」的来源。
* **出厂 idle 只走一部分行程**。全行程（转头 ±30°、眼睛每秒全闭一次）是扫掠
  诊断的形态；本机实测播放时读起来就是抽搐。默认按参数语义取行程的一小段
  （见 ``_IDLE_AMPLITUDE_PREFIXES``），需要扫满时显式 ``full_range=True``。
* **密集线性采样，不用贝塞尔**。这样「运行时在 t 时刻应当取到的值」可以直接由
  Segments 线性插值得到，验收测试就能逐帧比对文件与内核观测值，而不是只判断
  「画面好像动了」。观感需要贝塞尔时再升级，判据不变。
* **首尾取值相同**，配合 ``Meta.Loop`` 才能无缝循环（各次谐波周期取整数圈）。
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

# 每条曲线用哪组谐波（权重, 圈数）。圈数必须是整数，否则循环处会跳变。
HARMONIC_SETS = (
    ((1.0, 1), (0.35, 2)),
    ((1.0, 1), (0.25, 3)),
    ((1.0, 2), (0.30, 5)),
    ((1.0, 1), (0.40, 4)),
)

# 出厂 idle 的幅度：占「静止值 → 该侧键形端」行程的比例，按参数语义定。
# 依据是播放观感：全行程时头部每 6 秒甩到 ±30°、眼睛每秒完全闭合一次，
# 实测看下来是抽搐而不是待机。前缀按**最长优先**匹配，同时决定相位与波形：
# 同一语义组的参数共用一条曲线（左右眼要一起眨、左右眉要一起动），组间再错开。
_IDLE_AMPLITUDE_PREFIXES = (
    ("ParamEyeBall", 0.25),   # 眼球游移，幅度可比眼睑大
    ("ParamBreath", 0.60),    # 呼吸要看得出来，接近全行程
    ("ParamTears", 0.05),     # 眼泪只是「有点湿」，不该满格
    ("ParamCheek", 0.10),
    ("ParamAngle", 0.18),     # ±30° 的行程只走 ±5.4°
    ("ParamBodyAngle", 0.20),
    ("ParamMouth", 0.15),
    ("ParamBrow", 0.15),
    ("ParamEye", 0.20),       # 眼睑：0.8~1.0，留出眨眼的余量
    ("ParamHair", 0.05),      # 头发主摆由物理驱动，这里只给一点偏移
)
_DEFAULT_IDLE_AMPLITUDE = 0.20
# 相位错开，避免所有参数同相（实测同相时呼吸与嘴部曲线逐值相同，像在喘气）。
# 黄金分割比的小数部分：相邻组相位差异最大、且不落在 2π 的有理倍数上。
_IDLE_PHASE_STEP = 0.6180339887498949


def idle_profile(parameter_id: str) -> tuple:
    """该参数在出厂 idle 里的 (组序号, 幅度)。

    组序号决定相位与谐波：组内完全同步（左右眼/左右眉），组间彼此错开。
    """
    for index, (prefix, amplitude) in enumerate(_IDLE_AMPLITUDE_PREFIXES):
        if parameter_id.startswith(prefix):
            return index, amplitude
    return len(_IDLE_AMPLITUDE_PREFIXES), _DEFAULT_IDLE_AMPLITUDE


def idle_amplitude(parameter_id: str) -> float:
    """该参数在出厂 idle 里走行程的几成。"""
    return idle_profile(parameter_id)[1]



# JSON 数值位置的指数写法（``5.3e-05``）。只匹配值 token：字符串里的文字
# 前面不会紧挨着 ``[ , :`` 或空白。
_EXPONENT_NUMBER = re.compile(
    r'(?<=[\s\[,:])-?\d+(?:\.\d+)?[eE][+-]?\d+(?=[\s,\]])')


def _expand_exponent(match: "re.Match[str]") -> str:
    text = format(float(match.group(0)), ".9f").rstrip("0")
    return text + "0" if text.endswith(".") else text


def dumps_motion(document: dict) -> str:
    """motion3.json 文本：tab 缩进，数值一律写成十进制。

    两条都是实测出来的硬约束，不是风格：紧凑单行会被加载路径直接拒绝；
    而官方内核的 JSON 解析器不认 ``5.3e-05`` 这类指数写法 —— 2026-11-01 实测，
    同一份曲线里只有 3 处指数数值，内核就报 ``Json parse error @line`` 并拒绝
    整份文件，参数一动不动（``tools/diag_extra_motion.py`` 的同一现象）。
    Python 的 ``json.dumps`` 对 <1e-4 的浮点默认输出指数形式，而待机曲线幅度收窄
    后必然出现这种小值，所以在序列化层统一展开。
    """
    return _EXPONENT_NUMBER.sub(
        _expand_exponent,
        json.dumps(document, ensure_ascii=False, indent="\t"))


class Motion3Builder:
    """按参数真实键形区间生成 idle 曲线并写出 motion3.json。"""

    def __init__(self, fps: float = 30.0, duration: float = 6.0,
                 full_range: bool = False) -> None:
        if fps <= 0 or duration <= 0:
            raise ValueError("fps 与 duration 必须为正")
        self.fps = float(fps)
        self.duration = float(duration)
        # 扫掠诊断（内核验收、tools）走满行程；出厂 idle 保持常态幅度。
        self.full_range = bool(full_range)

    # ------------------------------------------------------------------
    # 曲线
    # ------------------------------------------------------------------

    def sample_times(self) -> List[float]:
        steps = int(round(self.duration * self.fps))
        return [round(i / self.fps, 6) for i in range(steps + 1)]

    def wave_at(self, time: float, harmonics: Sequence[tuple],
                phase: float = 0.0) -> float:
        """[-1, 1] 的归一化摆动；谐波权重之和归一到 1。

        ``phase`` 以「圈」为单位叠加到各次谐波上——正弦周期整数圈，所以
        相位偏移不影响首尾相等（无缝循环）。
        """
        total_weight = sum(weight for weight, _cycles in harmonics)
        value = 0.0
        for weight, cycles in harmonics:
            value += weight * math.sin(2.0 * math.pi * (
                cycles * (time / self.duration) + phase))
        return value / total_weight

    def build_curve(self, keys: Sequence[float],
                    harmonics: Sequence[tuple],
                    amplitude: float = 1.0,
                    phase: float = 0.0,
                    rest: Optional[float] = None) -> List[float]:
        """键形区间上的线性 Segments 数组 ``[t0, v0, 0, t, v, 0, t, v, ...]``。

        段编码按官方 Haru 样例实测对账（6 个官方文件与 Meta 逐项吻合）：
        前两个浮点是初始点（无前缀），其后**每段**先写段类型再写该段的点——
        0=线性(1 点)、1=贝塞尔(3 点：两控制点 + 终点)、2=阶跃(1 点)。
        生成器只用线性段，所以是 ``0, t, v`` 三元组重复。

        ``rest`` 是静止姿态的取值。必须来自参数声明而不是「离 0 最近的键」：
        眼睑的默认值是 1.0（睁开），按离 0 最近会取到 0.0，待机时眼睛就是闭的。
        """
        low, high = float(min(keys)), float(max(keys))
        if not high > low:
            raise ValueError(f"键形区间退化（{low}..{high}），无法生成曲线")
        default = (float(rest) if rest is not None
                   and low <= float(rest) <= high else self.rest_value(keys))
        times = self.sample_times()
        waves = [self.wave_at(t, harmonics, phase) for t in times]
        # 多次谐波叠加后峰值不再是 1，按自身峰值归一，再乘幅度：幅度 1 时
        # 曲线正好扫到键形两端，幅度小于 1 时按同一比例收窄。
        peak = max(abs(w) for w in waves)
        if peak <= 0.0:
            raise ValueError("谐波叠加后恒为 0，无法生成曲线")
        segments: List[float] = []
        for time, wave in zip(times, waves):
            wave = wave / peak * amplitude
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

    def build_idle(self, drivable: Dict[str, Sequence[float]],
                   defaults: Optional[Dict[str, float]] = None
                   ) -> Optional[dict]:
        """drivable 为空时返回 None —— 没有可动的东西就不该产出动画文件。

        ``defaults`` 是参数声明里的默认值（模型静止姿态）。缺省时才退回
        「离 0 最近的键」，那只对以 0 为静止值的参数成立。
        """
        curves: List[Dict[str, Any]] = []
        for parameter_id in sorted(drivable):
            keys = [float(k) for k in drivable[parameter_id]]
            if len(keys) < 2:
                continue      # 单键形没有可插值的行程
            if max(keys) == min(keys):
                continue      # 键形全等 = 这个参数形变不出位移，排进曲线只会是噪声
            group, amplitude = idle_profile(parameter_id)
            if self.full_range:
                # 扫掠诊断只关心「打满键形两端」，因此统一用同一组无相位偏移的
                # 谐波：相位一错开，采样网格就不再落在极值点上，量到的行程会比
                # 键形区间短一截，验收判据也随之变松（实测 −29.5 vs −30.0）。
                group, amplitude = 0, 1.0
            curves.append({
                "Target": "Parameter",
                "Id": parameter_id,
                "Segments": self.build_curve(
                    keys,
                    HARMONIC_SETS[group % len(HARMONIC_SETS)],
                    amplitude=1.0 if self.full_range else amplitude,
                    phase=(group * _IDLE_PHASE_STEP) % 1.0,
                    rest=(defaults or {}).get(parameter_id),
                ),
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
            (out / filename).write_text(dumps_motion(document),
                                        encoding="utf-8")
            manifest.setdefault(group, []).append({
                "File": f"motions/{filename}",
                # 淡入淡出用 Editor 默认的 0.5 秒，与官方 Haru 的写法一致
                "FadeInTime": 0.5,
                "FadeOutTime": 0.5,
            })
        return manifest
