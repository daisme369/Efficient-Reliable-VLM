"""Merge the M1 (or B1) LoRA adapter into MedGemma's weights, so vLLM can serve one plain model.

Run on the GPU host, in the Linux CUDA environment (requirements/medgemma-cu128.txt):

    python scripts/gpu/merge_m1_adapter.py --config /private/runs/<run>/config.json \
        --adapter /private/runs/<run>/checkpoint --out /models/medgemma-m1

It applies the same checks as scripts/serve_vlm.py (config fingerprint, complete adapter, processor and chat
template), merges in bfloat16 (never on the 4-bit training base), and prints the served model name to pass to
`vllm serve --served-model-name` and to THORAX_VLM_MODEL, so every answer records the adapter it came from.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, required=True, help="the exact config the adapter was trained with")
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    import torch
    from peft import PeftModel
    from transformers import AutoModelForImageTextToText, AutoProcessor
    from cxr_research.experiments.checkpoints import verify_artifacts
    from cxr_research.experiments.config import load_config
    from cxr_research.utils.hashing import digest

    identity = json.loads((args.adapter / "model_identity.json").read_text(encoding="utf-8"))
    config = load_config(args.config)
    if config.fingerprint != identity["config_sha256"]:
        raise SystemExit("--config is not the config this adapter was trained with.")
    adapter_sha = digest(verify_artifacts(args.adapter))  # refuses a changed or incomplete adapter
    m = config.medgemma
    processor = AutoProcessor.from_pretrained(m.model_id, revision=m.revision, trust_remote_code=False)
    if digest(processor.to_dict()) != identity["processor_sha256"] or digest(processor.chat_template) != identity["chat_template_sha256"]:
        raise SystemExit("The processor or chat template differs from the one used in training.")
    base = AutoModelForImageTextToText.from_pretrained(m.model_id, revision=m.revision, dtype=torch.bfloat16, trust_remote_code=False)
    merged = PeftModel.from_pretrained(base, str(args.adapter), is_trainable=False).merge_and_unload()
    args.out.mkdir(parents=True, exist_ok=True)
    merged.save_pretrained(args.out, safe_serialization=True)
    processor.save_pretrained(args.out)  # vLLM applies this chat template, the one training used
    name = f"medgemma-{identity.get('objective', 'adapter').split('_')[0]}-{adapter_sha[:12]}"
    (args.out / "thorax_merge.json").write_text(json.dumps({
        "served_model_name": name, "base_model": m.model_id, "base_revision": m.revision, "adapter_sha256": adapter_sha,
        "config_sha256": identity["config_sha256"], "system_prompt": m.prompt, "max_new_tokens": m.max_new_tokens}, indent=2))
    print(f"Merged into {args.out}. Serve it with --served-model-name {name} and set THORAX_VLM_MODEL={name}.")


if __name__ == "__main__":
    main()
