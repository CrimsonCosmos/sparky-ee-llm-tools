"""Download SmolLM2-135M (Apache-2.0) into <dir> (default /home/user/data/smollm2). usage: python tools/tpu_fetch_model.py [dir]   (needs `pip install huggingface_hub tokenizers safetensors`)"""
import sys

from huggingface_hub import snapshot_download

d = sys.argv[1] if len(sys.argv) > 1 else "/home/user/data/smollm2"
print(snapshot_download("HuggingFaceTB/SmolLM2-135M", local_dir=d, allow_patterns=["*.json", "*.safetensors", "*.txt", "LICENSE*", "README.md"]))
