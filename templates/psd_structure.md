# PSD 图层结构模板

## 层级顺序（从上到下）

1. 头发最前层
2. 头发前层
3. 脸部装饰
4. 眼睛
5. 嘴巴
6. 脸部基础
7. 头发侧层
8. 头发后层
9. 衣服
10. 身体
11. 背景

## 图层命名示例

```
hair_front_01
hair_front_02
hair_side_l_01
hair_side_r_01
hair_back_01
face_base
face_shadow
eye_l_white
eye_l_iris
eye_l_pupil
eye_r_white
eye_r_iris
eye_r_pupil
mouth_base
mouth_a
mouth_i
mouth_u
mouth_e
mouth_o
body_base
clothes_top
```

## 推荐分层规范（对照 psd2live 的最佳实践）

导入外部 PSD 时，系统会**按图层名识别部件**（`core/psd/part_naming.py`，
支持英/中/日命名 + 左右侧向 + 口型元音）。要让自动绑定达到最佳效果，PSD 应满足：

1. **眼睛拆细**：眼白（eye_white / 白目）、虹膜（iris / 虹膜）、瞳孔（pupil / 瞳孔）、
   上睫毛（eyelash / 睫毛）各自独立层 —— 眼球开合与视线绑定依赖这些分离层；
2. **口型素材按元音命名**：`mouth_a / mouth_i / mouth_u / mouth_e / mouth_o`
   （或 嘴_a…），自动绑定据此生成口型参数键形；
3. **前后发分离**：`hair_front`（前发/前髪）与 `hair_back`（后发/後髪）必须分层，
   头部 Z 轴摆动时前后发位移方向不同；侧发按左右命名（`hair_side_l / hair_side_r`）；
4. **身体直立**：身体层保持直立姿态，避免倚靠/透视姿势（变形器按直立基准生成）；
5. **左右对称部件显式命名左右**：`eye_l_* / eye_r_*`、`左目/右目`、
   `arm_left / arm_right` —— 自动左右配对依赖这些标记；
6. **图层从上到下 = 前到后**：导入时按此顺序生成层序（底层在前、前景最后绘制）。

### 图层名 → 部件映射速查

| 部件 | 英文关键词 | 中文 | 日文 |
|------|-----------|------|------|
| 眼白 | eye_white, sclera | 白眼/眼白 | 白目 |
| 虹膜 | iris, eye | 虹膜/眼球 | 瞳 |
| 瞳孔 | pupil | 瞳孔 | — |
| 睫毛 | eyelash, lash | 睫毛 | まつ毛 |
| 嘴 | mouth, lip | 嘴/唇 | 口 |
| 前发 | hair_front, bangs | 前发/刘海 | 前髪 |
| 侧发 | hair_side, sidelock | 侧发/鬓 | もみあげ |
| 后发 | hair_back | 后发 | 後髪 |
| 身体 | body, torso, neck | 身体/脖子 | からだ |
| 衣服 | clothes, skirt, dress | 衣/服/裙 | — |
| 饰品 | ribbon, hat, glasses | 蝴蝶结/帽子/眼镜 | リボン |

