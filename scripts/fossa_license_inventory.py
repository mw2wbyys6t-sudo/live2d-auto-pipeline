#!/usr/bin/env python3
"""依赖许可清单 + FOSSA Licensing 策略建议。

FOSSA 的「License Compliance」告警本质是：把依赖树里每个包的许可，与组织
的 Licensing 策略（允许/需审阅/禁止三档）逐条比对，不一致就记一条 issue。
仓库侧看不见那 65 条明细（逐条明细只有企业版 API 能取），但**许可本身是可
以自己枚举的** —— 本脚本把四个生态的依赖树解析出来，逐个取 SPDX 标识，按
Apache-2.0 分发场景分类，直接给出「策略里该允许什么」的清单。

用法：
    python scripts/fossa_license_inventory.py            # 人类可读报告
    python scripts/fossa_license_inventory.py --policy   # 策略建议（allow/deny）
    python scripts/fossa_license_inventory.py --json     # 机器可读
    python scripts/fossa_license_inventory.py --offline  # 只用本地元数据（npm 锁文件自带许可）

数据来源（都是权威源，不是扫描器的结论）：
- npm：``web/package-lock.json`` 的 ``packages[].license``（锁文件自带，无需联网）
- PyPI：https://pypi.org/pypi/<name>/json 的 license / license_expression / classifiers
- crates.io：https://crates.io/api/v1/crates/<name>
- Go：https://api.deps.dev/v3/systems/go/packages/<module>/versions/<version>

为什么分类标准是「按 Apache-2.0 分发」：本仓库自身是 Apache-2.0（ADR-001 Rev.4），
产出物对外分发，所以判断标准是「这个依赖的许可能否出现在 Apache-2.0 项目的
分发物里」，而不是「它是不是自由软件」。
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------- 许可分类

#: 可自由再分发、无需在文件级施加传染条件 —— 直接允许。
ALLOW = {
    "0BSD", "AFL-3.0", "Apache-1.1", "Apache-2.0", "Artistic-1.0", "BSD-2-Clause",
    "BSD-3-Clause", "BSL-1.0", "CC0-1.0", "CC-BY-4.0", "CC-BY-SA-4.0", "ISC",
    "MIT", "MIT-0", "MIT-CMU", "MS-PL", "NCSA", "OFL-1.1", "PostgreSQL", "PSF-2.0",
    "Python-2.0", "Ruby", "Unlicense", "UPL-1.0", "W3C", "X11", "Zlib", "ZPL-2.1",
    "Zend-2.0", "BSD-3-Clause-Clear", "BlueOak-1.0.0", "MIT-0",
    # 上游元数据的常见写法（PyPI 的 license 字段是自由文本，不是 SPDX id）
    "BSD", "BSD-3", "BSD-2",
    # Unicode License v3（ICU4X / unicode-ident 等）：宽松许可，可自由再分发
    "Unicode-3.0", "Unicode-DFS-2016",
    # IJG（Independent JPEG Group，mozjpeg 用的）：宽松许可
    "IJG",
    # CDLA-Permissive-2.0（webpki-root-certs）：宽松许可
    "CDLA-Permissive-2.0",
    # AGPL-3.0 系列：**本项目自身协议**（ADR-001 Rev.5）。AGPL 项目吸收 AGPL
    # 代码没有兼容问题；继续放在 DENY 会让项目自己被判违规。
    "AGPL-3.0", "AGPL-3.0-only", "AGPL-3.0-or-later",
}

#: 弱 copyleft / 条款型许可：以库形式（动态链接 / import）随**本项目**分发是
#: 兼容的，但要求人工确认一次 —— FOSSA 的默认策略通常把这档记成 issue。
#:
#: ``GPL-3.0`` 系列也归这一档（而不是 DENY）：本项目自 v0.10.10 起自身协议是
#: AGPL-3.0（ADR-001 Rev.5），而 AGPL-3.0 与 GPL-3.0 之间有 GPL-3.0 §13 的
#: 双向授权条款，二者可以合法组合。但它会把「整体须以 GPL-3.0 或 AGPL-3.0
#: 分发」的义务传导进来，与 AGPL 双轨（商业授权）需要逐条核对，所以是
#: 「需人工确认」而不是「自动允许」。
REVIEW = {
    "LGPL-2.0", "LGPL-2.1", "LGPL-2.0-only", "LGPL-2.0-or-later",
    "LGPL-2.1-only", "LGPL-2.1-or-later", "LGPL-3.0", "LGPL-3.0-only",
    "LGPL-3.0-or-later", "LGPLLR", "MPL-1.1", "MPL-2.0",
    "CDDL-1.0", "CDDL-1.1", "EPL-1.0", "EPL-2.0", "Artistic-2.0", "OSL-3.0",
    "QPL-1.0", "IPL-1.0", "Sleepycat", "EUPL-1.2", "APSL-2.0",
    "GPL-3.0", "GPL-3.0-only", "GPL-3.0-or-later",
}

#: 与 AGPL-3.0 不兼容 / 非商业条款 —— 必须挡掉。
#:
#: 注意 ``AGPL-3.0`` **不在**这里：它是本项目自身协议，挡掉等于判自己违规。
#: ``GPL-2.0-only`` 仍在 DENY：无 "or later" 时升不到 GPL-3.0，走不到 §13 互认。
DENY = {
    "GPL-2.0", "GPL-2.0-only", "GPL-2.0-or-later",
    "AGPL-1.0", "SSPL-1.0", "BUSL-1.1", "Commons Clause",
    "CC-BY-NC-4.0", "CC-BY-NC-SA-4.0", "JSON", "CC-BY-NC-3.0", "Elastic-2.0",
}

#: 许可文本里出现这些片段 → 归入 DENY（包元数据常写自由文本而非 SPDX id）。
#:
#: 从 ``agpl`` 改成了 ``agpl-?1``：AGPL-3.0 现在是本项目自身协议，不能再靠
#: 自由文本匹配把它打成禁止；AGPL-1.0 仍然不兼容，单独保留。
_DENY_PAT = re.compile(
    r"agpl-?1(?:\.0)?\b|gnu[\s-]*general[\s-]*public|\bgpl-?2\b|\bsspl|"
    r"server side public|business source|commons clause|non.?commercial|"
    r"no derivative|creative commons.*nc|proprietary|all rights reserved",
    re.I,
)

#: 自由文本里的 AGPL-3（项目自身协议）与 GPL-3（可经 §13 组合）。
#: 必须**排在** ``_DENY_PAT`` 之前判定：``gnu general public`` 会同时命中
#: GPL-3 与 GPL-2 的自由文本，不先认出 v3 就会把 GPL-3 误判成禁止。
#:
#: 分隔符用 ``[\s-]*``：``_normalize_spdx`` 会把 "GNU General Public License
#: v3.0" 归一成 "GNU-GENERAL-PUBLIC-LICENSE-V3.0"（空格变连字符），正则只写
#: 空格会完全匹配不到。
_AGPL3_PAT = re.compile(r"affero.*?3|agpl-?3", re.I)
_GPL3_PAT = re.compile(
    r"general[\s-]*public.*?(?:v|version[\s-]*)3|gpl-?3(?!\d)", re.I)
_REVIEW_PAT = re.compile(r"lesser general public|lgpl|mozilla public|mpl|cddl|"
                         r"eclipse public|artistic", re.I)

UNKNOWN = "UNKNOWN"

#: SPDX 标识大小写敏感，但上游元数据的写法五花八门（"BSD-3-Clause" /
#: "bsd-3-clause" / "MIT"）。比较时统一转大写 —— 否则混合大小写的标识
#: （BSD-3-Clause / MPL-2.0 / Zlib …）会全部被误判成「许可未知」。
_ALLOW_U = {x.upper() for x in ALLOW}
_REVIEW_U = {x.upper() for x in REVIEW}
_DENY_U = {x.upper() for x in DENY}


def _classify_one(ident: str) -> str:
    """把**单个** SPDX 标识（或自由文本）归到三档之一。

    自由文本（"Apache 2.0" / "MIT License"）必须先归一化成 SPDX 标识再比对
    —— 直接拿原串比对会因为空格与大小写全部落空。
    """
    norm = _normalize_spdx(ident)
    if not norm:
        return UNKNOWN
    # 带例外条款的 SPDX（"Apache-2.0 WITH LLVM-exception"）：例外不改变
    # 宽松度，按基础许可判。
    base = re.split(r"\s+WITH\s+", norm, flags=re.I)[0].strip()
    upper = base.upper()
    if upper in _ALLOW_U:
        return "ALLOW"
    if upper in _REVIEW_U:
        return "REVIEW"
    if upper in _DENY_U:
        return "DENY"
    if upper.startswith("APACHE-2.0"):
        return "ALLOW"
    # 自由文本要先认出 AGPL-3 / GPL-3 再走 DENY_PAT，否则
    # "GNU General Public License v3.0" 会被 "gnu general public" 打成禁止。
    if _AGPL3_PAT.search(norm):
        return "ALLOW"
    if _GPL3_PAT.search(norm):
        return "REVIEW"
    if _DENY_PAT.search(norm):
        return "DENY"
    if _REVIEW_PAT.search(norm):
        return "REVIEW"
    return UNKNOWN


#: 三档的严格程度排序（越大越严）。
_RANK = {"ALLOW": 0, "REVIEW": 1, "DENY": 2}

#: 上游元数据的自由文本写法 → SPDX id。只收「含义明确、不会歧义」的。
_SYNONYMS = {
    "APACHE 2.0": "Apache-2.0",
    "APACHE LICENSE 2.0": "Apache-2.0",
    "APACHE LICENSE, VERSION 2.0": "Apache-2.0",
    "APACHE SOFTWARE LICENSE": "Apache-2.0",
    "APACHE-2": "Apache-2.0",
    "MIT LICENSE": "MIT",
    "THE MIT LICENSE": "MIT",
    "MIT LICENSE (MIT)": "MIT",
    "BSD LICENSE": "BSD",
    "BSD-LICENSE": "BSD",
    "BSD 3-CLAUSE LICENSE": "BSD-3-Clause",
    "BSD 2-CLAUSE LICENSE": "BSD-2-Clause",
    "BSD-3-CLAUSE LICENSE": "BSD-3-Clause",
    "3-CLAUSE BSD LICENSE": "BSD-3-Clause",
    "LGPLV3": "LGPL-3.0",
    "LGPL V3": "LGPL-3.0",
    "LGPL V2.1": "LGPL-2.1",
    "LGPL-2.1-OR-LATER": "LGPL-2.1",
    "MOZILLA PUBLIC LICENSE 2.0 (MPL 2.0)": "MPL-2.0",
    "MOZILLA PUBLIC LICENSE 2.0": "MPL-2.0",
    "GNU LESSER GENERAL PUBLIC LICENSE V3": "LGPL-3.0",
    "GNU LESSER GENERAL PUBLIC LICENSE V2.1": "LGPL-2.1",
    "ISC LICENSE (ISCL)": "ISC",
    "PYTHON SOFTWARE FOUNDATION LICENSE": "PSF-2.0",
    "ZLIB LICENSE": "Zlib",
    "THE UNLICENSE": "Unlicense",
    "UNLICENSE": "Unlicense",
    "CC0-1.0": "CC0-1.0",
    "CC0 1.0 UNIVERSAL": "CC0-1.0",
}

#: SPDX 标识 → 规范大小写（"APACHE-2.0" → "Apache-2.0"）。FOSSA 后台的
#: 策略按 SPDX 标识匹配，写成全大写未必命中，所以输出必须用规范写法。
_CANON = {x.upper(): x for x in (ALLOW | REVIEW | DENY)}
_CANON.update({k: v for k, v in _SYNONYMS.items()})

#: SPDX 例外条款的规范写法（"LLVM-exception"）。
_EXCEPTIONS = {
    "LLVM-EXCEPTION": "LLVM-exception",
    "LLVM-EXCEPTION-2.0": "LLVM-exception-2.0",
    "CLASSPATH-EXCEPTION-2.0": "Classpath-exception-2.0",
    "GCC-EXCEPTION-3.1": "GCC-exception-3.1",
    "AUTOCONF-EXCEPTION-3.0": "Autoconf-exception-3.0",
    "BISON-EXCEPTION-2.2": "Bison-exception-2.2",
    "FONTSEC-EXCEPTION": "Font-exception",
    "OPENJDK-ASSEMBLY-EXCEPTION-1.0": "OpenJDK-assembly-exception-1.0",
}


def _normalize_spdx(text: str) -> str:
    """把自由文本的许可写法归一到**规范的 SPDX 标识**。

    PyPI 的 ``license`` 字段是自由文本（"Apache 2.0" / "MIT License" /
    "BSD 3-Clause License"），crates.io 多用 SPDX id。两者混在一起逐字比对，
    大量宽松许可会被误判成「未知」—— 报告数字就失真了。
    """
    upper = re.sub(r"\s+", " ", text.strip()).upper()
    # 先剥掉整个尾部括号组，再剥零散括号 —— 顺序反了会把 "(LGPLv3)" 剥成
    # "GNU LESSER GENERAL PUBLIC LICENSE V3 (LGPLV3"（左括号留下了）。
    upper = re.sub(r"\s*\([^)]*\)\s*$", "", upper).strip()
    upper = re.sub(r"^\(+|\)+$", "", upper).strip()
    if not upper:
        return ""
    if upper in _SYNONYMS:
        return _SYNONYMS[upper]
    # 例外条款（"Apache-2.0 WITH LLVM-exception"）单独处理：WITH 前后的
    # 空格是 SPDX 表达式的运算符，不能一并换成连字符。
    parts = re.split(r"\s+WITH\s+", upper)
    base = parts[0].strip()
    base = re.sub(r"\s+LICENSE$", "", base).strip()
    base = re.sub(r"\s+VERSION\s+[\d.]+$", "", base).strip()
    base = base.replace(" ", "-")
    base = _CANON.get(base, base)
    if len(parts) == 1:
        return base
    # 例外名保持 SPDX 规范写法（"LLVM-exception"，不是 "LLVM-EXCEPTION"）——
    # 这串文本要原样粘进 FOSSA 后台，写法不匹配就命不中。
    exception = _EXCEPTIONS.get(parts[1].strip().upper(), parts[1].strip())
    return f"{base} WITH {exception}"


def _split_expression(text: str) -> List[List[str]]:
    """把一个许可表达式拆成 AND / OR 两级结构。

    SPDX 的语义：
    - ``A OR B``  —— 双许可，**选一个**即可（取最宽松的那支）；
    - ``A AND B`` —— 两条同时成立（取最严格的那支）。

    所以 "MIT OR Apache-2.0 OR LGPL-2.1-or-later" 里的 LGPL 是**可选分支**，
    不该算「需审阅」；而 "Apache-2.0 AND LGPL-3.0-or-later" 才是真约束。
    先按 AND 切、再按 OR 切，正好对应这个语义。
    """
    # SPDX 的例外条款（"Apache-2.0 WITH LLVM-exception"）保留完整形式 ——
    # FOSSA 按标识匹配，"Apache-2.0 WITH LLVM-exception" 才是它认的写法。
    # 分类时按**基础许可**判（例外不改变宽松度），但输出必须保留原表达式。
    # crates.io 的斜杠写法（"MIT/Apache-2.0"）语义等同 OR —— 上游惯例是
    # 「二选一」，不是「同时满足」。不切开会被当成一个不存在的标识。
    text = re.sub(r"\s*/\s*", " OR ", text)
    # 表达式级括号（"(MIT OR Apache-2.0) AND Unicode-3.0"）：先剥掉外层
    # 括号再切分，否则括号里的 OR 会被当成 AND 组内的一个选项。
    text = re.sub(r"^\((.*)\)$", r"\1", text.strip())
    groups: List[List[str]] = []
    for and_part in re.split(r"\s+AND\s+", text, flags=re.I):
        options = [o.strip() for o in re.split(r"\s+OR\s+", and_part, flags=re.I)]
        options = [o for o in options if o]
        if options:
            groups.append(options)
    return groups


def classify(license_text: Optional[str]) -> str:
    """把一个许可表达式归到三档之一（按 SPDX 的 AND/OR 语义求值）。"""
    if not license_text:
        return UNKNOWN
    text = license_text.strip()
    if not text:
        return UNKNOWN

    worst = None
    for options in _split_expression(text):
        # OR 分支里取**最宽松**的那支（可选分支不该拉严整包）
        best = None
        for opt in options:
            bucket = _classify_one(opt)
            if bucket == UNKNOWN:
                continue
            if best is None or _RANK[bucket] < _RANK[best]:
                best = bucket
        if best is None:
            continue
        # AND 分支之间取**最严格**的那支（同时成立的约束都要满足）
        if worst is None or _RANK[best] > _RANK[worst]:
            worst = best
    if worst is not None:
        return worst
    if _DENY_PAT.search(text):
        return "DENY"
    if _REVIEW_PAT.search(text):
        return "REVIEW"
    return UNKNOWN




# ---------------------------------------------------------------- 各生态枚举

_REQ = re.compile(r"^([A-Za-z0-9_.\-]+)\s*(==|>=|~=|>|<)?\s*([0-9][0-9.]*)?")
_EXCLUDE = ("#", "-c ", "--", "git+")


def _py_floors(path: Path) -> List[Tuple[str, str]]:
    """从 requirements 清单里取「声明的下限」—— FOSSA 审的就是声明值。"""
    out: List[Tuple[str, str]] = []
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if any(line.startswith(x) for x in _EXCLUDE):
            continue
        line = line.split(";")[0].strip()
        m = _REQ.match(line)
        if not m:
            continue
        name = m.group(1).lower()
        version = m.group(3) or ""
        out.append((name, version))
    return out


def enumerate_pypi() -> List[Tuple[str, str, str]]:
    seen = set()
    out = []
    for req in ("requirements.txt", "requirements.desktop.txt"):
        for name, ver in _py_floors(ROOT / req):
            if name in seen:
                continue
            seen.add(name)
            out.append(("PyPI", name, ver))
    return out


def enumerate_npm() -> List[Tuple[str, str, str]]:
    path = ROOT / "web" / "package-lock.json"
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for pkg_path, meta in (data.get("packages") or {}).items():
        if not pkg_path or not meta.get("version"):
            continue
        name = pkg_path.split("node_modules/")[-1]
        out.append(("npm", name, meta["version"]))
    return out


def enumerate_crates() -> List[Tuple[str, str, str]]:
    out = []
    for rel in ("desktop/Cargo.lock", "native/face_tracker/Cargo.lock"):
        path = ROOT / rel
        if not path.is_file():
            continue
        for block in path.read_text(encoding="utf-8", errors="ignore").split("[[package]]"):
            name = re.search(r'^name = "([^"]+)"', block, re.M)
            version = re.search(r'^version = "([^"]+)"', block, re.M)
            if name and version:
                out.append(("crates.io", name.group(1), version.group(1)))
    return out


def enumerate_go() -> List[Tuple[str, str, str]]:
    path = ROOT / "api" / "go.sum"
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        parts = line.split()
        if len(parts) >= 2 and not parts[1].endswith("/go.mod"):
            out.append(("Go", parts[0], parts[1].lstrip("v")))
    return out


# ---------------------------------------------------------------- 许可解析

_UA = {"User-Agent": "live2d-auto-pipeline license inventory"}

#: 解析结果缓存 —— 依赖树有 700+ 条，逐次重跑没必要反复打扰上游 registry。
CACHE_PATH = Path(os.environ.get("FOSSA_LICENSE_CACHE", "/tmp/fossa-license-cache.json"))


def _get_json(url: str, timeout: int = 45, retries: int = 3) -> Optional[dict]:
    """带退避重试的 JSON 抓取。

    crates.io 对匿名请求限流很严（429），并发一高就会大面积失败 —— 失败会被
    误记成「许可未知」，让报告的数字失真。这里退避重试是必要的。

    必须覆盖 ``http.client.HTTPException``：连接被中途截断时 ``resp.read()``
    抛的是 ``IncompleteRead``，它继承自 ``HTTPException`` 而**不是** ``OSError``，
    漏掉就会一路逃出线程池把整个脚本打挂（CI 上实测：
    ``IncompleteRead(7339 bytes read, 50666 more expected)``）。本地网络稳定时
    不会暴露，只有 registry 限流的 CI 环境才炸 —— 这类异常必须归到「可重试」。
    """
    delay = 1.5
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=_UA)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8", errors="ignore"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            if exc.code == 404:
                return None
            if attempt < retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            return None
        except (
            urllib.error.URLError,
            TimeoutError,
            ValueError,
            OSError,
            http.client.HTTPException,  # IncompleteRead / RemoteDisconnected 等
        ):
            if attempt < retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            return None
    return None


def _license_from_info(info: dict) -> Optional[str]:
    expr = info.get("license_expression")
    if expr:
        return expr
    lic = (info.get("license") or "").strip()
    if lic and len(lic) < 120:
        return lic
    for classifier in info.get("classifiers") or []:
        if classifier.startswith("License ::"):
            return classifier.split("::")[-1].strip()
    return None


def pypi_license(name: str, version: str) -> Optional[str]:
    data = _get_json(f"https://pypi.org/pypi/{name}/json")
    if data:
        lic = _license_from_info(data.get("info") or {})
        if lic:
            return lic
    # 回退：查具体版本的元数据（最新版的元数据可能没有 license 字段）
    if version:
        data = _get_json(f"https://pypi.org/pypi/{name}/{version}/json")
        if data:
            lic = _license_from_info(data.get("info") or {})
            if lic:
                return lic
    return None


def npm_license(name: str, _version: str) -> Optional[str]:
    # 锁文件自带许可，无需联网（enumerate 时已读到，此处按名字回查缓存）。
    return _NPM_LICENSE_CACHE.get(name)


def go_license(module: str, version: str) -> Optional[str]:
    quoted = module.replace("/", "%2F")
    data = _get_json(
        f"https://api.deps.dev/v3/systems/go/packages/{quoted}/versions/v{version}")
    if not data:
        return None
    licenses = data.get("licenses") or []
    return " OR ".join(licenses) if licenses else None


_NPM_LICENSE_CACHE: Dict[str, str] = {}


def _own_crate_licenses() -> Dict[str, str]:
    """本仓库自己发布的 crate → 其许可取自仓库内的 Cargo.toml。

    ``face_tracker`` 与 ``pygmalion-studio`` 没发布到 crates.io，registry
    上查不到许可，但它们的许可就写在仓库的 ``Cargo.toml`` 里（ADR-001 Rev.4
    的 Apache-2.0）。查不到不等于未知 —— 这里按第一方处理。
    """
    out: Dict[str, str] = {}
    for rel in ("desktop/Cargo.toml", "native/face_tracker/Cargo.toml"):
        path = ROOT / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        name = re.search(r'^\s*name\s*=\s*"([^"]+)"', text, re.M)
        license_field = re.search(r'^\s*license\s*=\s*"([^"]+)"', text, re.M)
        if name and license_field:
            out[name.group(1)] = license_field.group(1)
    return out


_OWN_CRATES: Dict[str, str] = {}


def crates_license(name: str, version: str) -> Optional[str]:
    # 第一方 crate：许可就在本仓库的 Cargo.toml 里
    if name in _OWN_CRATES:
        return _OWN_CRATES[name]
    # deps.dev 优先：它没有 crates.io 那种严格限流，一次能取到 SPDX 许可；
    # crates.io API 作为回退。
    data = _get_json(
        f"https://api.deps.dev/v3/systems/crate/packages/{name}/versions/{version}")
    if data:
        licenses = data.get("licenses") or []
        if licenses:
            return " OR ".join(licenses)
    data = _get_json(f"https://crates.io/api/v1/crates/{name}")
    if not data:
        return None
    versions = data.get("versions") or []
    for entry in versions:
        if entry.get("num") == version:
            return entry.get("license") or entry.get("license_expression")
    if versions:
        return versions[0].get("license") or versions[0].get("license_expression")
    return None


#: 各生态的许可解析器。放在所有解析函数**之后**声明 —— 早先这里排在
#: crates_license 之前，绑定到的是旧函数对象，第一方 crate 的许可就取不到。
_RESOLVERS = {
    "PyPI": pypi_license,
    "npm": npm_license,
    "crates.io": crates_license,
    "Go": go_license,
}


# ---------------------------------------------------------------- 主流程


def collect(offline: bool) -> List[Dict[str, str]]:
    # npm 的许可在锁文件里，先缓存
    lock_path = ROOT / "web" / "package-lock.json"
    if lock_path.is_file():
        packages = json.loads(lock_path.read_text(encoding="utf-8")).get("packages") or {}
        for pkg_path, meta in packages.items():
            if pkg_path and meta.get("license"):
                _NPM_LICENSE_CACHE[pkg_path.split("node_modules/")[-1]] = meta["license"]

    # 第一方 crate 的许可取自仓库自己的 Cargo.toml
    _OWN_CRATES.clear()
    _OWN_CRATES.update(_own_crate_licenses())

    deps = enumerate_pypi() + enumerate_npm() + enumerate_crates() + enumerate_go()
    seen = set()
    unique = []
    for eco, name, ver in deps:
        key = (eco, name)
        if key in seen:
            continue
        seen.add(key)
        unique.append((eco, name, ver))

    # 磁盘缓存：重跑时不必再打扰上游 registry
    cache: Dict[str, str] = {}
    if CACHE_PATH.is_file():
        try:
            cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            cache = {}

    rows: List[Dict[str, str]] = []

    def resolve(item):
        eco, name, ver = item
        key = f"{eco}:{name}"
        lic = cache.get(key)
        if lic is None:
            lic = None
            if not offline or eco == "npm":
                lic = _RESOLVERS[eco](name, ver)
            cache[key] = lic or ""
        # 第一方组件（本仓库自己发布的 crate）不适用依赖策略：它的许可由
        # ADR-001 决定（Rev.5 起 = AGPL-3.0），不能按「第三方依赖是否兼容
        # 本项目」的三档判定，否则项目自身会被自己的策略判成「禁止」。
        own = name in _OWN_CRATES
        rows.append({
            "ecosystem": eco, "name": name, "version": ver,
            "license": lic or UNKNOWN,
            "bucket": "SELF" if own else classify(lic),
        })

    # 分生态限速：crates.io / PyPI 对匿名并发敏感，一味开 16 线程会大面积
    # 429，失败被误记成「许可未知」，报告数字就失真了。
    for eco, workers in (("npm", 8), ("PyPI", 8), ("crates.io", 6), ("Go", 8)):
        group = [d for d in unique if d[0] == eco]
        if not group:
            continue
        if offline and eco != "npm":
            for item in group:
                resolve(item)
            continue
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(resolve, group))

    try:
        CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass

    rows.sort(key=lambda r: (r["ecosystem"], r["name"]))
    return rows


def print_report(rows: List[Dict[str, str]]) -> None:
    # 第一方组件单列，不计入依赖三档（它是「我们自己」，不是「我们的依赖」）。
    dep_rows = [r for r in rows if r["bucket"] != "SELF"]
    self_rows = [r for r in rows if r["bucket"] == "SELF"]
    total = len(dep_rows)
    counts = Counter(r["bucket"] for r in dep_rows)
    print(f"依赖总数：{total}")
    for bucket in ("ALLOW", "REVIEW", "DENY", UNKNOWN):
        print(f"  {bucket:8s} {counts.get(bucket, 0):4d}")
    print()
    by_eco = defaultdict(Counter)
    for r in dep_rows:
        by_eco[r["ecosystem"]][r["bucket"]] += 1
    print(f"{'生态':10s} {'总数':>6s} {'允许':>6s} {'需审阅':>8s} {'禁止':>6s} {'未知':>6s}")
    for eco in sorted(by_eco):
        c = by_eco[eco]
        print(f"{eco:10s} {sum(c.values()):6d} {c['ALLOW']:6d} {c['REVIEW']:8d} "
              f"{c['DENY']:6d} {c[UNKNOWN]:6d}")
    print()
    if self_rows:
        print("=== 第一方组件（本仓库自有，不适用依赖策略）===")
        for r in self_rows:
            print(f"  {r['ecosystem']:10s} {r['name']:34s} {r['version']:12s} "
                  f"{r['license']}  ← ADR-001 Rev.5：AGPL-3.0 + 商业授权双轨")
        print()
    print("=== 需人工审阅（弱 copyleft / 条款型）===")
    for r in rows:
        if r["bucket"] == "REVIEW":
            print(f"  {r['ecosystem']:10s} {r['name']:34s} {r['version']:12s} {r['license']}")
    print()
    print("=== 禁止（强 copyleft / 非商业条款）===")
    found = False
    for r in rows:
        if r["bucket"] == "DENY":
            found = True
            print(f"  {r['ecosystem']:10s} {r['name']:34s} {r['version']:12s} {r['license']}")
    if not found:
        print("  （无）")
    print()
    print("=== 许可未知（FOSSA 会记成「未知许可」issue 的主要来源）===")
    found = False
    for r in rows:
        if r["bucket"] == UNKNOWN:
            found = True
            print(f"  {r['ecosystem']:10s} {r['name']:34s} {r['version']:12s}")
    if not found:
        print("  （无）")


def _spdx_ids(license_text: str) -> List[str]:
    """从一个许可表达式里抽出各个 SPDX 标识。

    FOSSA 的策略是**按单个 SPDX 标识**匹配的（允许列表里写 "MIT"），
    不是按整个表达式（"MIT OR Apache-2.0"）。所以策略建议必须拆开。

    注意 AND / OR 的语义差别（见 :func:`classify`）：拆出来的标识里，
    ``OR`` 分支是**可选**的、``AND`` 分支是**强制的**。策略清单按标识列全
    （FOSSA 自己会按表达式求值），但报告里的档位必须区分这两种语义。
    """
    out: List[str] = []
    for options in _split_expression(license_text):
        for opt in options:
            # 先剥整个括号组，再剥零散括号 —— 顺序反了会留下半个括号。
            cleaned = re.sub(r"\s*\([^)]*\)\s*$", "", opt.strip()).strip()
            cleaned = re.sub(r"^\(+|\)+$", "", cleaned).strip()
            if not cleaned:
                continue
            normalized = _normalize_spdx(cleaned)
            if normalized and normalized not in out:
                out.append(normalized)
    return out


def print_policy(rows: List[Dict[str, str]]) -> None:
    print("# 建议的 FOSSA Licensing 策略")
    print("#")
    print("# 用法：FOSSA 后台 → Policies → Licensing → Create Policy → Type: Licensing")
    print("#       按下面三档填「允许 / 需审阅 / 禁止」的许可标识列表。")
    print("#")
    print("# 判定基准：本仓库自身自 v0.10.10 起是 **AGPL-3.0 + 商业授权双轨**"
          "（ADR-001 Rev.5）。")
    print("# 本策略只管**第三方依赖**；本项目自己的两个 crate（pygmalion-studio /")
    print("# face_tracker）不在三档里 —— 它们的许可由 ADR-001 决定。")
    print("# 依赖侧仍按「该依赖是否适合进入对外分发物」三档判定，**不因项目自身")
    print("# 协议变更而自动放宽**：强 copyleft 依赖继续需要人工确认后再决定。")
    print("#")
    print("# 下面是**单个 SPDX 标识**（FOSSA 按标识匹配，不是按整个表达式）。")
    print()

    allow: List[str] = []
    review: List[str] = []
    deny: List[str] = []
    for r in rows:
        if r["bucket"] == "SELF":
            continue
        if r["license"] == UNKNOWN:
            continue
        # 按**单个标识**独立定档，不能按整个表达式 —— "Apache-2.0 AND
        # LGPL-3.0-or-later" 若按表达式定档，LGPL 会被混进「允许」档，
        # 而弱 copyleft 恰恰是需要人工确认的那一档。
        for ident in _spdx_ids(r["license"]):
            bucket = classify(ident)
            target = {"ALLOW": allow, "REVIEW": review, "DENY": deny}.get(bucket)
            if target is None:
                continue
            if ident not in target and ident not in allow and ident not in review and ident not in deny:
                target.append(ident)

    def show(title: str, items: List[str], note: str = "") -> None:
        print(f"## {title}")
        if note:
            print(f"# {note}")
        for item in sorted(items):
            print(f"  - {item}")
        if not items:
            print("  - （无）")
        print()

    show("允许（无需审阅）", allow,
         "宽松型许可，可自由再分发，不施加文件级传染条件。")
    show("允许，但需一次人工确认（弱 copyleft）", review,
         "以库形式（动态链接 / import）随本项目分发是兼容的；\n"
         "# 要求确认一次：pygame / pygame-ce / edge-tts 是 LGPL（被 import），\n"
         "# @img/sharp-libvips-* 是 libvips 的 LGPL 运行时（仅 Linux/macOS 分支用）。")
    show("禁止", deny,
         "强 copyleft / 非商业条款 —— 需人工确认后再决定是否引入（策略不随项目自身协议变更而放宽）。")

    unknown = sorted({r["name"] for r in rows if r["bucket"] == UNKNOWN})
    if unknown:
        print("## 许可未声明 —— 需逐个确认上游实际许可后加入允许清单")
        for name in unknown:
            print(f"  - {name}")
        print()

    print("# 备注")
    print("# - 本清单由 scripts/fossa_license_inventory.py 生成，可复现：")
    print("#     python scripts/fossa_license_inventory.py --policy")
    print("# - 依赖树变动后重跑一次，策略里的许可列表也应同步更新。")
    print("# - 仓库侧无法读取 FOSSA 的逐条 issue 明细（需企业版 API），")
    print("#   所以这份清单是按依赖树的实际许可推导的，不是照抄扫描结论。")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--policy", action="store_true", help="输出策略建议")
    parser.add_argument("--offline", action="store_true",
                        help="只用本地元数据（仅 npm 锁文件；其余记为未知）")
    args = parser.parse_args()

    rows = collect(args.offline)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    elif args.policy:
        print_policy(rows)
    else:
        print_report(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
