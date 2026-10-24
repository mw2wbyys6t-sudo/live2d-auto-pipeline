# 从零训练部位分层模型

这条链路刻意和主项目解耦：只依赖 `torch / numpy / Pillow`，不 import `core`。
训练产物通过 `core/custom_model` 的 ONNX 契约接回管线。

## 它训练什么

输入一张立绘，输出**逐像素的部位类别图**（label map）。默认 9 类：

| 索引 | 类别 | 说明 |
|---|---|---|
| 0 | background | 背景 |
| 1 | hair_back | 后发 |
| 2 | hair_front | 刘海/前发 |
| 3 | face | 脸部皮肤 |
| 4 | eyes_left | 左眼（含眼白/瞳孔） |
| 5 | eyes_right | 右眼 |
| 6 | mouth | 嘴 |
| 7 | neck | 脖子 |
| 8 | clothes_top | 上衣 |

这 8 个部位正好对应 `core/segment_engine/layer_quality.py` 里的
`REQUIRED_PARTS` —— 模型把这 8 类分对，就能过管线现成的分层质量门禁。

**为什么先做 9 类而不是 35 类**：从零训练时类别数直接决定难度。35 类里
很多是 Live2D 特有的细分（`eyewhite_left` / `irides_left` / `clothes_inner`），
样本量要求高一个量级。先把 9 类做到 IoU 可用，再扩类，返工成本最低。

## 1. 装依赖

主项目 `requirements.txt` 默认**不装 torch**（它是可选的）。

```bash
# 纯 CPU 环境
pip install torch torchvision onnx --index-url https://download.pytorch.org/whl/cpu

# 有 NVIDIA 显卡（按你的 CUDA 版本选）
pip install torch torchvision onnx --index-url https://download.pytorch.org/whl/cu124
```

> torch 2.9+ 的 `torch.onnx.export` 默认走 dynamo 路径，会额外要求 `onnxscript`。
> `export.py` 已显式指定 `dynamo=False` 走 TorchScript 导出器，只需要 `onnx`
> 包即可，不用装 `onnxscript`。

## 2. 先自检环境

还没数据时先跑这个，确认网络形状和反向传播没问题：

```bash
python -m training.train --self-test
```

预期输出类似：

```
[self-test] device=cpu logits=(2, 9, 128, 128) loss=2.2750 params=1,942,713
[self-test] 前向/反向通过，环境可用
```

> 内存不足时把 `--size` 调小。512×512 + base=32 的前向+反向在 CPU 上约需
> 4GB 以上；5GB 以下的机器会直接被 OOM 杀掉（exit code 137）。先用
> `--size 128 --base 16` 验证结构，再放到内存足够的机器上训。

接着跑端到端冒烟测试（合成数据 → 短训练 → 导出 → 管线回读，全程不到 1 分钟）：

```bash
python -m training.smoke_test
```

看到 `[OK] 端到端链路打通` 就说明整条链没问题，可以开始准备真实数据了。

## 3. 准备数据

目录必须严格长这样：

```
dataset/
  images/
    case_0001.png
    case_0002.png
  masks/
    case_0001.png
    case_0002.png
```

- `images/` 放原图（png/jpg/webp 均可）
- `masks/` 放**同名**的单通道 PNG，像素值 = 上表的类别索引
- 尺寸不必一致，读取时会统一 resize 到 `--size`（默认 512）
- mask 用 `NEAREST` 缩放，不会因为插值产生不存在的类别

配套工具在 `training/labels.py`：

```python
from training.labels import color_table, label_map_to_rgb, rgb_to_label_map

print(color_table())          # 打印类别表和配色
rgb_to_label_map(arr)         # 彩色标注图 → 索引图
label_map_to_rgb(arr)         # 索引图 → 彩色预览，给人看得懂
```

标注建议：先用 `24yearsold/l2d_sam_iter2`（Apache-2.0，19 部位）自动出初标，
再用上面的 `label_map_to_rgb` 转彩色图人工修。这就是「半自动标注」的闭环。

## 4. 训练

```bash
python -m training.train \
  --data ./dataset \
  --out models/custom/layering \
  --epochs 60 \
  --batch 4 \
  --size 512 \
  --base 32
```

关键参数：

| 参数 | 作用 | 建议 |
|---|---|---|
| `--base` | U-Net 首层通道数 | 32 ≈ 2M 参数；显存紧就 16 |
| `--size` | 训练分辨率 | 512 起；显存够可 768 |
| `--batch` | 批大小 | 按显存调，4 是 8GB 卡的安全值 |
| `--lr` | 学习率 | 从零训练 3e-4，加载预训练权重时降到 1e-4 |
| `--val-split` | 验证集比例 | 默认 0.15 |

训练会按验证集 mIoU 保存 `best.pt`，并写 `train_summary.json`。

**样本量参考**（从零、9 类）：
- 20～50 张：能跑通，但基本不可用
- 300～800 张：可用的起点
- 2000+ 张：开始接近专用基座的水平

## 5. 评估

```bash
python -m training.eval \
  --checkpoint models/custom/layering/best.pt \
  --data ./dataset \
  --json report.json \
  --dump Work/pred_preview
```

输出三块：

- `miou` / `per_class_iou`：纯模型指标
- `gate_passed` / `gate_pass_rate`：**管线门禁通过率**，这才是业务指标
- `defects`：每张图具体违反了哪条分层规则

`--dump` 会把预测结果存成彩色图，方便肉眼比对。

## 6. 导出成管线能吃的 ONNX

```bash
python -m training.export \
  --checkpoint models/custom/layering/best.pt \
  --out models/custom/layering \
  --name layering-v1
```

产出两个文件：

```
models/custom/layering/
  model.onnx        # 输出 [1,1,512,512] 的类别索引图
  model.json        # 契约清单，已被 core.custom_model.manifest 校验
```

## 7. 接回管线（注意：这一步目前还需要改代码）

`core/custom_model` 这套机制**已经写好但还没接进主流程** ——
`semantic.py` 和 `workflow.py` 目前都不读 `models/custom/`。所以导出之后还差：

1. 在 `SemanticSegmenter` 初始化时 `discover()` 自定义模型
2. 命中 `segmentation` 任务时优先走 `custom_model.runner.run()`
3. 把返回的 `{部位: mask}` 直接交给后续 composer

没做这一步，导出的模型放在那里不会被用到。

## 8. 想扩到 35 类怎么办

只改 `training/labels.py` 里的 `CORE_PARTS`：

```python
CORE_PARTS = (
    "hair_back", "hair_front", "face",
    "eyewhite_left", "irides_left", "eyewhite_right", "irides_right",
    "eyebrows", "mouth", "neck", "clothes_top", "clothes_inner", ...
)
```

`NUM_CLASSES`、模型输出通道、导出清单的 labels 映射都会自动跟着变。
但记住：类每翻一倍，需要的标注量至少翻一倍。

## 9. 许可证提醒

- 本项目 Apache-2.0，训练脚本无额外限制。
- 如果你后续引入第三方预训练权重当初始化，**先确认它的许可证**。
  已知可用的：`24yearsold/l2d_sam_iter2`（Apache-2.0，允许商用）。
  已知**不可用**：`suzukimain/AnimeSeg`（仓库无 LICENSE 文件，等同保留所有权利）。
- 用第三方模型自动生成标注时，该模型的许可证同样约束这份数据。
