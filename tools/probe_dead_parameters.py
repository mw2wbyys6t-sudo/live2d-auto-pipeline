"""死参数扫描：模型声明出来的每个参数，是不是真的让画面动起来。

怎么发现的：``head_xy`` 的门控漏了一项 → 头的 X/Y 形变退化成恒等 → 但**构建
照样报 valid、测试全绿、画面照常渲染**，只有把参数推到极值再逐像素比对才看得
出来。所以这里对**每一个**参数都做同一件事：静止帧 vs 极值帧，比 SHA。

输出里「没变」的参数分两类，要分开看：
  * 该参数对应的画在这份产物里根本不存在（如没有眉毛层时的 ParamBrowLY）
    —— 合法；
  * 画在、参数也声明了，但画面纹丝不动 —— **功能是死的**，要修。

用法：python tools/probe_dead_parameters.py [--model <model3.json>]
不带 --model 就用一份合成的全身层集现搭一个模型。
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from drivers.live2d_runtime.moc3_verify import render_probe


def _layers():
    """尽量齐全的层名，让作者表里的每条变形器都真的有成员。"""
    import numpy as np
    from PIL import Image

    def layer(box, color=(200, 120, 100, 255), w=300, h=420):
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        arr = np.array(img)
        x0, y0, x1, y1 = box
        arr[y0:y1, x0:x1] = color
        return Image.fromarray(arr, "RGBA")

    return {
        "face": layer((90, 40, 210, 200), (255, 220, 180, 255)),
        "nose": layer((140, 110, 160, 140)),
        "cheek": layer((100, 150, 130, 180)),
        "ear_L": layer((80, 90, 92, 120)),
        "ear_R": layer((208, 90, 220, 120)),
        "eyelid_left": layer((105, 85, 135, 95)),
        "eyelid_right": layer((165, 85, 195, 95)),
        "eyewhite_left": layer((108, 88, 132, 104)),
        "eyewhite_right": layer((168, 88, 192, 104)),
        "irides_left": layer((115, 92, 125, 102), (30, 30, 80, 255)),
        "irides_right": layer((175, 92, 185, 102), (30, 30, 80, 255)),
        "eyebrow_L": layer((100, 70, 140, 78), (60, 40, 30, 255)),
        "eyebrow_R": layer((160, 70, 200, 78), (60, 40, 30, 255)),
        "mouth": layer((135, 140, 165, 158)),
        "mouth_cavity": layer((140, 145, 160, 156), (120, 40, 50, 255)),
        "neck": layer((130, 190, 170, 215)),
        "collar": layer((120, 205, 180, 225)),
        "accessories": layer((225, 120, 245, 140), (200, 200, 60, 255)),
        "tear_left": layer((100, 150, 108, 160), (180, 220, 240, 255)),
        "hair_back": layer((70, 20, 230, 300), (80, 80, 200, 255)),
        "hair_front": layer((80, 30, 220, 160), (60, 60, 180, 255)),
        "clothes_top": layer((70, 210, 230, 390), (50, 150, 50, 255)),
        "waistband": layer((85, 370, 215, 390), (40, 40, 120, 255)),
        "arm_L": layer((230, 180, 280, 360)),
        "arm_R": layer((20, 180, 70, 360)),
        "leg_L": layer((95, 330, 140, 415), (90, 60, 120, 255)),
        "leg_R": layer((160, 330, 205, 415), (90, 60, 120, 255)),
    }


def _build(workdir: Path) -> Path:
    from live2d_builder.pipeline import Live2DBuilder

    Live2DBuilder(output_dir=str(workdir), character_name="dead").build(_layers())
    return next(workdir.glob("*.model3.json"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", default=None, help="现成的 model3.json；不给就现搭")
    args = ap.parse_args()

    with tempfile.TemporaryDirectory(prefix="dead_params_") as tmp:
        manifest = Path(args.model) if args.model else _build(Path(tmp))
        model = json.loads(manifest.read_text(encoding="utf-8"))
        params = model.get("Parameters") or []
        rest = render_probe(str(manifest), {})
        if not rest["ok"]:
            print(f"静止帧渲染失败: {rest.get('blocker')}")
            return 2
        dead = []
        alive = 0
        for spec in params:
            pid = str(spec.get("Id") or "")
            if not pid:
                continue
            lo = float(spec.get("Min", 0.0))
            hi = float(spec.get("Max", 0.0))
            value = hi if hi != lo else lo + 1.0
            probe = render_probe(str(manifest), {pid: value})
            if not probe["ok"]:
                print(f"  {pid:20s} 渲染失败：{probe.get('blocker')}")
                continue
            if probe["pixels_sha256"] == rest["pixels_sha256"]:
                dead.append(pid)
            else:
                alive += 1
        print(f"\n参数 {len(params)} 个：动了 {alive} 个，没动 {len(dead)} 个")
        if dead:
            print("没动的参数（该参数对应的画不在产物里就属正常，否则是死功能）：")
            for pid in dead:
                print(f"  {pid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
