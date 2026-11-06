"""产物级零变化必须有个**能跑的**家（Task 2 审查前移的 Important 3）。

审查员的 original 批评：「画面零变化」今天只由 `F:\\tmp` 里的临时脚本量过（重建
`.moc3` 逐字节 cmp + 官方 Core 静止 SHA），而仓库里的集成用例受
`LIVE2D_TEST_PIXELS=1`、`live2d-py`、`importorskip("moc3._core")` 三重门 ——
那 12 个 skip 就是这么来的。10880 字节的黄金夹具
（``tests/unit/fixtures/moc3_root_level_flat_warps_golden.b64``）只覆盖**编译器路径**，
覆盖不到「整条管线重导出来的歌手产物画面没变」。

三条判据各自钉一件事：

1. **官方 Core 静止帧**：这才是真门。Task 3 的原子性约束说清楚了 —— 变形器表第一次
   真的带上父链，产物字节**必然**变，`c062538d9ee4…` 那条字节相同从今天起不再成立
   也不该成立；从现在起正确的不变式是**画面不变**，谁把前者当门就会误判。
2. **`.moc3` 的 sha 与尺寸**：钉的是「Task 3 落地以后不再漂」的回归哨兵，
   换拓扑的那一笔提交里必须显式改掉这两个常数。
3. **修正后的离线尺子**（#72）：自检 1（默认参数重放==静止）< 1e-3px，且
   `art outside parent grid` 读 0 —— 后者是 Step B（父级矩形覆盖子孙）的**产物侧**
   契约：只有画留在父级网格之内，官方内核、自研播放器与这把尺子才读同一个数。

开：`LIVE2D_TEST_PRODUCT=1 python -m pytest tests/integration/test_moc3_product_invariant.py -q`
（重建整条产物 ~1 分钟 + 一次 Core 渲染）

前提是分层源在 `config.output_dir/live2d_exports/singer_demo/layers`（`.gitignore` 里的
本机产物，不在版本控制内）。缺了它这几条会 **skip**，而 skip 不等于绿：那三个钉值
只有在本机真量过的那一次里才算被验证过。
"""
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

requires_product = pytest.mark.skipif(
    os.environ.get("LIVE2D_TEST_PRODUCT") != "1",
    reason="重建整条歌手产物（~1 分钟）：设 LIVE2D_TEST_PRODUCT=1 开启")

#: 官方 Cubism Core 在默认参数下的静止帧判据（Task 0 起从未变过）。
REST_PIXELS_SHA256 = "7c10ce6c0ce4f11dde8d5d654f01876b67c7e6463a43e1fb329f0dac6300db54"
REST_OPAQUE_PIXELS = 69857

#: Task 3 落地（单一头部场 + 父级矩形覆盖子孙）以后的产物字节哨兵。
#: 改拓扑的那一笔提交必须连同这两个数一起改，并在提交信息里说清楚画面为什么没变。
ARTIFACT_SHA256 = "b2e7d36535542d0f27fad775870aac29527df4e805faa6dfe6cf2a239607b615"
ARTIFACT_BYTES = 359936

NAME = "trunk"


def _singer_layers() -> Path:
    from core.config import config
    return Path(config.output_dir) / "live2d_exports" / "singer_demo" / "layers"


def _rebuild(tmp_path: Path) -> dict:
    """把分层源复制到 tmp_path 再走一遍导出（**不**往真实产物目录里写）。"""
    import api_server as srv

    source = _singer_layers()
    if not source.is_dir():
        pytest.skip(f"没有歌手分层源：{source}")
    layers_dir = tmp_path / "layers"
    shutil.copytree(source, layers_dir)
    entries = []
    for i, path in enumerate(sorted(layers_dir.glob("*.png"))):
        entry = {"name": path.stem, "draw_index": i}
        if path.stem == "002_cheek":
            # 与真实歌手件同一份 layer_guide：腮红是淡入层，静止透明度 0
            entry["rest_opacity"] = 0.0
        entries.append(entry)
    (layers_dir / "layer_guide.json").write_text(
        json.dumps({"layers": entries}, ensure_ascii=False), encoding="utf-8")

    layers, opacity = srv._load_layers_for_export(str(layers_dir))
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    result = srv._build_live2d_model(layers, str(out_dir), NAME, opacity)
    return result


@requires_product
def test_singer_product_rest_picture_survives_the_deformation_trunk(tmp_path):
    """判据 1：官方 Core 的静止帧逐像素不变 —— 这是主干唯一的硬门。"""
    result = _rebuild(tmp_path)
    raw = Path(result["moc3"]["moc3_path"]).read_bytes()
    assert raw, "产物是空的"

    from drivers.live2d_runtime.moc3_verify import render_probe

    rest = render_probe(str(Path(result["model3_json"])))
    assert rest["ok"], rest.get("blocker")
    assert rest["pixels_sha256"] == REST_PIXELS_SHA256, (
        f"官方内核画出来的静止帧变了：{rest['pixels_sha256']}")
    assert rest["opaque_pixels"] == REST_OPAQUE_PIXELS


@requires_product
def test_singer_product_bytes_are_pinned_after_task_3(tmp_path):
    """判据 2：字节哨兵。**这条故意会随拓扑改动而失败**，那是提醒不是回归。

    字节门从 Task 3 起**不再是不变式**：变形器表第一次真的带上父链，之后 Task 4/5
    每动一次拓扑都会搬字节。它留在这里的唯一作用是**绊线** —— 红一次就逼读的人去
    判一次「画面到底变没变」，判据是同文件的
    ``test_singer_product_rest_picture_survives_the_deformation_trunk``（官方 Core
    静止像素 SHA），不是这条。
    """
    result = _rebuild(tmp_path)
    raw = Path(result["moc3"]["moc3_path"]).read_bytes()
    trip_wire = (
        "【绊线，不是正确性】产物字节和上次不一样了。字节门从 Task 3 起故意不再是"
        "不变式（父链第一次进产物，Task 4/5 每次动拓扑都会搬字节），所以这条红的"
        "正确反应既不是删掉它、也不是拿它当「画面变了」的证据：\n"
        "  1) 先跑同文件的判据 1（官方 Core 静止帧 SHA）—— 它绿就说明画面没变，"
        "只是有人搬了字节；\n"
        "  2) 再查是谁搬的：git log -p -- live2d_builder/exporter/ 找拓扑那一步；\n"
        "  3) 确认画面确实没变以后，把 ARTIFACT_SHA256 与 ARTIFACT_BYTES **连同**"
        "改掉，并在提交信息里写清是谁搬的字节、为什么画面没变。\n"
        "判据 1 也一起红 = 真的改了画面，那时才谈得上回归。")
    assert len(raw) == ARTIFACT_BYTES, (
        f"实测 {len(raw)} != 钉住 {ARTIFACT_BYTES}\n{trip_wire}")
    got_sha = hashlib.sha256(raw).hexdigest()
    assert got_sha == ARTIFACT_SHA256, (
        f"实测产物 sha {got_sha} != 钉住 {ARTIFACT_SHA256}（尺寸相同但内容搬了位，"
        f"多半是键形/表序的重排）\n{trip_wire}")


@requires_product
def test_singer_product_art_stays_inside_every_parent_grid(tmp_path):
    """判据 3：修正后的尺子读静止 == 静止，且没有任何画越出父级网格。

    这条是 Step A + Step B 合起来的产物侧结论：`7c10ce6c…` 说内核没变，
    这里说的是**我们的读数**也没变 —— 越界读数为 0 时才谈得上三方一致。
    曾经这一步在夹取版尺子上读出 27.183px 的假静止偏差（#72）。
    """
    import bindings_gap_probe as probe_mod

    result = _rebuild(tmp_path)
    doc = json.loads(Path(result["moc3"]["bindings"]).read_text(encoding="utf-8"))
    meshes = doc["meshes"]
    field = probe_mod.Field(doc, {})
    worst, where = probe_mod.selfcheck_rest(field, meshes)
    assert worst < 1e-3, f"自检 1 读 {worst:.6f}px（{where}）"
    outside = {m["id"]: field.art_outside_parent_grid(m)["levels"] for m in meshes}
    assert not any(outside.values()), outside
