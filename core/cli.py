#!/usr/bin/env python3
"""
Live2D Master Agent v10.0 - Interactive CLI

Usage:
    python -m core.cli                    # Interactive menu
    python -m core.cli generate "prompt"  # Quick generate
    python -m core.cli chat               # Chat with character
    python -m core.cli pet                # Run desktop pet
"""

import sys
import argparse
from pathlib import Path

from core.version import __version__, FULL_VERSION_STRING


def print_banner():
    """Print the startup banner."""
    print(f"""
╔══════════════════════════════════════════════════════════╗
║       🎭 Live2D Master Agent v{__version__:<24s}║
║       AI Character → Live2D Model → Desktop Pet        ║
╚══════════════════════════════════════════════════════════╝
""")


def interactive_menu():
    """Show interactive menu."""
    from core.logger import get_logger
    log = get_logger("cli")

    while True:
        print_banner()
        print("  1. 🎨 Generate character (AI image → layers → pet)")
        print("  2. 🖼️  Layer an existing image")
        print("  3. 🐱 Run desktop pet")
        print("  4. 💬 Chat with character (LLM)")
        print("  5. 📋 Character management")
        print("  6. 📦 Export Live2D model")
        print("  7. ⚙️  Settings / API keys")
        print("  8. 🔧 Start API server")
        print("  9. 📖 Documentation")
        print("  0. Exit")
        print()

        choice = input("Select an option [0-9]: ").strip()

        if choice == "1":
            prompt = input("\n✏️  Character description: ").strip()
            if prompt:
                _generate(prompt)
        elif choice == "2":
            path = input("\n📁 Image path: ").strip()
            if path:
                _layer_image(path)
        elif choice == "3":
            _run_pet()
        elif choice == "4":
            _chat()
        elif choice == "5":
            _manage_characters()
        elif choice == "6":
            _export_model()
        elif choice == "7":
            _settings()
        elif choice == "8":
            _start_api()
        elif choice == "9":
            _show_docs()
        elif choice == "0":
            print("\n👋 Goodbye!")
            break
        else:
            print("\n❌ Invalid choice")

        if choice != "0":
            input("\nPress Enter to continue...")


def _generate(
    prompt: str,
    output_dir: str = "./output",
    provider: str = "auto",
    deploy_desktop: bool = False,
    use_semantic: bool = True,
    k_clusters: int = 12,
):
    """Run the full generation pipeline."""
    from core.workflow import WorkflowEngine

    print(f"\n🎨 Generating: {prompt}")
    print("-" * 50)

    engine = WorkflowEngine(
        output_dir=output_dir,
        k_clusters=k_clusters,
        provider=None if provider == "auto" else provider,
        use_semantic_segmentation=use_semantic,
        export_live2d=True,
    )
    result = engine.run(
        prompt=prompt,
        deploy_desktop=deploy_desktop,
    )

    if result["success"]:
        print(f"\n✅ Done! Output: {result.get('output_dir', 'output/')}")
        if "character_image" in result:
            print(f"   Image: {result['character_image']}")
        if "layers_dir" in result:
            print(f"   Layers: {result['layers_dir']}")
        if "steps" in result and "rigging" in result["steps"]:
            print(f"   Model: {result['steps']['rigging'].get('model3_json', 'N/A')}")
        if "steps" in result and "psd" in result["steps"]:
            print(f"   PSD: {result['steps']['psd'].get('psd_path', 'N/A')}")
    else:
        print(f"\n❌ Failed: {result.get('error', 'Unknown error')}")
    return result


def _layer_image(image_path: str):
    """Layer an existing image."""
    from core.segment_engine.semantic import SemanticLayerer
    from PIL import Image

    path = Path(image_path)
    if not path.exists():
        print(f"❌ File not found: {image_path}")
        return

    print(f"\n🔍 Layering: {image_path}")
    img = Image.open(path).convert("RGBA")

    layerer = SemanticLayerer()
    result = layerer.layer(img, output_dir=f"./output/layers_{path.stem}")

    print(f"✅ Created {result['layer_count']} layers in {result['output_dir']}")


def _run_pet():
    """Run desktop pet."""
    print("\n🐱 Starting desktop pet...")
    try:
        from drivers.desktop_pet.runner import PetRunner
        runner = PetRunner(layers_dir="./output")
        runner.run()
    except ImportError:
        print("❌ Desktop pet requires pygame. Install with: pip install pygame")
    except Exception as e:
        print(f"❌ Error: {e}")


def _chat():
    """Chat with character."""
    print("\n💬 AI Chat (type 'quit' to exit)")
    print("-" * 40)

    try:
        import asyncio
        from llm_bridge.chat_session import ChatSession
        from llm_bridge.providers.router import LLMRouter
        from llm_bridge.emotion.analyzer import EmotionAnalyzer

        router = LLMRouter()
        emotion = EmotionAnalyzer(use_llm=False)
        session = ChatSession(llm_router=router, tts=None, asr=None, emotion=emotion)

        while True:
            text = input("\nYou: ").strip()
            if text.lower() in ("quit", "exit", "q"):
                break
            if not text:
                continue

            print("\nCharacter: ", end="", flush=True)
            async def _send():
                async for chunk in session.send_message(text):
                    if "text" in chunk:
                        print(chunk["text"], end="", flush=True)
                    if "emotion" in chunk:
                        print(f" [{chunk['emotion']}]", end="")
                print()
            asyncio.run(_send())
    except ImportError as e:
        print(f"❌ Chat requires optional dependencies: {e}")
    except Exception as e:
        print(f"❌ Error: {e}")


def _manage_characters():
    """Character management."""
    from core.character.manager import CharacterManager
    manager = CharacterManager()

    characters = manager.list_characters()
    print(f"\n📋 Characters ({len(characters)}):")
    for c in characters:
        print(f"  • {c.get('name', 'Unknown')} ({c.get('character_id', '?')[:8]}...)")

    if not characters:
        print("  (no characters yet)")

    action = input("\nCreate new? [y/N]: ").strip().lower()
    if action == "y":
        name = input("Name: ").strip()
        desc = input("Description/persona: ").strip()
        card = manager.create_character(name=name, personality=desc)
        print(f"✅ Created: {card.character_id}")


def _export_model():
    """Export Live2D model."""
    print("\n📦 Live2D model export")
    print("This exports a model3.json package from previously generated layers.")
    print("Use: python -m core.workflow <prompt> to generate first.")


def _settings():
    """Settings menu."""
    from core.config import config
    print(f"\n⚙️  Settings:")
    print(f"  Version: {config.version}")
    print(f"  Output:  {config.output_dir}")
    print(f"  ARK Key: {'***' + config.ark_api_key[-4:] if config.ark_api_key else 'NOT SET'}")
    print(f"  SenseNova Key: {'***' if config.sensenova_api_key else 'NOT SET'}")
    print()
    print("  Edit .env file to configure API keys")


def _start_api():
    """Start the Go API server."""
    import subprocess
    api_bin = Path("api/live2d-api")
    if not api_bin.exists():
        api_bin = Path("api/live2d-api.exe")
    if api_bin.exists():
        print("\n🔧 Starting API server on :8080...")
        subprocess.run([str(api_bin)], cwd="api")
    else:
        print("\n❌ API binary not found. Build it first:")
        print("   cd api && go build -o live2d-api .")


def _show_docs():
    """Show documentation links."""
    print("""
📖 Documentation:
  README.md              - Project overview
  docs/QUICKSTART.md     - Quick start guide
  docs/USER_GUIDE.md     - Full user guide
  docs/FAQ.md            - Frequently asked questions
  docs/LIMITATIONS.md    - Known limitations
""")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description=f"Live2D Master Agent v{__version__}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")

    # generate
    gen = sub.add_parser("generate", help="Generate a character")
    gen.add_argument("prompt", help="Character description")
    gen.add_argument("--output", "-o", default="./output")
    gen.add_argument("--provider", default="auto",
                     choices=["pollinations", "sensenova", "seedream", "auto"])
    gen.add_argument("--deploy-desktop", action="store_true")
    gen.add_argument("--no-semantic", action="store_true")
    gen.add_argument("--k", type=int, default=12, help="K-means clusters (default: 12)")
    gen.add_argument("--negative-prompt", "-n", default=None, help="Negative prompt")
    gen.add_argument("--seed", type=int, default=None, help="Random seed")

    # pet
    sub.add_parser("pet", help="Run desktop pet")

    # chat
    sub.add_parser("chat", help="Chat with character")

    # serve
    serve = sub.add_parser("serve", help="Start API server")
    serve.add_argument("--port", type=int, default=8080)

    # version
    sub.add_parser("version", help="Show version")

    args = parser.parse_args()

    if args.command == "generate":
        print_banner()
        _generate(
            prompt=args.prompt,
            output_dir=args.output,
            provider=args.provider,
            deploy_desktop=args.deploy_desktop,
            use_semantic=not args.no_semantic,
            k_clusters=args.k,
        )
    elif args.command == "pet":
        _run_pet()
    elif args.command == "chat":
        print_banner()
        _chat()
    elif args.command == "serve":
        _start_api()
    elif args.command == "version":
        print(FULL_VERSION_STRING)
    else:
        interactive_menu()


if __name__ == "__main__":
    main()
