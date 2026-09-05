"""Report availability and versions of the project's Python dependencies."""

from __future__ import annotations

import importlib
from importlib import metadata


PACKAGES = {
    "pandas": "pandas",
    "pyarrow": "pyarrow",
    "numpy": "numpy",
    "tqdm": "tqdm",
    "datasets": "datasets",
    "transformers": "transformers",
    "torch": "torch",
    "accelerate": "accelerate",
    "bitsandbytes": "bitsandbytes",
    "scikit-learn": "sklearn",
    "flask": "flask",
    "requests": "requests",
    "huggingface-hub": "huggingface_hub",
    "sentencepiece": "sentencepiece",
    "protobuf": "google.protobuf",
    "peft": "peft",
}


def main() -> None:
    print("Python environment check")
    print("=" * 28)
    for package, module_name in PACKAGES.items():
        try:
            importlib.import_module(module_name)
            version = metadata.version(package)
            print(f"[OK]   {package}: {version}")
        except Exception as exc:
            print(f"[MISS] {package}: {exc.__class__.__name__}")

    try:
        import torch

        print(f"\nCUDA available: {torch.cuda.is_available()}")
        if torch.cuda.is_available():
            print(f"CUDA device: {torch.cuda.get_device_name(0)}")
    except Exception:
        print("\nCUDA status: unavailable (PyTorch is not installed)")


if __name__ == "__main__":
    main()