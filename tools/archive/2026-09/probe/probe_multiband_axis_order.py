#!/usr/bin/env python3
"""F-06 受控实验（通用版）：多 binding 带的 keyform 展平顺序。

假设 H：带的 binding 列表按 [内层 → 外层] 排列，即
        index(i_0..i_{n-1}) = Σ_j i_j · Π_{m<j} k_m
    其中 j 是**列表下标**，k_j 是该 binding 的键数（列表第一个 binding 步长 1）。

判定法（每个轴独立、决定性）：对成员网格做**单点变异**——只改 keyform 序号 s_j
的顶点位置（s_j = H 预测的"仅第 j 轴取第 1 键"的那个序号），再用官方内核在这些
设置下渲染：
    S_0 = 所有轴都取第 0 键                （应【不变】，天然对照）
    S_t = 仅第 t 轴取第 1 键               （应【只有 S_j 变】）
同一设置下，变异与未变异的渲染只差这一个网格的这一个关键形，因此像素差异只可能
来自变异本身。若"变的那个设置"正好是 S_j，则 H 在该轴上成立。

运行：.venv/Scripts/python.exe tools/probe_multiband_axis_order.py
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from moc3 import _core  # noqa: E402
from drivers.live2d_runtime.moc3_verify import (  # noqa: E402
    render_probe,
    verify_moc3_consistency,
)

HARU_DIR = ROOT / "Work" / "native-sample" / "Haru"
HARU = HARU_DIR / "Haru.moc3"
MANIFEST = HARU_DIR / "Haru.model3.json"
OUT = ROOT / "Work" / "multiband-axis"
CI = _core.CountIdx


def _owner_map(doc):
    p_begin = doc.get("parameter.keyform_binding_begin_indices")
    p_count = doc.get("parameter.keyform_binding_counts")
    kb = doc.get("keyform_binding.keys_begin_indices")
    kc = doc.get("keyform_binding.keys_counts")
    kv = doc.get("keys.values")
    owner = {}
    for p in range(doc.counts[CI.PARAMETERS]):
        for b in range(p_begin[p], p_begin[p] + p_count[p]):
            owner[b] = (p, list(kv[kb[b]: kb[b] + kc[b]]))
    return owner


def _bands(doc, want_n):
    bb = doc.get("keyform_binding_band.begin_indices")
    bc = doc.get("keyform_binding_band.counts")
    bi = doc.get("keyform_binding_index.indices")
    out = []
    for b in range(doc.counts[CI.KEYFORM_BINDING_BANDS]):
        if bc[b] == want_n:
            out.append((b, list(bi[bb[b]: bb[b] + bc[b]])))
    return out


def _render(manifest, params, tag):
    return render_probe(str(manifest), params, png=str(OUT / f"{tag}.png"))


def _sha_map(tag, manifest, settings):
    got = {}
    for name, params in settings:
        r = _render(manifest, params, f"{tag}_{name}")
        if not r.get("ok"):
            return None, f"{name} 渲染失败: {r.get('blocker')}"
        got[name] = r["pixels_sha256"]
    return got, None


def probe(doc0, b, binds, owner, mesh, payload):
    kf_count = doc0.get("art_mesh.keyform_counts")
    kf_begin = doc0.get("art_mesh.keyform_begin_indices")
    verts = doc0.get("art_mesh.position_index_counts")
    kf_pos_begin = doc0.get("art_mesh_keyform.keyform_position_begin_indices")
    parent = doc0.get("art_mesh.parent_deformer_indices")
    dtypes = doc0.get("deformer.types")

    pins = [owner[x] for x in binds]
    pids = [doc0.parameter_ids[p] for p, _ in pins]
    ks = [len(k) for _, k in pins]
    prod = 1
    for k in ks:
        prod *= k
    if kf_count[mesh] != prod:
        return None, f"mesh #{mesh} keyforms={kf_count[mesh]} != 乘积 {prod}"

    par = parent[mesh]
    under_warp = par >= 0 and dtypes[par] == 0
    delta = 0.35 if under_warp else 25.0

    def params_for(axis_key: int, only: int) -> dict:
        d = {}
        for j, (p, k) in enumerate(pins):
            d[pids[j]] = k[only if j == axis_key else 0]
        return d

    names = ["S0"] + [f"S{j+1}" for j in range(len(binds))]
    settings = [("S0", params_for(-1, 0))]
    for j in range(len(binds)):
        settings.append((f"S{j+1}", params_for(j, 1)))

    base_sha, err = _sha_map(f"b{b}_m{mesh}_unmut", MANIFEST, settings)
    if base_sha is None:
        return None, err

    strides = []
    acc = 1
    for k in ks:
        strides.append(acc)
        acc *= k

    lines = []
    ok_all = True
    for j, stride in enumerate(strides):
        if stride >= prod:
            ok_all = False
            lines.append(f"  轴{j}({pids[j]}): 预测序号 {stride} 越界（prod={prod}）")
            continue
        mut = _core.Moc3.from_file(str(HARU))
        xs = list(mut.get("keyform_position.xys"))
        begin = kf_pos_begin[kf_begin[mesh] + stride]
        for i in range(begin, begin + verts[mesh] * 2, 2):
            xs[i] += delta
        mut.set("keyform_position.xys", xs)
        mdir = OUT / f"b{b}_m{mesh}_axis{j}"
        if mdir.exists():
            shutil.rmtree(mdir)
        shutil.copytree(HARU_DIR, mdir)
        (mdir / "Haru.moc3").write_bytes(mut.to_bytes())
        cons = verify_moc3_consistency(str(mdir / "Haru.moc3"))
        m_sha, err = _sha_map(f"b{b}_m{mesh}_axis{j}",
                              mdir / "Haru.model3.json", settings)
        if m_sha is None:
            return None, err
        changed = [n for n in names if m_sha[n] != base_sha[n]]
        expect = [f"S{j+1}"]
        good = (changed == expect) and cons["ok"]
        ok_all = ok_all and good
        lines.append(
            f"  轴{j}={pids[j]} ks={ks} 预测stride={stride} 变异后变化的设置="
            f"{changed}（期望 {expect}）核一致={cons['ok']} -> "
            f"{'✅' if good else '❌'}")
    return ok_all, "\n".join(lines)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    doc0 = _core.Moc3.from_file(str(HARU))
    owner = _owner_map(doc0)
    band_idx = doc0.get("art_mesh.keyform_binding_band_indices")

    results = []
    for want in (2, 3):
        found = 0
        for b, binds in _bands(doc0, want):
            members = [m for m in range(doc0.counts[CI.ART_MESHES])
                       if band_idx[m] == b]
            for m in members:
                pins = [owner[x] for x in binds]
                pids = [doc0.parameter_ids[p] for p, _ in pins]
                ks = [len(k) for _, k in pins]
                prod = 1
                for k in ks:
                    prod *= k
                if doc0.get("art_mesh.keyform_counts")[m] != prod:
                    continue
                print(f"\n=== band {b} | binds={binds} | "
                      f"{list(zip(pids, ks))} | mesh #{m} keyforms={prod} ===")
                ok, detail = probe(doc0, b, binds, owner, m, want)
                if ok is None:
                    print(f"  跳过：{detail}")
                    continue
                print(detail)
                results.append((b, want, ok))
                found += 1
                break
            if found:
                break
        if not found:
            print(f"\n（未找到 n={want} 的可用候选）")

    print("\n== 汇总 ==")
    for b, want, ok in results:
        print(f"  n={want} band {b}: {'符合假设 H ✅' if ok else '不符合假设 H ❌'}")
    print(f"结论：{'假设 H 在全部已测带上成立' if results and all(r[2] for r in results) else '存在反例或未测到'}")
    return 0 if results and all(r[2] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
