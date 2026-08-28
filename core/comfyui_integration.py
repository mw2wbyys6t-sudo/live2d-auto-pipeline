#!/usr/bin/env python3
"""
Live2D Master Agent - ComfyUI 集成工具
版本: 1.0
功能: 连接 ComfyUI 和 Live2D 工作流
"""

import os
import sys
import json
import asyncio
import requests
from pathlib import Path
from typing import Dict, Any, Optional, List


class Live2DComfyUI:
    """Live2D 和 ComfyUI 集成工具"""
    
    def __init__(self, comfyui_url: str = "http://127.0.0.1:8188"):
        self.comfyui_url = comfyui_url.rstrip('/')
        self.output_dir = Path.cwd() / "output"
        self.output_dir.mkdir(exist_ok=True)
        
    def print_header(self):
        """打印标题"""
        print()
        print("=" * 60)
        print("🎨 Live2D Master Agent - ComfyUI 集成工具")
        print("=" * 60)
        print()
    
    def print_success(self, message: str):
        """打印成功消息"""
        print(f"✅ {message}")
    
    def print_warning(self, message: str):
        """打印警告消息"""
        print(f"⚠️ {message}")
    
    def print_error(self, message: str):
        """打印错误消息"""
        print(f"❌ {message}")
    
    def print_info(self, message: str):
        """打印信息"""
        print(f"ℹ️ {message}")
    
    def check_connection(self) -> bool:
        """检查 ComfyUI 连接"""
        self.print_info(f"检查 ComfyUI 连接: {self.comfyui_url}")
        try:
            response = requests.get(f"{self.comfyui_url}/system_stats", timeout=5)
            if response.status_code == 200:
                self.print_success("ComfyUI 连接成功！")
                return True
            else:
                self.print_error(f"ComfyUI 连接失败: {response.status_code}")
                return False
        except Exception as e:
            self.print_error(f"无法连接到 ComfyUI: {e}")
            self.print_warning("请确保 ComfyUI 正在运行")
            return False
    
    def load_workflow(self, workflow_file: str) -> Optional[Dict[str, Any]]:
        """加载工作流文件"""
        try:
            workflow_path = Path(__file__).parent / workflow_file
            if workflow_path.exists():
                with open(workflow_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            else:
                self.print_error(f"工作流文件不存在: {workflow_file}")
                return None
        except Exception as e:
            self.print_error(f"加载工作流失败: {e}")
            return None
    
    def get_prompt_template(self, preset: str = "Cute Kawaii") -> Optional[Dict[str, str]]:
        """获取提示词模板"""
        templates = {
            "Cute Kawaii": {
                "positive": "anime girl, cute kawaii style, beautiful face, big expressive eyes, long flowing pink hair, soft pink gradient hair, hair strands detailed, wearing JK school uniform, white blouse, navy blue pleated skirt, red ribbon tie, slender figure, elegant pose, standing pose, perfect for Live2D rigging, clean layer separation, isolated character on white background, easy to rig, sharp clean lines, vibrant colors, ultra detailed, masterpiece, award-winning quality, professional artwork, 4K resolution, high quality render, anime art style, soft lighting, detailed facial features, sparkling eyes",
                "negative": "blurry, low quality, bad anatomy, bad hands, multiple characters, complex background, merged layers, overlapping parts, extra fingers, mutated, deformed, disfigured, lowres, text, watermark, signature, logo, worst quality, low quality, normal quality, jpeg artifacts, blurry, out of focus"
            },
            "Cool Tomboy": {
                "positive": "anime girl, cool style, tomboy, sharp features, confident expression, dark colors, dynamic pose, perfect for Live2D rigging, clean layer separation, isolated character on white background",
                "negative": "blurry, low quality, bad anatomy"
            },
            "Elegant Refined": {
                "positive": "anime girl, elegant, refined, detailed features, graceful pose, vibrant colors, high fashion, perfect for Live2D rigging",
                "negative": "blurry, low quality, bad anatomy"
            },
            "Magical Fantasy": {
                "positive": "anime girl, magical girl, fantasy, sparkles, magical elements, ethereal, mystical atmosphere, perfect for Live2D rigging",
                "negative": "blurry, low quality, bad anatomy"
            }
        }
        
        if preset in templates:
            return templates[preset]
        else:
            self.print_error(f"未知的预设: {preset}")
            return None
    
    def generate_character(
        self,
        positive_prompt: str,
        negative_prompt: str,
        width: int = 2048,
        height: int = 2048,
        seed: int = -1,
        steps: int = 30
    ) -> Optional[str]:
        """生成角色立绘"""
        
        self.print_info("准备生成角色立绘...")
        
        # 构建 ComfyUI 提示
        prompt = {
            "1": {
                "inputs": {
                    "seed": seed,
                    "steps": steps,
                    "cfg": 7.0,
                    "sampler_name": "euler",
                    "scheduler": "normal",
                    "positive": ["7", 0],
                    "negative": ["8", 0],
                    "latent_image": ["16", 0]
                },
                "class_type": "KSampler"
            },
            "7": {
                "inputs": {
                    "text": positive_prompt,
                    "clip": ["18", 1]
                },
                "class_type": "CLIPTextEncode"
            },
            "8": {
                "inputs": {
                    "text": negative_prompt,
                    "clip": ["18", 1]
                },
                "class_type": "CLIPTextEncode"
            },
            "16": {
                "inputs": {
                    "width": width,
                    "height": height,
                    "batch_size": 1
                },
                "class_type": "EmptyLatentImage"
            },
            "18": {
                "inputs": {
                    "ckpt_name": "AnythingV5.safetensors"
                },
                "class_type": "CheckpointLoaderSimple"
            },
            "9": {
                "inputs": {
                    "vae": ["10", 0],
                    "samples": ["1", 0]
                },
                "class_type": "VAEDecode"
            },
            "10": {
                "inputs": {
                    "vae_name": "auto"
                },
                "class_type": "VAELoader"
            },
            "11": {
                "inputs": {
                    "filename_prefix": "Live2D_Character",
                    "images": ["9", 0]
                },
                "class_type": "SaveImage"
            }
        }
        
        try:
            self.print_info("发送生成请求到 ComfyUI...")
            response = requests.post(
                f"{self.comfyui_url}/prompt",
                json={"prompt": prompt},
                timeout=300
            )
            
            if response.status_code != 200:
                self.print_error(f"请求失败: {response.status_code}")
                self.print_error(response.text)
                return None
            
            result = response.json()
            prompt_id = result.get("prompt_id")
            
            if prompt_id:
                self.print_success(f"请求已提交，Prompt ID: {prompt_id}")
                self.print_info("正在生成，请稍候...")
                
                # 等待生成完成（简单实现）
                import time
                time.sleep(30)  # 初始等待
                
                # 获取结果图片
                return self._get_generated_image()
            else:
                self.print_error("未收到 prompt_id")
                return None
                
        except Exception as e:
            self.print_error(f"生成失败: {e}")
            return None
    
    def _get_generated_image(self) -> Optional[str]:
        """获取生成的图片"""
        try:
            # 获取历史记录
            response = requests.get(
                f"{self.comfyui_url}/history",
                timeout=30
            )
            
            if response.status_code == 200:
                history = response.json()
                
                # 获取最新的生成结果
                if history:
                    # 假设最新的是第一个
                    for key in reversed(list(history.keys())):
                        item = history[key]
                        outputs = item.get("outputs", {})
                        
                        for node_id, node_output in outputs.items():
                            if "images" in node_output:
                                images = node_output["images"]
                                if images:
                                    # 下载第一张图片
                                    image_info = images[0]
                                    filename = image_info["filename"]
                                    subfolder = image_info.get("subfolder", "")
                                    
                                    self.print_success(f"找到图片: {filename}")
                                    
                                    # 下载图片
                                    download_url = f"{self.comfyui_url}/view"
                                    params = {
                                        "filename": filename,
                                        "subfolder": subfolder,
                                        "type": "output"
                                    }
                                    
                                    download_response = requests.get(
                                        download_url,
                                        params=params,
                                        timeout=60
                                    )
                                    
                                    if download_response.status_code == 200:
                                        output_file = self.output_dir / filename
                                        with open(output_file, 'wb') as f:
                                            f.write(download_response.content)
                                        
                                        self.print_success(f"图片已保存: {output_file}")
                                        return str(output_file)
                                    
            self.print_error("未找到生成的图片")
            return None
            
        except Exception as e:
            self.print_error(f"获取图片失败: {e}")
            return None
    
    def generate_with_preset(self, preset_name: str = "Cute Kawaii") -> Optional[str]:
        """使用预设生成"""
        template = self.get_prompt_template(preset_name)
        if not template:
            return None
        
        self.print_info(f"使用预设: {preset_name}")
        
        return self.generate_character(
            positive_prompt=template["positive"],
            negative_prompt=template["negative"]
        )
    
    def show_presets(self):
        """显示可用预设"""
        presets = [
            "Cute Kawaii",
            "Cool Tomboy", 
            "Elegant Refined",
            "Magical Fantasy"
        ]
        
        print()
        self.print_info("可用预设:")
        for i, preset in enumerate(presets, 1):
            print(f"  {i}. {preset}")
        print()
    
    def interactive_mode(self):
        """交互模式"""
        self.print_header()
        
        print("请选择一个选项:")
        print()
        print("  1. 使用预设生成角色")
        print("  2. 自定义生成")
        print("  3. 查看可用预设")
        print("  4. 检查连接")
        print("  0. 退出")
        print()
        
        choice = input("请输入选项 (0-4): ").strip()
        
        if choice == "1":
            self.show_presets()
            preset_choice = input("选择预设 (1-4): ").strip()
            
            presets = [
                "Cute Kawaii",
                "Cool Tomboy", 
                "Elegant Refined",
                "Magical Fantasy"
            ]
            
            if preset_choice.isdigit():
                idx = int(preset_choice) - 1
                if 0 <= idx < len(presets):
                    self.generate_with_preset(presets[idx])
                else:
                    self.print_error("无效的选择")
            
        elif choice == "2":
            positive = input("正向提示词: ").strip()
            negative = input("负向提示词 (可选，按回车跳过): ").strip()
            
            if not negative:
                negative = "blurry, low quality, bad anatomy"
            
            self.generate_character(positive, negative)
            
        elif choice == "3":
            self.show_presets()
            
        elif choice == "4":
            self.check_connection()
            
        elif choice == "0":
            print("再见！")
            return
            
        else:
            self.print_error("无效的选择")


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Live2D Master Agent - ComfyUI 集成工具"
    )
    parser.add_argument(
        "--url", 
        type=str, 
        default="http://127.0.0.1:8188",
        help="ComfyUI 地址 (默认: http://127.0.0.1:8188)"
    )
    parser.add_argument(
        "--preset",
        type=str,
        help="使用预设生成 (Cute Kawaii, Cool Tomboy, Elegant Refined, Magical Fantasy)"
    )
    
    args = parser.parse_args()
    
    app = Live2DComfyUI(comfyui_url=args.url)
    
    if args.preset:
        if app.check_connection():
            app.generate_with_preset(args.preset)
    else:
        app.interactive_mode()


if __name__ == "__main__":
    main()
