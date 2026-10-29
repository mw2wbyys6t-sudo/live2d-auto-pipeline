/**
 * 品牌常量（Pygmalion Studio）在前端的唯一出口。
 *
 * 版本由 `next.config.js` 在构建期从 `web/package.json` 注入 —— 该文件的版本
 * 与 `core/version.py` 的一致性由 `tests/unit/test_release_version_single_source.py`
 * 钉住。组件里再写死字面量就会像 v0.10.9 那样：改了版本，页脚还显示旧版本。
 */

export const APP_NAME = 'Pygmalion Studio';

export const APP_VERSION: string = process.env.NEXT_PUBLIC_APP_VERSION || '0.0.0-dev';
