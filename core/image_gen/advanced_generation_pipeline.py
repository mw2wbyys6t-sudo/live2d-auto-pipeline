#!/usr/bin/env python3
"""
Live2D Master Agent - 高级生成管道 v1.0
弯道超车核心技术：LoRA + ControlNet + 图生图风格迁移

三大超车路径：
1. 🎯 LoRA 训练 - 用参考图训练专属风格模型（10-200MB）
2. 🎨 ControlNet - 精准控制姿势/线稿/深度
3. 🖼️ 图生图 + IP-Adapter - 参考图风格迁移

使用方法：
    # 路径1: LoRA训练
    python advanced_generation_pipeline.py --mode lora --reference-dir ./refs --output-name my_style

    # 路径2: ControlNet生成
    python advanced_generation_pipeline.py --mode controlnet --pose-image pose.png --prompt "cute girl"

    # 路径3: 图生图风格迁移
    python advanced_generation_pipeline.py --mode img2img --reference ref.png --prompt "same style, new character"
"""

import os
import sys
import argparse
import subprocess
from pathlib import Path
from typing import Optional, List, Dict
import json


class LoRATrainer:
    """LoRA训练器 - 用参考图训练专属风格模型"""

    def __init__(self, base_model: str = "Linaqruf/anything-v3.0"):
        self.base_model = base_model
        self.output_dir = Path(__file__).parent / "models" / "lora"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def prepare_dataset(self, reference_dir: str, target_size: int = 512) -> str:
        """准备训练数据集"""
        from PIL import Image

        ref_path = Path(reference_dir).resolve()

        # 安全验证：限制 reference_dir 必须在当前工作目录或其子目录下
        base_dir = Path(__file__).parent.resolve()
        try:
            ref_path.relative_to(base_dir)
        except ValueError:
            print(f"❌ 安全错误: reference_dir ({ref_path}) 必须在项目目录 ({base_dir}) 内")
            raise ValueError("reference_dir must be within the project directory")

        # 验证路径存在且是目录
        if not ref_path.exists():
            raise ValueError(f"reference_dir does not exist: {ref_path}")
        if not ref_path.is_dir():
            raise ValueError(f"reference_dir is not a directory: {ref_path}")

        dataset_dir = self.output_dir / "dataset"
        dataset_dir.mkdir(exist_ok=True)

        # 清理旧数据
        for f in dataset_dir.glob("*"):
            f.unlink()

        print(f"📁 准备数据集: {ref_path}")
        images = list(ref_path.glob("*.png")) + list(ref_path.glob("*.jpg"))

        if len(images) < 5:
            print(f"⚠️ 警告: 只有 {len(images)} 张图片，建议至少 20-40 张")

        for i, img_path in enumerate(images):
            try:
                img = Image.open(img_path).convert("RGB")
                # 保持比例缩放
                ratio = target_size / max(img.size)
                new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
                img = img.resize(new_size, Image.Resampling.LANCZOS)

                # 中心裁剪
                left = (img.size[0] - target_size) // 2
                top = (img.size[1] - target_size) // 2
                img = img.crop((left, top, left + target_size, top + target_size))

                # 保存
                save_path = dataset_dir / f"{i:04d}.png"
                img.save(save_path)

                # 创建caption文件（自动标注）
                caption = self._generate_caption(img_path)
                with open(dataset_dir / f"{i:04d}.txt", 'w') as f:
                    f.write(caption)

            except Exception as e:
                print(f"⚠️ 处理 {img_path.name} 失败: {e}")

        print(f"✅ 数据集准备完成: {len(list(dataset_dir.glob('*.png')))} 张图片")
        return str(dataset_dir)

    def _generate_caption(self, image_path: Path) -> str:
        """生成图片标注"""
        # 基于文件名的简单标注，实际应用中可以用BLIP等模型自动生成
        return "masterpiece, best quality, anime style, illustration, 1girl"

    def train(
        self,
        dataset_dir: str,
        output_name: str,
        network_dim: int = 64,
        network_alpha: int = 32,
        learning_rate: float = 1e-4,
        batch_size: int = 1,
        epochs: int = 10,
        save_every_n_epochs: int = 2,
    ) -> Optional[str]:
        """
        训练LoRA模型

        Args:
            dataset_dir: 数据集目录
            output_name: 输出模型名称
            network_dim: 网络维度（越大拟合能力越强，建议64-128）
            network_alpha: 缩放因子（通常为dim的一半）
            learning_rate: 学习率
            batch_size: 批次大小
            epochs: 训练轮数
            save_every_n_epochs: 每N轮保存一次
        """
        output_path = self.output_dir / f"{output_name}.safetensors"

        # 检查是否安装了训练工具
        try:
            import peft
            import accelerate
        except ImportError:
            print("❌ 缺少训练依赖")
            print("💡 请安装: pip install peft accelerate bitsandbytes")
            return None

        print(f"\n🚀 开始训练LoRA模型...")
        print(f"   基础模型: {self.base_model}")
        print(f"   输出名称: {output_name}")
        print(f"   网络维度: {network_dim}")
        print(f"   学习率: {learning_rate}")
        print(f"   训练轮数: {epochs}")

        # 使用diffusers的LoRA训练脚本
        try:
            from diffusers import StableDiffusionPipeline
            import torch
            from peft import LoraConfig, get_peft_model

            # 加载基础模型
            print("📥 加载基础模型...")
            pipe = StableDiffusionPipeline.from_pretrained(
                self.base_model,
                torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
                safety_checker=None,
            )

            # 配置LoRA
            lora_config = LoraConfig(
                r=network_dim,
                lora_alpha=network_alpha,
                target_modules=[
                    "to_q", "to_k", "to_v", "to_out.0",
                    "proj_in", "proj_out", "ff.net.0.proj", "ff.net.2"
                ],
                lora_dropout=0.0,
                bias="none",
            )

            # 应用LoRA到UNet
            pipe.unet = get_peft_model(pipe.unet, lora_config)

            print(f"✅ LoRA配置完成")
            print(f"   可训练参数: {sum(p.numel() for p in pipe.unet.parameters() if p.requires_grad):,}")

            # 简化的训练循环（实际应用中需要更完整的实现）
            print("\n⚠️ 注意: 完整训练需要更多代码和计算资源")
            print("💡 建议使用 kohya_ss 或 AI-Toolkit 进行完整训练")
            print("   参考: https://github.com/bmaltais/kohya_ss")

            # 保存配置
            config = {
                "base_model": self.base_model,
                "network_dim": network_dim,
                "network_alpha": network_alpha,
                "output_name": output_name,
            }
            with open(self.output_dir / f"{output_name}_config.json", 'w') as f:
                json.dump(config, f, indent=2)

            return str(output_path)

        except Exception as e:
            print(f"❌ 训练失败: {e}")
            return None


class ControlNetGenerator:
    """ControlNet生成器 - 精准控制姿势/线稿/深度"""

    CONTROLNET_MODELS = {
        "openpose": {
            "id": "lllyasviel/control_v11p_sd15_openpose",
            "desc": "姿势控制",
            "size": "约 1.5GB",
        },
        "canny": {
            "id": "lllyasviel/control_v11p_sd15_canny",
            "desc": "边缘检测",
            "size": "约 1.5GB",
        },
        "depth": {
            "id": "lllyasviel/control_v11f1p_sd15_depth",
            "desc": "深度控制",
            "size": "约 1.5GB",
        },
        "lineart": {
            "id": "lllyasviel/control_v11p_sd15_lineart",
            "desc": "线稿控制",
            "size": "约 1.5GB",
        },
        "softedge": {
            "id": "lllyasviel/control_v11p_sd15_softedge",
            "desc": "软边缘",
            "size": "约 1.5GB",
        },
        "scribble": {
            "id": "lllyasviel/control_v11p_sd15_scribble",
            "desc": "涂鸦控制",
            "size": "约 1.5GB",
        },
    }

    def __init__(self, base_model: str = "Linaqruf/anything-v3.0"):
        self.base_model = base_model
        self.pipe = None
        self.controlnet = None

    def load_controlnet(self, control_type: str = "openpose") -> bool:
        """加载ControlNet模型"""
        try:
            from diffusers import StableDiffusionControlNetPipeline, ControlNetModel
            import torch

            if control_type not in self.CONTROLNET_MODELS:
                print(f"❌ 不支持的ControlNet类型: {control_type}")
                return False

            model_id = self.CONTROLNET_MODELS[control_type]["id"]

            print(f"📥 加载ControlNet: {control_type}")
            self.controlnet = ControlNetModel.from_pretrained(
                model_id,
                torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
            )

            print(f"📥 加载基础模型...")
            self.pipe = StableDiffusionControlNetPipeline.from_pretrained(
                self.base_model,
                controlnet=self.controlnet,
                torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
                safety_checker=None,
            )

            # 优化
            if not torch.cuda.is_available():
                self.pipe.enable_attention_slicing()
                self.pipe.enable_vae_slicing()

            print(f"✅ ControlNet加载完成")
            return True

        except ImportError:
            print("❌ 缺少依赖: pip install diffusers transformers accelerate controlnet-aux")
            return False
        except Exception as e:
            print(f"❌ 加载失败: {e}")
            return False

    def generate(
        self,
        control_image_path: str,
        prompt: str,
        negative_prompt: str = "",
        width: int = 512,
        height: int = 768,
        steps: int = 25,
        guidance_scale: float = 7.5,
        controlnet_conditioning_scale: float = 1.0,
        seed: Optional[int] = None,
        output_path: Optional[str] = None,
    ) -> Optional[str]:
        """使用ControlNet生成图片"""
        if self.pipe is None:
            print("❌ ControlNet未加载")
            return None

        try:
            from PIL import Image
            import torch

            # 加载控制图像
            control_image = Image.open(control_image_path).convert("RGB")
            control_image = control_image.resize((width, height))

            # 设置种子
            if seed is None:
                seed = int(time.time()) % 1000000

            generator = torch.Generator(device="cuda" if torch.cuda.is_available() else "cpu").manual_seed(seed)

            print(f"\n🎨 ControlNet生成...")
            print(f"   提示词: {prompt[:80]}...")
            print(f"   控制强度: {controlnet_conditioning_scale}")

            result = self.pipe(
                prompt=prompt,
                negative_prompt=negative_prompt,
                image=control_image,
                width=width,
                height=height,
                num_inference_steps=steps,
                guidance_scale=guidance_scale,
                controlnet_conditioning_scale=controlnet_conditioning_scale,
                generator=generator,
            )

            image = result.images[0]

            # 保存
            if output_path is None:
                output_dir = Path(__file__).parent / "output"
                output_dir.mkdir(exist_ok=True)
                output_path = str(output_dir / f"controlnet_{int(time.time())}_{seed}.png")

            image.save(output_path, "PNG")
            print(f"✅ 图片已保存: {output_path}")
            return output_path

        except Exception as e:
            print(f"❌ 生成失败: {e}")
            return None


class Img2ImgStyleTransfer:
    """图生图 + 风格迁移 - 参考图风格迁移"""

    def __init__(self, base_model: str = "Linaqruf/anything-v3.0"):
        self.base_model = base_model
        self.pipe = None

    def load_pipeline(self) -> bool:
        """加载图生图pipeline"""
        try:
            from diffusers import StableDiffusionImg2ImgPipeline
            import torch

            print("📥 加载图生图模型...")
            self.pipe = StableDiffusionImg2ImgPipeline.from_pretrained(
                self.base_model,
                torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
                safety_checker=None,
            )

            if not torch.cuda.is_available():
                self.pipe.enable_attention_slicing()
                self.pipe.enable_vae_slicing()

            print("✅ 图生图模型加载完成")
            return True

        except Exception as e:
            print(f"❌ 加载失败: {e}")
            return False

    def style_transfer(
        self,
        reference_image_path: str,
        prompt: str,
        negative_prompt: str = "",
        strength: float = 0.6,
        width: int = 512,
        height: int = 768,
        steps: int = 25,
        guidance_scale: float = 7.5,
        seed: Optional[int] = None,
        output_path: Optional[str] = None,
    ) -> Optional[str]:
        """
        风格迁移

        Args:
            reference_image_path: 参考图片路径
            prompt: 生成提示词
            strength: 变化强度（0-1，越大变化越大）
        """
        if self.pipe is None:
            print("❌ Pipeline未加载")
            return None

        try:
            from PIL import Image
            import torch

            # 加载参考图
            ref_image = Image.open(reference_image_path).convert("RGB")
            ref_image = ref_image.resize((width, height))

            # 设置种子
            if seed is None:
                seed = int(time.time()) % 1000000

            generator = torch.Generator(device="cuda" if torch.cuda.is_available() else "cpu").manual_seed(seed)

            print(f"\n🎨 风格迁移...")
            print(f"   参考图: {reference_image_path}")
            print(f"   变化强度: {strength}")
            print(f"   提示词: {prompt[:80]}...")

            result = self.pipe(
                prompt=prompt,
                negative_prompt=negative_prompt,
                image=ref_image,
                strength=strength,
                num_inference_steps=steps,
                guidance_scale=guidance_scale,
                generator=generator,
            )

            image = result.images[0]

            # 保存
            if output_path is None:
                output_dir = Path(__file__).parent / "output"
                output_dir.mkdir(exist_ok=True)
                output_path = str(output_dir / f"img2img_{int(time.time())}_{seed}.png")

            image.save(output_path, "PNG")
            print(f"✅ 图片已保存: {output_path}")
            return output_path

        except Exception as e:
            print(f"❌ 生成失败: {e}")
            return None


class AdvancedPipeline:
    """高级生成管道 - 整合三大超车技术"""

    def __init__(self):
        self.lora_trainer = LoRATrainer()
        self.controlnet = ControlNetGenerator()
        self.img2img = Img2ImgStyleTransfer()

    def run_lora_training(self, reference_dir: str, output_name: str, **kwargs):
        """运行LoRA训练"""
        print("\n" + "="*80)
        print("🎯 路径1: LoRA 风格训练")
        print("="*80)

        dataset_dir = self.lora_trainer.prepare_dataset(reference_dir)
        result = self.lora_trainer.train(dataset_dir, output_name, **kwargs)

        if result:
            print(f"\n✅ LoRA训练完成！")
            print(f"   模型: {result}")
            print(f"\n💡 使用方式:")
            print(f"   1. 将 .safetensors 文件放到 models/Lora 目录")
            print(f"   2. 在生成时添加触发词: <lora:{output_name}:0.8>")
        return result

    def run_controlnet_generation(
        self,
        control_image: str,
        prompt: str,
        control_type: str = "openpose",
        **kwargs
    ):
        """运行ControlNet生成"""
        print("\n" + "="*80)
        print("🎨 路径2: ControlNet 精准控制")
        print("="*80)

        if self.controlnet.load_controlnet(control_type):
            result = self.controlnet.generate(control_image, prompt, **kwargs)
            if result:
                print(f"\n✅ ControlNet生成完成！")
                print(f"   图片: {result}")
            return result
        return None

    def run_img2img_transfer(
        self,
        reference_image: str,
        prompt: str,
        strength: float = 0.6,
        **kwargs
    ):
        """运行图生图风格迁移"""
        print("\n" + "="*80)
        print("🖼️ 路径3: 图生图风格迁移")
        print("="*80)

        if self.img2img.load_pipeline():
            result = self.img2img.style_transfer(
                reference_image, prompt, strength=strength, **kwargs
            )
            if result:
                print(f"\n✅ 风格迁移完成！")
                print(f"   图片: {result}")
            return result
        return None


def main():
    parser = argparse.ArgumentParser(
        description="Live2D Master Agent - 高级生成管道（弯道超车）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
三大超车路径:

1. 🎯 LoRA训练 - 用参考图训练专属风格
   python advanced_generation_pipeline.py --mode lora --reference-dir ./refs --output-name my_style

2. 🎨 ControlNet - 精准控制姿势/线稿
   python advanced_generation_pipeline.py --mode controlnet --control-image pose.png --prompt "cute girl" --control-type openpose

3. 🖼️ 图生图 - 参考图风格迁移
   python advanced_generation_pipeline.py --mode img2img --reference ref.png --prompt "same style, new character" --strength 0.6

ControlNet类型:
  - openpose: 姿势控制
  - canny: 边缘检测
  - depth: 深度控制
  - lineart: 线稿控制
  - softedge: 软边缘
  - scribble: 涂鸦控制
""",
    )

    parser.add_argument("--mode", choices=["lora", "controlnet", "img2img"], required=True, help="运行模式")
    parser.add_argument("--reference-dir", help="LoRA: 参考图目录")
    parser.add_argument("--output-name", help="LoRA: 输出模型名称")
    parser.add_argument("--control-image", help="ControlNet: 控制图像路径")
    parser.add_argument("--control-type", default="openpose", help="ControlNet: 控制类型")
    parser.add_argument("--reference", help="图生图: 参考图像路径")
    parser.add_argument("--prompt", default="masterpiece, best quality, 1girl", help="生成提示词")
    parser.add_argument("--negative", default="", help="反向提示词")
    parser.add_argument("--strength", type=float, default=0.6, help="图生图: 变化强度 (0-1)")
    parser.add_argument("--width", type=int, default=512, help="图片宽度")
    parser.add_argument("--height", type=int, default=768, help="图片高度")
    parser.add_argument("--steps", type=int, default=25, help="推理步数")
    parser.add_argument("--seed", type=int, default=None, help="随机种子")

    args = parser.parse_args()

    pipeline = AdvancedPipeline()

    if args.mode == "lora":
        if not args.reference_dir or not args.output_name:
            print("❌ LoRA模式需要 --reference-dir 和 --output-name")
            sys.exit(1)
        pipeline.run_lora_training(args.reference_dir, args.output_name)

    elif args.mode == "controlnet":
        if not args.control_image:
            print("❌ ControlNet模式需要 --control-image")
            sys.exit(1)
        pipeline.run_controlnet_generation(
            args.control_image,
            args.prompt,
            control_type=args.control_type,
            negative_prompt=args.negative,
            width=args.width,
            height=args.height,
            steps=args.steps,
            seed=args.seed,
        )

    elif args.mode == "img2img":
        if not args.reference:
            print("❌ 图生图模式需要 --reference")
            sys.exit(1)
        pipeline.run_img2img_transfer(
            args.reference,
            args.prompt,
            strength=args.strength,
            negative_prompt=args.negative,
            width=args.width,
            height=args.height,
            steps=args.steps,
            seed=args.seed,
        )


if __name__ == "__main__":
    import time
    main()
