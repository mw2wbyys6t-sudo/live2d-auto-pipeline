"""FOSSA 许可证载体核对脚本的可验证内核。

为什么要有它：FOSSA 报的是「issue」，不是「我们违规了」。后台要逐条 ignore
之前，必须先知道每条 SPDX ID 在依赖里**到底有没有载体**、载体是运行时还是
文档/测试夹具。这里钉住判定规则，避免以后又变成一次性的人肉结论。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import fossa_license_carrier_check as carrier_check  # noqa: E402
from scripts.fossa_license_carrier_check import (  # noqa: E402
    Carrier,
    DistributionFinding,
    canonical_spdx,
    classify_path,
    find_carriers,
    requirement_names,
    verdict_for,
)

GPL3_TEXT = """                    GNU GENERAL PUBLIC LICENSE
                       Version 3, 29 June 2007

 Everyone is permitted to copy and distribute verbatim copies
"""


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_classify_path_separates_runtime_docs_and_tests():
    assert classify_path("pkg/core/module.py") == "runtime"
    assert classify_path("pkg/docs/generated/LGPL.txt") == "docs"
    assert classify_path("pkg/Tests/fonts/FreeMono.ttf") == "tests"
    assert classify_path("pkg/tests/data/case.json") == "tests"


def test_classify_path_treats_dist_info_licenses_as_notice():
    """``*.dist-info/licenses/`` 是随包分发的许可告知文件：不是被 import 的代码。"""
    assert classify_path("pkg-1.0.dist-info/licenses/LICENSE.txt") == "notice"
    assert classify_path("pkg-1.0.dist-info/METADATA") == "metadata"


def test_verdict_prefers_runtime_carrier_over_notice():
    finding = DistributionFinding(
        name="demo", version="1.0",
        carriers=[
            Carrier(package="demo", spdx_id="GPL-3.0", path="a", kind="runtime"),
            Carrier(package="demo", spdx_id="GPL-3.0", path="b", kind="notice"),
        ])
    assert verdict_for(finding, "GPL-3.0") == "runtime"


def test_verdict_falls_back_to_notice_when_only_notice_carrier_exists():
    finding = DistributionFinding(
        name="demo", version="1.0",
        carriers=[
            Carrier(package="demo", spdx_id="GPL-3.0", path="b", kind="notice"),
        ])
    assert verdict_for(finding, "GPL-3.0") == "notice"


def test_canonical_spdx_maps_fossa_variants():
    assert canonical_spdx("GPL-3.0-with-GCC-exception") == "GPL-3.0"
    assert canonical_spdx("LGPL-2.1-later") == "LGPL-2.1"
    assert canonical_spdx("apache-2.0 WITH lm-exception") == \
        "Apache-2.0 WITH LLVM-exception"
    assert canonical_spdx("Bitstreamream") == "Bitstream Vera"


def _patch_collect(monkeypatch, findings):
    monkeypatch.setattr(carrier_check, "collect", lambda _requirements: findings)


def test_gate_does_not_fail_when_an_id_has_no_carrier(monkeypatch, tmp_path):
    """「无载体」= 扫描器误判，是**结论**，不该让 CI 变红。"""
    _patch_collect(monkeypatch, [DistributionFinding(name="demo", version="1.0")])
    code = carrier_check.main(["--expect", "demo:GPL-1.0",
                               "--fail-on", "runtime",
                               "--requirements", str(tmp_path / "r.txt")])
    assert code == 0


def test_gate_fails_when_a_previously_misjudged_id_gains_a_runtime_carrier(
        monkeypatch, tmp_path):
    _patch_collect(monkeypatch, [DistributionFinding(
        name="demo", version="1.0",
        carriers=[Carrier(package="demo", spdx_id="GPL-1.0", path="a",
                          kind="runtime")])])
    code = carrier_check.main(["--expect", "demo:GPL-1.0",
                               "--fail-on", "runtime",
                               "--requirements", str(tmp_path / "r.txt")])
    assert code == 1


def test_gate_fails_on_notice_when_asked(monkeypatch, tmp_path):
    _patch_collect(monkeypatch, [DistributionFinding(
        name="demo", version="1.0",
        carriers=[Carrier(package="demo", spdx_id="GPL-3.0", path="a",
                          kind="notice")])])
    assert carrier_check.main(["--expect", "demo:GPL-3.0",
                                "--fail-on", "notice",
                                "--requirements", str(tmp_path / "r.txt")]) == 1
    assert carrier_check.main(["--expect", "demo:GPL-3.0",
                                "--fail-on", "runtime",
                                "--requirements", str(tmp_path / "r.txt")]) == 0


def test_find_carriers_locates_license_text_and_classifies_it(tmp_path):
    _write(tmp_path / "pkg" / "core.py", "# runtime code\n")
    _write(tmp_path / "pkg" / "docs" / "COPYING.txt", GPL3_TEXT)
    _write(tmp_path / "pkg" / "Tests" / "fonts" / "LICENSE.txt", GPL3_TEXT)

    carriers = find_carriers("demo", tmp_path, _templates())

    assert len(carriers) == 2
    kinds = {item.path: item.kind for item in carriers}
    assert kinds["pkg/docs/COPYING.txt"] == "docs"
    assert kinds["pkg/Tests/fonts/LICENSE.txt"] == "tests"


def test_verdict_reports_runtime_carrier_as_runtime(tmp_path):
    _write(tmp_path / "pkg" / "COPYING.txt", GPL3_TEXT)
    carriers = find_carriers("demo", tmp_path, _templates())
    finding = DistributionFinding(name="demo", version="1.0",
                                  metadata_licenses=["BSD-3-Clause"],
                                  carriers=carriers)

    assert verdict_for(finding, "GPL-3.0") == "runtime"


def test_verdict_reports_metadata_only_when_no_carrier_exists():
    finding = DistributionFinding(name="demo", version="1.0",
                                  metadata_licenses=["LGPL-2.1-only"])

    assert verdict_for(finding, "LGPL-2.1") == "metadata"
    assert verdict_for(finding, "GPL-1.0") == "none"


def test_requirement_names_strips_markers_and_extras(tmp_path):
    path = tmp_path / "requirements.txt"
    path.write_text(
        "# comment\n"
        "Pillow>=13.0.0\n"
        "pygame-ce>=2.5.8; python_version >= \"3.14\"\n"
        "requests>=2.34.2\n",
        encoding="utf-8")

    assert requirement_names(path) == ["Pillow", "pygame-ce", "requests"]


def _templates():
    from scripts.fossa_license_carrier_check import TEMPLATES
    return TEMPLATES
