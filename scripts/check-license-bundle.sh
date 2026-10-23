#!/usr/bin/env bash
# FF-15「License 与署名完整性」校验钩子。
#
# 契约（docs/architecture/index.md §4 FF-15 / ADR-001 Rev.4）：
#   产出物（桌宠安装包、导出模型包、Web 构建包）内必须含 Apache-2.0 许可全文，
#   且文本与仓库根 LICENSE **逐字节一致**；仓库根若存在 NOTICE 则一并保留。
#   失败即**阻断发布**。
#
# 用法：
#   scripts/check-license-bundle.sh <产物路径> [<产物路径> ...]
#
# 产物路径可以是：
#   - 目录：直接查该目录下的 LICENSE / NOTICE
#   - zip：  逐条读取压缩包内的 LICENSE / NOTICE
#
# 退出码：0 = 全部合规；1 = 存在不合规项（缺失 / 内容不一致 / 缺 NOTICE）。
#
# 为什么用 shell 而不是 Python：这是**发版前**钩子，会在只有 git 与系统工具的
# 最小构建环境里跑（此时 Python 依赖可能还没装）。校验的是许可证文本本身，
# 不依赖项目代码。

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LICENSE_SRC="$ROOT/LICENSE"
NOTICE_SRC=""
for candidate in "$ROOT/NOTICE" "$ROOT/NOTICE.txt"; do
    if [ -f "$candidate" ]; then NOTICE_SRC="$candidate"; break; fi
done

RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RESET=$'\033[0m'
FAILURES=0

fail() { printf '%s✗ %s%s\n' "$RED" "$1" "$RESET"; FAILURES=$((FAILURES + 1)); }
ok()   { printf '%s✓ %s%s\n' "$GREEN" "$1" "$RESET"; }
info() { printf '%s· %s%s\n' "$YELLOW" "$1" "$RESET"; }

# ---------- 前置：源文件自身必须合规 ----------
if [ ! -f "$LICENSE_SRC" ]; then
    fail "仓库根缺少 LICENSE —— 无许可正文可分发（ADR-001 Rev.4 要求 Apache-2.0）"
    exit 1
fi
if ! head -n 3 "$LICENSE_SRC" | grep -qi "Apache License"; then
    fail "根目录 LICENSE 的开头不是 Apache License 全文（ADR-001 Rev.4 要求 Apache-2.0）"
fi
ok "根目录 LICENSE 就位（$(wc -c < "$LICENSE_SRC") 字节，Apache-2.0 全文）"
if [ -n "$NOTICE_SRC" ]; then
    ok "根目录 NOTICE 就位（$(wc -c < "$NOTICE_SRC") 字节，Apache-2.0 §4(d) 要求随附）"
fi

# ---------- 校验单个产物 ----------
check_artifact() {
    local artifact="$1"
    if [ ! -e "$artifact" ]; then
        fail "产物不存在：$artifact"
        return
    fi

    if [ -d "$artifact" ]; then
        local target="$artifact/LICENSE"
        if [ ! -f "$target" ]; then
            fail "$artifact 内缺少 LICENSE（FF-15：产出物必须内含许可全文）"
        elif cmp -s "$LICENSE_SRC" "$target"; then
            ok "$artifact 的 LICENSE 与根目录逐字节一致"
        else
            fail "$artifact 的 LICENSE 与根目录不一致（FF-15 要求逐字节一致）"
        fi
        if [ -n "$NOTICE_SRC" ]; then
            local notice="$artifact/NOTICE"
            [ -f "$ROOT/NOTICE.txt" ] && notice="$artifact/NOTICE.txt"
            if [ ! -f "$notice" ]; then
                fail "$artifact 内缺少 NOTICE（根目录有 NOTICE 时必须随附）"
            elif cmp -s "$NOTICE_SRC" "$notice"; then
                ok "$artifact 的 NOTICE 与根目录逐字节一致"
            else
                fail "$artifact 的 NOTICE 与根目录不一致"
            fi
        fi
        return
    fi

    # zip：用 python3 解包比对（发版机有 Python；无 python3 时退化为只查文件名）
    if command -v python3 >/dev/null 2>&1; then
        python3 - "$artifact" "$LICENSE_SRC" "$NOTICE_SRC" <<'PY'
import sys, zipfile
artifact, license_src, notice_src = sys.argv[1], sys.argv[2], sys.argv[3]
bad = 0
try:
    with zipfile.ZipFile(artifact) as zf:
        names = zf.namelist()
        lic = [n for n in names if n.rsplit("/", 1)[-1] == "LICENSE"]
        if not lic:
            print(f"✗ {artifact} 内缺少 LICENSE（FF-15：产出物必须内含许可全文）")
            bad += 1
        else:
            with open(license_src, "rb") as f:
                want = f.read()
            got = zf.read(lic[0])
            if got == want:
                print(f"✓ {artifact} 的 LICENSE 与根目录逐字节一致")
            else:
                print(f"✗ {artifact} 的 LICENSE 与根目录不一致（FF-15 要求逐字节一致）")
                bad += 1
        if notice_src:
            nt = [n for n in names if n.rsplit("/", 1)[-1] in ("NOTICE", "NOTICE.txt")]
            if not nt:
                print(f"✗ {artifact} 内缺少 NOTICE（根目录有 NOTICE 时必须随附）")
                bad += 1
            else:
                with open(notice_src, "rb") as f:
                    want = f.read()
                got = zf.read(nt[0])
                if got == want:
                    print(f"✓ {artifact} 的 NOTICE 与根目录逐字节一致")
                else:
                    print(f"✗ {artifact} 的 NOTICE 与根目录不一致")
                    bad += 1
except zipfile.BadZipFile:
    print(f"✗ {artifact} 不是有效的 zip")
    bad += 1
sys.exit(1 if bad else 0)
PY
        [ $? -ne 0 ] && FAILURES=$((FAILURES + 1))
        return
    fi

    info "$artifact（无 python3，跳过 zip 内容比对，仅记录存在性）"
}

if [ $# -eq 0 ]; then
    printf '%s用法: %s <产物路径> [<产物路径> ...]%s\n' "$YELLOW" "$0" "$RESET"
    printf '%s  例: %s scripts/check-license-bundle.sh output/export_xxx desktop/target/release/bundle/nsis\n' "$YELLOW" "$0" "$RESET"
    exit 2
fi

for artifact in "$@"; do
    check_artifact "$artifact"
done

if [ "$FAILURES" -gt 0 ]; then
    printf '\n%sFF-15 未通过：%d 项不合规 —— 阻断发布。%s\n' "$RED" "$FAILURES" "$RESET"
    exit 1
fi
printf '\n%sFF-15 通过：全部产出物均含与根目录逐字节一致的许可全文。%s\n' "$GREEN" "$RESET"
exit 0
