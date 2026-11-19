"""Download a SmolLM2 model (Apache-2.0, Llama architecture) into <dir>. usage: python tools/tpu_fetch_model.py [dir] [repo]   (default dir /home/user/data/smollm2, default repo HuggingFaceTB/SmolLM2-135M; e.g. HuggingFaceTB/SmolLM2-360M)   (needs `pip install huggingface_hub tokenizers safetensors`)"""
import sys

from huggingface_hub import snapshot_download

d = sys.argv[1] if len(sys.argv) > 1 else "/home/user/data/smollm2"
print(snapshot_download(sys.argv[2] if len(sys.argv) > 2 else "HuggingFaceTB/SmolLM2-135M", local_dir=d, allow_patterns=["*.json", "*.safetensors", "*.txt", "LICENSE*", "README.md"]))
