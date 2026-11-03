# tools/archive/vendor — 带第三方许可头的样例文件

`.fossa.yml` 把 `./tools/archive` 排除在生产扫描面之外。本目录放的是**从上游样例
里拿来、但仍保留原作者版权与许可头**的文件：它们不参与产品运行，也不该被改成
本项目的 AGPL-3.0 头（那等于替版权方重新授权）。

| 文件 | 原始位置 | 许可 | 说明 |
| --- | --- | --- | --- |
| `seedream_image_generate.py` | `scripts/` | Apache-2.0（火山引擎版权头） | 即梦/Seedream 图像接口调用样例。全仓库零引用，2026-07-29 后未改动；留在 `scripts/` 会让 FOSSA 记一条「Apache License 2.0 directly in code」，与本项目的 AGPL 声明对不上。 |

需要重新使用时 `git mv` 回原位即可，`git log --follow` 能穿透本目录追到归档前的历史。
