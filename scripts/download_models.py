#!/usr/bin/env python3
"""
Download AI models for Live2D Master Agent.
Usage:
    python scripts/download_models.py --model sam       # SAM for segmentation
    python scripts/download_models.py --model rembg      # rembg background removal
    python scripts/download_models.py --model all        # All models
"""

import os
import sys
import argparse
from pathlib import Path


PROJECT_ROOT = Path(__file__).parent.parent.resolve()
MODELS_DIR = PROJECT_ROOT / "assets" / "models"


def download_rembg():
    """Download rembg background removal model."""
    print("📦 Downloading rembg model...")
    try:
        from rembg import new_session
        session = new_session("u2net")
        print("✓ rembg model ready")
    except Exception as e:
        print(f"⚠ rembg download failed: {e}")
        print("  Will use fallback background removal")


def download_sam():
    """Download SAM model for segmentation."""
    print("📦 Downloading SAM ViT-H model (2.4 GB)...")
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    sam_path = MODELS_DIR / "sam_vit_h_4b8939.pth"

    if sam_path.exists():
        print(f"✓ SAM model already exists at {sam_path}")
        return

    try:
        import urllib.request
        url = "https://dl.fbaipublicfiles.com/segment_anything/sam_vit_h_4b8939.pth"
        print(f"  Downloading from {url}...")
        urllib.request.urlretrieve(url, str(sam_path))
        print(f"✓ SAM model saved to {sam_path}")
    except Exception as e:
        print(f"⚠ SAM download failed: {e}")
        print("  SAM-based segmentation will fall back to K-means")
        print("  You can manually download from:")
        print(f"  {url}")
        print(f"  Save to: {sam_path}")


def download_isnet():
    """Download ISNet anime segmentation model."""
    print("📦 Downloading ISNet anime-segmentation model...")
    try:
        from huggingface_hub import hf_hub_download
        path = hf_hub_download(
            repo_id="skytnt/anime-segmentation",
            filename="isnetis.onnx",
            local_dir=str(MODELS_DIR / "isnet"),
        )
        print(f"✓ ISNet model saved to {path}")
    except ImportError:
        print("⚠ huggingface_hub not installed, skipping ISNet")
        print("  Install with: pip install huggingface-hub")
    except Exception as e:
        print(f"⚠ ISNet download failed: {e}")


def download_clip():
    """Download CLIP model for character embeddings."""
    print("📦 Downloading CLIP model (for character consistency)...")
    try:
        import torch
        from transformers import CLIPModel, CLIPProcessor
        model = CLIPModel.from_pretrained("openai/clip-vit-base-patch32")
        processor = CLIPProcessor.from_pretrained("openai/clip-vit-base-patch32")
        print("✓ CLIP model ready")
    except ImportError:
        print("⚠ torch/transformers not installed, skipping CLIP")
        print("  Install with: pip install torch transformers")
    except Exception as e:
        print(f"⚠ CLIP download failed: {e}")


def main():
    parser = argparse.ArgumentParser(description="Download AI models")
    parser.add_argument("--model", choices=["sam", "rembg", "isnet", "clip", "all"],
                       default="all", help="Which model to download")
    args = parser.parse_args()

    print(f"🎭 Live2D Master Agent - Model Downloader\n")
    print(f"Models directory: {MODELS_DIR}\n")

    if args.model in ("sam", "all"):
        download_sam()
        print()
    if args.model in ("rembg", "all"):
        download_rembg()
        print()
    if args.model in ("isnet", "all"):
        download_isnet()
        print()
    if args.model in ("clip", "all"):
        download_clip()
        print()

    print("\n✅ Done!")


if __name__ == "__main__":
    main()
