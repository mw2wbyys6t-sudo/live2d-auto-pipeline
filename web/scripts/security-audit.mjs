#!/usr/bin/env node
/**
 * 生产依赖漏洞门禁。
 *
 * 背景（2026-11 FOSSA 扫描）：picomatch <= 4.0.7 命中 CVE-2026-112366
 * （CWE-1333 正则复杂度 ReDoS），而 npm 上最新版本就是 4.0.7 —— 上游还没有
 * 任何修复版本。本仓库的两条命中都来自 tailwindcss 的**构建期**依赖链：
 *
 *   tailwindcss → micromatch → picomatch@2.3.2
 *   tailwindcss → sucrase → tinyglobby → fdir/picomatch@4.0.7
 *
 * `NEXT_STATIC_EXPORT` 产物只有编译后的 CSS/JS，Go 二进制内嵌的也是这些静态
 * 文件；两个包都不会进入运行时，攻击面仅限「构建时传入恶意 glob」。
 *
 * 所以这里不做假修复（升到同样有漏洞的版本），而是把两个可验证的事实钉死：
 *   1. 生产依赖树（--omit=dev）里绝不能出现受影响的包；
 *   2. 生产依赖的 npm audit 结论里不能有 high / critical。
 * 等上游发布修复版本后，升级 picomatch 再重跑本门禁即可，无需改本脚本。
 */
import { execSync } from 'node:child_process';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

/** 已知「上游无修复版本、且只允许出现在 devDependencies」的包。 */
const DEV_ONLY_TRACKED = ['picomatch'];

function run(cmd) {
  try {
    return execSync(cmd, { cwd: webRoot, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
  } catch (err) {
    // npm audit / npm ls 发现漏洞时以非零码退出，stdout 仍是合法内容。
    return `${err.stdout ?? ''}`;
  }
}

/** 从 `npm ls --parseable` 的输出里挑出真正安装了该包的路径。 */
function installedPaths(output, name) {
  return output
    .split(/\r?\n/)
    .map((line) => line.trim().replace(/\\/g, '/'))
    .filter((line) => line.includes(`/node_modules/${name}`));
}

function main() {
  let failed = false;
  const fail = (message) => {
    console.error(`[security-audit] ✗ ${message}`);
    failed = true;
  };

  // 1) 受跟踪包不得出现在生产依赖树里。
  for (const name of DEV_ONLY_TRACKED) {
    const hits = installedPaths(run(`npm ls ${name} --omit=dev --all --parseable`), name);
    if (hits.length > 0) {
      fail(`${name} 出现在生产依赖树中：\n${hits.join('\n')}`);
    }
  }

  // 2) 生产依赖 audit 不得有 high / critical。
  let report;
  try {
    report = JSON.parse(run('npm audit --omit=dev --json'));
  } catch {
    fail('npm audit 输出无法解析，无法确认生产依赖状态');
    report = null;
  }
  if (report) {
    const counts = report?.metadata?.vulnerabilities ?? {};
    const prodHigh = (counts.high ?? 0) + (counts.critical ?? 0);
    if (prodHigh > 0) {
      const names = Object.keys(report.vulnerabilities ?? {}).join(', ');
      fail(`生产依赖存在 ${prodHigh} 个 high/critical 漏洞：${names || '(见 npm audit)'}`);
    }
  }

  // 3) 构建期（devDependencies）命中只提示，不阻断 —— 目前没有修复版本可升。
  const devHits = installedPaths(run(`npm ls ${DEV_ONLY_TRACKED.join(' ')} --all --parseable`), DEV_ONLY_TRACKED[0]);
  if (devHits.length > 0) {
    console.warn(
      '[security-audit] ⚠ 构建期依赖命中 CVE-2026-112366（上游暂无修复版本）：\n' +
        devHits.map((line) => `  - ${line}`).join('\n') +
        '\n  这些包只用于本地/CI 构建，不进入静态导出与二进制；上游发布修复版本后请升级。'
    );
  }

  if (failed) {
    process.exitCode = 1;
    return;
  }
  console.log('[security-audit] ✓ 生产依赖无 high/critical 漏洞，受跟踪包均未进入运行时');
}

main();
