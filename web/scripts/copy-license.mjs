/**
 * 把仓库根的许可证文件复制进 web/public/（Next 静态导出会把 public/ 原样
 * 复制到 web/out/，再由 api/webui/dist 内嵌进 Go 二进制）。
 *
 * 为什么走 prebuild 而不是把 LICENSE 直接提交进 web/public/：
 * 仓库根 LICENSE 是**唯一真源**（ADR-001 Rev.4）。提交一份副本就多一个会
 * 漂移的副本 —— 改了根目录那份、忘了改副本，分发出去的许可文本就是旧的。
 * 这里在每次构建时从根目录复制，副本永远与真源一致。
 *
 * FF-15（License 与署名完整性）要求产出物内含与根目录逐字节一致的许可全文。
 */
import { copyFileSync, existsSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(here, '..', '..');
const publicDir = join(here, '..', 'public');

const LICENSE_NAMES = ['LICENSE', 'LICENSE.txt', 'COPYING'];
const NOTICE_NAMES = ['NOTICE', 'NOTICE.txt'];

function pick(names) {
  for (const name of names) {
    const candidate = join(repoRoot, name);
    if (existsSync(candidate)) return candidate;
  }
  return null;
}

mkdirSync(publicDir, { recursive: true });

const sources = [pick(LICENSE_NAMES), pick(NOTICE_NAMES)].filter(Boolean);
if (sources.length === 0) {
  console.error('[license] 仓库根找不到 LICENSE —— 无法产出合规的 Web 构建包（FF-15）');
  process.exit(1);
}

for (const src of sources) {
  const dest = join(publicDir, src.split(/[\\/]/).pop());
  copyFileSync(src, dest);
  console.log(`[license] ${dest.split(/[\\/]/).pop()} <- 仓库根（逐字节一致）`);
}
