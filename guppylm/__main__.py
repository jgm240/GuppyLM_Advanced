"""Entry point for: python -m guppylm"""

import os
import sys

CHECKPOINT_PATH = "checkpoints/best_model.pt"
TOKENIZER_PATH = "data/tokenizer.json"
HF_REPO = "arman-bd/guppylm-9M"
HF_BASE = f"https://huggingface.co/{HF_REPO}/resolve/main"


def download_model():
    """Download pre-trained GuppyLM from HuggingFace."""
    import urllib.request

    files = [
        (f"{HF_BASE}/pytorch_model.bin", CHECKPOINT_PATH),
        (f"{HF_BASE}/tokenizer.json", TOKENIZER_PATH),
        (f"{HF_BASE}/config.json", "checkpoints/config.json"),
    ]

    print(f"Downloading GuppyLM from {HF_REPO}...\n")
    for url, dest in files:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        name = os.path.basename(dest)
        print(f"  {name}...", end=" ", flush=True)
        urllib.request.urlretrieve(url, dest)
        size_mb = os.path.getsize(dest) / 1e6
        print(f"{size_mb:.1f} MB")

    print("\nDone! Run: python -m guppylm chat")


def main():
    if len(sys.argv) < 2:
        print("GuppyLM — A tiny fish brain")
        print()
        print("Usage:")
        print("  python -m guppylm train        Train the model")
        print("  python -m guppylm prepare      Generate data & train tokenizer")
        print("  python -m guppylm context-data Generate multi-turn (context) training data")
        print("  python -m guppylm chat         Chat with Guppy")
        print("  python -m guppylm tank         Colored ASCII aquarium client (guppytank)")
        print("  python -m guppylm tank-gui     Smooth graphical aquarium (Qt)")
        print("  python -m guppylm download     Download pre-trained model from HuggingFace")
        return

    cmd = sys.argv[1]
    sys.argv = sys.argv[1:]

    if cmd == "prepare":
        from .prepare_data import prepare
        prepare()

    elif cmd == "train":
        from .train import train
        train(sys.argv[1:])

    elif cmd == "world-data":
        from .world import generate_world_data
        generate_world_data()

    elif cmd == "context-data":
        from .multiturn import generate_context_data
        generate_context_data()

    elif cmd == "download":
        download_model()

    elif cmd == "chat":
        if not os.path.exists(CHECKPOINT_PATH):
            print("Model not found. Download the pre-trained model first:\n")
            print("  python -m guppylm download\n")
            print("Or train your own:\n")
            print("  python -m guppylm prepare")
            print("  python -m guppylm train")
            return

        from .inference import main as inference_main
        inference_main()

    elif cmd == "tank-gui":
        from .guppytank_gui import main as gui_main
        gui_main(sys.argv[1:])

    elif cmd == "tank":
        from .guppytank import main as tank_main
        tank_main(sys.argv[1:])

    else:
        print(f"Unknown command: {cmd}")
        print("Run 'python -m guppylm' for usage.")


main()
