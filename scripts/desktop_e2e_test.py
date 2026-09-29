#!/usr/bin/env python3
"""桌面版全链路真实测试（对 http://127.0.0.1:8095 反复执行）。"""
import json
import io
import os
import sys
import time

import requests

BASE = "http://127.0.0.1:8095"
PASS, FAIL = 0, 0
FAILURES = []


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  OK  {name}")
    else:
        FAIL += 1
        FAILURES.append(f"{name}: {detail}")
        print(f"  FAIL {name}  {detail}")


def fig_png(w=256, h=256, fill=(240, 190, 160, 255)):
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (w, h), (255, 255, 255, 255))
    d = ImageDraw.Draw(img)
    d.ellipse([int(w * 0.23), int(h * 0.05), int(w * 0.77), int(h * 0.61)], fill=fill)
    d.rectangle([int(w * 0.31), int(h * 0.66), int(w * 0.69), h], fill=(236, 72, 153, 255))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def run_round(round_no: int):
    print(f"\n========== 第 {round_no} 轮 ==========")

    # 1. 基础端点
    r = requests.get(f"{BASE}/api/health", timeout=10).json()
    check("health", r.get("success") is True)
    r = requests.get(f"{BASE}/api/status", timeout=30).json()
    svcs = {s["name"]: s["available"] for s in (r.get("data", {}).get("services") or [])}
    check("python_env 就绪", svcs.get("python_env") is True, str(svcs))
    r = requests.get(f"{BASE}/api/providers", timeout=60).json()
    check("providers 含 openai 注册", "openai" in (r["data"].get("registered") or []))

    # 2. 上传 → 分层（kmeans + semantic）→ PSD
    png = fig_png()
    r = requests.post(f"{BASE}/api/upload", files={"file": ("t1.png", png, "image/png")}, timeout=60).json()
    check("upload", r.get("success") is True)
    img_path = r["data"]["path"]
    check("upload path 在 uploads 内", "uploads" in img_path.replace("/", "\\"))

    seg_result = {}
    for method in ("kmeans", "semantic"):
        r = requests.post(f"{BASE}/api/segment", json={"image_path": img_path, "method": method}, timeout=900).json()
        ok = r.get("success") and r["data"]["psd_success"] and r["data"]["layer_count"] > 0
        check(f"segment({method}) + PSD", ok, str(r)[:200])
        seg_result = r["data"]

    # 3. 导出（官方内核验收）→ 最新记录
    r = requests.post(f"{BASE}/api/export/live2d", json={
        "character_id": "web_preview", "layers_dir": seg_result["layers_dir"]}, timeout=900).json()
    check("export runtime_ready", r.get("data", {}).get("runtime_ready") is True,
          str(r.get("data", {}).get("blocker")))
    r = requests.get(f"{BASE}/api/generations/latest", timeout=15).json()
    check("latest 记录含 model3_url", bool(r["data"].get("model3_url")))
    check("latest runtime_ready=true", r["data"].get("runtime_ready") is True)

    # 4. 模型列表
    r = requests.get(f"{BASE}/api/generations/models", timeout=15).json()
    check("models 列表非空", r["data"]["count"] >= 1)

    # 5. 语义命名 PSD 导入（部件识别）
    from psd_tools import PSDImage
    from PIL import Image, ImageDraw
    W, H = 256, 384
    psd = PSDImage.new(mode="RGBA", size=(W, H), color=(0, 0, 0, 0))
    for name, fn in [
        ("hair_back_01", lambda d: d.ellipse([60, 20, 196, 200], fill=(60, 40, 30, 255))),
        ("face_base", lambda d: d.ellipse([70, 60, 186, 176], fill=(240, 190, 160, 255))),
        ("eye_l_white", lambda d: d.ellipse([95, 100, 122, 122], fill=(255, 255, 255, 255))),
        ("eye_r_white", lambda d: d.ellipse([134, 100, 161, 122], fill=(255, 255, 255, 255))),
        ("mouth_a", lambda d: d.ellipse([116, 148, 140, 162], fill=(170, 60, 60, 255))),
    ]:
        layer_img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        fn(ImageDraw.Draw(layer_img))
        psd.create_pixel_layer(name=name, image=layer_img)
    psd_bytes = io.BytesIO()
    psd.save(psd_bytes)
    r = requests.post(f"{BASE}/api/import/psd",
                      files={"file": ("semantic.psd", psd_bytes.getvalue())}, timeout=900).json()
    layers = r.get("data", {}).get("layers") or []
    check("psd 导入 5 层", r.get("success") and r["data"]["layer_count"] == 5, str(r)[:200])
    parts = {l["name"]: l.get("part") for l in layers}
    check("部件识别 hair/eye/mouth",
          parts.get("hair_back_01") == "hair_back" and parts.get("eye_l_white") == "eye_white"
          and parts.get("mouth_a") == "mouth", str(parts))
    check("左右配对", {l["name"]: l.get("side") for l in layers}.get("eye_l_white") == "left")

    # 6. 多 PNG 导入（异尺寸规范化）→ 导出
    files = [("files", ("a.png", fig_png(128, 128), "image/png")),
             ("files", ("b.png", fig_png(300, 200), "image/png"))]
    r = requests.post(f"{BASE}/api/import/pngs", files=files, timeout=120).json()
    check("png 导入 2 层", r.get("success") and r["data"]["layer_count"] == 2)
    r = requests.post(f"{BASE}/api/export/live2d", json={
        "character_id": "web_preview", "layers_dir": r["data"]["layers_dir"]}, timeout=900).json()
    check("异尺寸规范化后可导出", r.get("data", {}).get("runtime_ready") is True,
          str(r.get("data", {}).get("blocker")))

    # 7. 角色 CRUD + 参考图 + embedding
    r = requests.post(f"{BASE}/api/characters", json={"name": f"测试角色{round_no}"}, timeout=15).json()
    cid = r["data"]["character_id"]
    check("创建角色", bool(cid))
    r = requests.post(f"{BASE}/api/upload", files={"file": ("ref.png", fig_png(), "image/png")}, timeout=60).json()
    r = requests.post(f"{BASE}/api/characters/{cid}/references",
                      json={"image_path": r["data"]["path"], "view": "front"}, timeout=300).json()
    check("参考图登记", r.get("success") is True)
    r = requests.post(f"{BASE}/api/characters/{cid}/embedding", json={}, timeout=600).json()
    check("embedding 重算", r.get("data", {}).get("has_embedding") is True, str(r)[:150])
    r = requests.put(f"{BASE}/api/characters/{cid}", json={"name": f"改名{round_no}"}, timeout=15).json()
    check("角色改名", r["data"]["name"] == f"改名{round_no}")
    r = requests.delete(f"{BASE}/api/characters/{cid}", timeout=15)
    check("删除角色", r.status_code == 200)

    # 8. 生成（离线 → 本地兜底占位图，链路不断）
    r = requests.post(f"{BASE}/api/generate/character", json={
        "prompt": "a cheerful anime girl with silver hair",
        "width": 512, "height": 512, "export_live2d": True}, timeout=900).json()
    gen_ok = r.get("success") and (r["data"].get("layers_dir") or r["data"].get("steps"))
    check("generate/character 全链路（含兜底）", bool(gen_ok), str(r)[:200])

    # 9. 边界与安全
    r = requests.post(f"{BASE}/api/upload", files={"file": ("evil.exe", b"MZ...", "application/x-msdownload")}, timeout=15)
    check("exe 上传被拒", r.status_code == 400)
    r = requests.post(f"{BASE}/api/segment", json={"image_path": "../../etc/passwd"}, timeout=30)
    check("路径穿越被拒", r.status_code in (400, 500))
    r = requests.get(f"{BASE}/output/..%2F..%2Fgo.mod", timeout=15)
    check("output 目录穿越被拒", r.status_code in (400, 403, 404))
    r = requests.get(f"{BASE}/api/characters/../../go.mod", timeout=15)
    check("API 目录穿越不 200", r.status_code != 200)
    r = requests.post(f"{BASE}/api/chat", json={"message": "hi"}, timeout=60).json()
    check("chat 不假成功", "success" in r)


def main():
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    for i in range(1, rounds + 1):
        try:
            run_round(i)
        except Exception as e:
            check(f"第 {i} 轮异常", False, repr(e))
    print(f"\n========== 汇总: {PASS} passed, {FAIL} failed ==========")
    for f in FAILURES:
        print("  ✗", f)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
