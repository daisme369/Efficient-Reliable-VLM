"""Verify the supplied adapter without importing torch or accessing the network."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "apps/api"), str(ROOT / "src")]

from thorax.inference.medgemma import AdapterManifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    manifest = AdapterManifest(args.manifest)
    print(json.dumps({"verified": True, "artifact": manifest.artifact, "base_model": manifest.base_model_id,
                      "base_revision": manifest.base_revision, "native_inference_tested": False}, indent=2))


if __name__ == "__main__":
    main()
