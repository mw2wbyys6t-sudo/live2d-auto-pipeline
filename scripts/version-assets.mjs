#!/usr/bin/env node
/**
 * 桌面版构建后处理：给静态资源 URL 加版本化前缀。
 *
 * `/_next/...` → `/_next-<ts>/...`（HTML、JS、CSS 内所有出现处）。
 * Go 侧 NoRoute 把 `/_next-<ts>/` 映射回内嵌的 `_next/` 目录。
 * 每次构建时间戳变化，浏览器端旧资源 URL 全部失效，
 * 彻底杜绝「升级后浏览器沿用旧缓存壳/chunk」导致的页面错乱。
 *
 * 幂等：`next.config.js` 的 assetPrefix 已经产出 `/_next-<ts>/_next/...`，
 * 这里只重写尚未带版本号的 `/_next/`，否则会叠加成
 * `/_next-<ts1>/_next-<ts2>/...`，Go 侧映射不到文件 → CSS/JS 全部 404
 * （v0.10.8 的裸 HTML 根因）。
 */
import { readdirSync, readFileSync, writeFileSync, statSync } from 'node:fs';
import { join, extname } from 'node:path';

const outDir = process.argv[2] || 'web/out';
const ts = Date.now();
const from = /(?<!\/_next-\d+)\/_next\//g;
const to = `/_next-${ts}/`;
const textExts = new Set(['.html', '.js', '.css', '.mjs']);

let files = 0, hits = 0;
(function walk(dir) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    const st = statSync(p);
    if (st.isDirectory()) { walk(p); continue; }
    if (!textExts.has(extname(name).toLowerCase())) continue;
    const src = readFileSync(p, 'utf8');
    const next = src.replace(from, to);
    if (next !== src) {
      writeFileSync(p, next);
      hits++;
    }
    files++;
  }
})(outDir);

console.log(`[version-assets] ts=${ts} 扫描 ${files} 个文本文件，重写 ${hits} 个（/_next/ → /_next-${ts}/）`);
