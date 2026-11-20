"""FOSSA 逐条复核表的结构闸门。

背景：``docs/fossa-agpl3-review.csv`` 曾经停在旧扫描（含 numpy:GPL-1.0 等
本轮已不存在的条目），42 行还是 ``CLASSIFY`` 占位 —— 表格在、结论不在，
下一个人只能重新推一遍。这个测试把「表必须与一次真实扫描一一对应」钉死：

- 恰好 64 行（当前 ``revisionScanId=129901049`` 的 licensing issue 数）；
- 不允许占位 action（CLASSIFY/TODO）；
- 每行都必须有依赖、许可、处置、理由与证据来源；
- ``count_in_group`` 必须等于该组的真实行数。
"""

import csv
from collections import Counter
from pathlib import Path

CSV_PATH = Path(__file__).resolve().parents[2] / "docs" / "fossa-agpl3-review.csv"

HEADER = ["seq", "group", "count_in_group", "issue_id", "dependency",
          "license", "action", "fossa_reason", "notes", "evidence"]

ALLOWED_ACTIONS = {
    "NOOP_POLICY_SHOULD_APPROVE",  # 项目自身许可，属策略绑定问题
    "IGNORE",                      # 无载体/不随产物分发，可按扫描器误判处理
    "APPROVE_PER_ITEM",            # 载体为告知/文档，逐条审阅后放行
}


def _rows():
    with CSV_PATH.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def test_review_table_matches_current_scan():
    rows = _rows()
    assert len(rows) == 64, f"应与当前扫描的 64 条 licensing issue 一一对应，实际 {len(rows)}"


def test_header_is_stable():
    with CSV_PATH.open(encoding="utf-8", newline="") as fh:
        assert next(csv.reader(fh)) == HEADER


def test_no_placeholder_actions_remain():
    rows = _rows()
    bad = [(r["seq"], r["action"]) for r in rows if r["action"] not in ALLOWED_ACTIONS]
    assert not bad, f"仍有未判定/非法 action: {bad}"


def test_every_row_has_verdict_and_evidence():
    rows = _rows()
    missing = [
        r["seq"] for r in rows
        if not r["license"].strip() or not r["fossa_reason"].strip()
        or not r["notes"].strip() or not r["evidence"].strip()
    ]
    assert not missing, f"这些行缺许可/理由/说明/证据: {missing}"


def test_sequence_is_contiguous():
    rows = _rows()
    assert [r["seq"] for r in rows] == [str(i) for i in range(1, len(rows) + 1)]


def test_group_counts_match_reality():
    rows = _rows()
    real = Counter(r["group"] for r in rows)
    for row in rows:
        declared = int(row["count_in_group"])
        assert declared == real[row["group"]], (
            f"组 {row['group']} 声明 {declared} 行，实际 {real[row['group']]} 行"
        )


def test_denied_group_is_only_cc_by_sa():
    """6 条 Denied 在本轮全是 CC-BY-SA，且全部落在 B 组。"""
    rows = _rows()
    denied_like = [r for r in rows if r["group"] == "B"]
    assert len(denied_like) == 6
    for row in denied_like:
        assert row["license"].startswith("CC-BY-SA"), row
        assert row["action"] == "IGNORE", row
