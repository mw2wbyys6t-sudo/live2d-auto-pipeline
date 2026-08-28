#!/usr/bin/env python3
"""
Live2D 图片后处理工具
功能：
- 轮廓增强（Edge Enhancement）
- 背景处理（Background Processing）
- 颜色量化（Color Quantization）
- Live2D 兼容性检查
"""

import sys
from pathlib import Path
from PIL import Image, ImageFilter, ImageEnhance, ImageOps
import numpy as np


def enhance_edges(image_path, output_path=None):
    """
    增强图片轮廓，使其更适合 Live2D 分层
    
    Args:
        image_path: 输入图片路径
        output_path: 输出图片路径（可选）
    
    Returns:
        处理后的图片路径
    """
    print(f"🎨 正在增强轮廓: {Path(image_path).name}")
    
    img = Image.open(image_path).convert("RGBA")
    
    # 1. 提取边缘
    edges = img.filter(ImageFilter.FIND_EDGES)
    
    # 2. 增强对比度
    enhancer = ImageEnhance.Contrast(img)
    img_enhanced = enhancer.enhance(1.3)
    
    # 3. 增强锐度
    sharpener = ImageEnhance.Sharpness(img_enhanced)
    img_sharp = sharpener.enhance(1.5)
    
    # 4. 颜色量化（减少颜色数量，便于分层）
    img_quantized = img_sharp.quantize(colors=64).convert("RGBA")
    
    # 5. 保存结果
    if output_path is None:
        output_path = str(Path(image_path).with_suffix('')) + "_live2d_ready.png"
    
    img_quantized.save(output_path, "PNG")
    print(f"✅ 轮廓增强完成: {Path(output_path).name}")
    
    return output_path


def process_background(image_path, output_path=None, bg_color=(255, 255, 255, 255)):
    """
    处理背景，确保为纯色背景
    
    Args:
        image_path: 输入图片路径
        output_path: 输出图片路径（可选）
        bg_color: 背景颜色（默认白色）
    
    Returns:
        处理后的图片路径
    """
    print(f"🎨 正在处理背景: {Path(image_path).name}")
    
    img = Image.open(image_path).convert("RGBA")
    width, height = img.size
    
    # 创建新背景
    background = Image.new("RGBA", (width, height), bg_color)
    
    # 将原图粘贴到背景上（保留透明通道）
    background.paste(img, (0, 0), img)
    
    # 保存结果
    if output_path is None:
        output_path = str(Path(image_path).with_suffix('')) + "_white_bg.png"
    
    background.save(output_path, "PNG")
    print(f"✅ 背景处理完成: {Path(output_path).name}")
    
    return output_path


def check_live2d_compatibility(image_path):
    """
    检查图片是否适合 Live2D 制作
    
    Args:
        image_path: 图片路径
    
    Returns:
        兼容性评分和建议
    """
    print(f"🔍 正在检查 Live2D 兼容性: {Path(image_path).name}")
    
    img = Image.open(image_path).convert("RGBA")
    width, height = img.size
    
    issues = []
    score = 100
    
    # 1. 检查尺寸
    if width < 512 or height < 512:
        issues.append("⚠️ 图片尺寸过小，建议至少 512x512")
        score -= 10
    elif width < 1024 or height < 1024:
        issues.append("💡 图片尺寸中等，建议 1024x1024 或更高")
        score -= 5
    else:
        print("✅ 图片尺寸合适")
    
    # 2. 检查背景
    pixels = list(img.getdata())
    bg_pixels = [p for p in pixels if p[3] < 128]  # 半透明或透明像素
    
    if len(bg_pixels) > 0:
        print("✅ 图片有透明背景")
    else:
        # 检查是否有纯色背景
        corner_colors = [
            pixels[0],                    # 左上角
            pixels[width - 1],            # 右上角
            pixels[width * (height - 1)], # 左下角
            pixels[-1]                    # 右下角
        ]
        
        if len(set(corner_colors)) == 1:
            print("✅ 图片有纯色背景")
        else:
            issues.append("⚠️ 背景复杂，建议处理为纯色背景")
            score -= 15
    
    # 3. 检查颜色数量
    unique_colors = len(set(pixels))
    if unique_colors > 10000:
        issues.append("⚠️ 颜色数量过多，建议进行颜色量化")
        score -= 10
    else:
        print(f"✅ 颜色数量适中: {unique_colors}")
    
    # 4. 检查对比度
    grayscale = img.convert("L")
    pixels_gray = list(grayscale.getdata())
    contrast = max(pixels_gray) - min(pixels_gray)
    
    if contrast < 50:
        issues.append("⚠️ 对比度较低，建议增强")
        score -= 10
    else:
        print(f"✅ 对比度良好: {contrast}")
    
    # 5. 检查是否全身
    if height > width * 1.5:
        print("✅ 图片比例适合全身像")
    else:
        issues.append("💡 图片比例偏宽，可能不是全身像")
        score -= 5
    
    # 输出结果
    print(f"\n📊 Live2D 兼容性评分: {score}/100")
    
    if score >= 80:
        print("🟢 非常适合 Live2D 制作")
    elif score >= 60:
        print("🟡 适合 Live2D 制作，但有改进空间")
    else:
        print("🔴 不太适合 Live2D 制作，建议优化")
    
    if issues:
        print(f"\n💡 改进建议:")
        for issue in issues:
            print(f"   {issue}")
    
    return score, issues


def auto_optimize_for_live2d(image_path, output_dir=None):
    """
    自动优化图片，使其更适合 Live2D
    
    Args:
        image_path: 输入图片路径
        output_dir: 输出目录（可选）
    
    Returns:
        优化后的图片路径
    """
    print(f"\n🚀 开始自动优化: {Path(image_path).name}")
    print("=" * 60)
    
    if output_dir is None:
        output_dir = Path(image_path).parent
    else:
        output_dir = Path(output_dir)
        output_dir.mkdir(exist_ok=True)
    
    base_name = Path(image_path).stem
    
    # 1. 检查兼容性
    score, issues = check_live2d_compatibility(image_path)
    
    # 2. 处理背景
    bg_path = output_dir / f"{base_name}_white_bg.png"
    process_background(image_path, str(bg_path))
    
    # 3. 增强轮廓
    final_path = output_dir / f"{base_name}_live2d_optimized.png"
    enhance_edges(str(bg_path), str(final_path))
    
    # 4. 清理临时文件
    bg_path.unlink(missing_ok=True)
    
    print(f"\n✅ 优化完成!")
    print(f"📁 输出文件: {final_path}")
    print(f"💡 建议: 使用此文件进行 Live2D 制作")
    
    return str(final_path)


def main():
    """主函数"""
    if len(sys.argv) < 2:
        print("Usage: python live2d_image_processor.py <image_path> [output_dir]")
        print("\n功能:")
        print("  1. 检查 Live2D 兼容性")
        print("  2. 处理背景为白色")
        print("  3. 增强轮廓和颜色")
        print("  4. 输出优化后的图片")
        sys.exit(1)
    
    image_path = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else None
    
    if not Path(image_path).exists():
        print(f"❌ 文件不存在: {image_path}")
        sys.exit(1)
    
    auto_optimize_for_live2d(image_path, output_dir)


if __name__ == "__main__":
    main()
