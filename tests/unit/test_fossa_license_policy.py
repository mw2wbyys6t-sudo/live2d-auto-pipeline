"""FOSSA 许可分类与策略推导的回归测试。

背景：仓库侧新增了 ``scripts/fossa_license_inventory.py``，它枚举四个生态的
依赖树、逐个取 SPDX 标识，并按「Apache-2.0 分发场景」把许可分成
允许 / 需审阅 / 禁止三档。**这个分类直接决定 FOSSA 后台策略怎么填**，
填错会让合规结论失真（要么把强 copyleft 放过去，要么把合法依赖误报）。

所以这里锁住三件事：

1. 自由文本写法（"Apache 2.0" / "MIT License"）必须能归一到 SPDX 标识；
2. SPDX 表达式的 AND / OR 语义必须正确 —— OR 分支是**可选**的，
   只有 AND 才是真约束（这正是早期实现算错的地方）；
3. 输出必须是 SPDX 规范大小写，因为要原样粘进 FOSSA 后台。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
_SCRIPT = PROJECT_ROOT / "scripts" / "fossa_license_inventory.py"


def _load():
    spec = importlib.util.spec_from_file_location("fossa_license_inventory", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


inv = _load()


class TestNormalizeSpdx:
    """自由文本 → SPDX 标识。"""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("MIT", "MIT"),
            ("mit", "MIT"),
            ("Apache 2.0", "Apache-2.0"),
            ("Apache License 2.0", "Apache-2.0"),
            ("Apache-2", "Apache-2.0"),
            ("MIT License", "MIT"),
            ("The MIT License", "MIT"),
            ("BSD License", "BSD"),
            ("BSD 3-Clause License", "BSD-3-Clause"),
            ("ZLIB License", "Zlib"),
            ("The Unlicense", "Unlicense"),
            ("CC0 1.0 Universal", "CC0-1.0"),
        ],
    )
    def test_free_text_maps_to_spdx(self, raw, expected):
        assert inv._normalize_spdx(raw) == expected

    def test_parenthetical_suffix_is_stripped(self):
        # "(LGPLv3)" 这类括注必须整组剥掉，不能留下半个括号。
        # 早期实现的剥离顺序反了，产出过
        # "GNU-LESSER-GENERAL-PUBLIC-LICENSE-V3-(LGPLV3" 这种残缺标识。
        assert inv._normalize_spdx("GNU Lesser General Public License v3 (LGPLv3)") == "LGPL-3.0"

    def test_exception_clause_is_preserved(self):
        # FOSSA 按标识匹配，"Apache-2.0 WITH LLVM-exception" 才是它认的写法。
        assert inv._normalize_spdx("Apache-2.0 WITH LLVM-exception") == "Apache-2.0 WITH LLVM-exception"


class TestClassify:
    """三档分类：允许 / 需审阅 / 禁止。"""

    @pytest.mark.parametrize(
        "raw,bucket",
        [
            ("MIT", "ALLOW"),
            ("Apache-2.0", "ALLOW"),
            ("BSD-3-Clause", "ALLOW"),
            ("Unicode-3.0", "ALLOW"),
            ("Zlib", "ALLOW"),
            ("IJG", "ALLOW"),
            ("LGPL", "REVIEW"),
            ("LGPL v2.1", "REVIEW"),
            ("LGPL-3.0-or-later", "REVIEW"),
            ("MPL-2.0", "REVIEW"),
            ("GPL-3.0", "DENY"),
            ("GPL-3.0-only", "DENY"),
            ("AGPL-3.0", "DENY"),
            ("SSPL-1.0", "DENY"),
        ],
    )
    def test_single_identifier(self, raw, bucket):
        assert inv.classify(raw) == bucket

    def test_or_takes_the_most_permissive_branch(self):
        # OR = 二选一。LGPL 是**可选**分支，选 MIT 即可 —— 不能因此判成需审阅。
        assert inv.classify("MIT OR Apache-2.0 OR LGPL-2.1-or-later") == "ALLOW"

    def test_and_takes_the_strictest_branch(self):
        # AND = 两条同时成立，LGPL 确实生效 —— 必须判成需审阅。
        assert inv.classify("Apache-2.0 AND LGPL-3.0-or-later") == "REVIEW"
        assert inv.classify("Apache-2.0 AND LGPL-3.0-or-later AND MIT") == "REVIEW"

    def test_slash_dual_license_is_treated_as_or(self):
        # crates.io 的 "MIT/Apache-2.0" 惯例是二选一，不是同时满足。
        assert inv.classify("MIT/Apache-2.0") == "ALLOW"
        assert inv.classify("BSD-3-Clause/MIT") == "ALLOW"

    def test_parenthesised_group(self):
        assert inv.classify("(MIT OR Apache-2.0) AND Unicode-3.0") == "ALLOW"

    def test_exception_clause_keeps_base_permissiveness(self):
        assert inv.classify("Apache-2.0 WITH LLVM-exception") == "ALLOW"

    def test_empty_is_unknown(self):
        assert inv.classify("") == inv.UNKNOWN
        assert inv.classify(None) == inv.UNKNOWN


class TestSpdxIds:
    """FOSSA 按**单个** SPDX 标识匹配，所以必须能拆开表达式。"""

    def test_expression_is_split(self):
        ids = inv._spdx_ids("MIT OR Apache-2.0")
        assert "MIT" in ids
        assert "Apache-2.0" in ids

    def test_identifiers_use_canonical_spdx_spelling(self):
        # 要原样粘进 FOSSA 后台，写成 "APACHE-2.0" 未必能命中。
        ids = inv._spdx_ids("Apache 2.0 OR MIT License")
        assert "Apache-2.0" in ids
        assert "MIT" in ids
        # 规范写法不是全大写（"MIT" 本身就是大写，但 "Apache-2.0" 不是）。
        # 用集合校验而不是 isupper() —— SPDX 里 "MIT" / "ISC" 本来就全大写。
        canonical = {x for x in (inv.ALLOW | inv.REVIEW | inv.DENY)}
        for ident in ids:
            assert ident in canonical, f"{ident!r} 不是规范 SPDX 写法"

    def test_no_residual_parenthesis(self):
        for ident in inv._spdx_ids("GNU Lesser General Public License v3 (LGPLv3)"):
            assert "(" not in ident and ")" not in ident


class TestPolicyBuckets:
    """策略推导：按单个标识独立定档，而不是按整个表达式。"""

    def test_each_identifier_is_classified_independently(self):
        rows = [
            {"license": "MIT OR Apache-2.0"},
            {"license": "Apache-2.0 AND LGPL-3.0-or-later"},
            {"license": "MPL-2.0"},
        ]
        # 收集三档（复刻 print_policy 的核心逻辑）
        allow, review, deny = [], [], []
        for r in rows:
            for ident in inv._spdx_ids(r["license"]):
                bucket = inv.classify(ident)
                target = {"ALLOW": allow, "REVIEW": review, "DENY": deny}.get(bucket)
                if target is not None and ident not in target:
                    target.append(ident)
        assert "MIT" in allow
        assert "Apache-2.0" in allow
        assert "LGPL-3.0-or-later" in review
        assert "MPL-2.0" in review
        # 关键：LGPL 绝不能混进「允许」档
        assert "LGPL-3.0-or-later" not in allow
