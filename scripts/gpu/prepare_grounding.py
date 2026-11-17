"""Rebuild the private development grounding/context bundle from pinned releases."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys

from dataset_sync import ROOT, private_path
from grounding.export_review import export_review, read, sha
from grounding.qa_regions import build_candidates
from grounding.source_index import audit_grounding_sources
from grounding.verify_review import verify
from cxr_research.utils.errors import ContractError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--base-manifest-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    if not __debug__:
        parser.error("run without Python optimization to preserve integrity checks")
    os.umask(0o077)
    root, catalog, base, out = map(private_path, (
        args.data_root, args.catalog, args.base_manifest_dir, args.output))
    if not all(p != root and p.is_relative_to(root) for p in (catalog, base, out)):
        raise ContractError("catalog, base manifest and output must be under the private data root")
    if out.exists() != args.verify_only:
        raise ContractError("build needs a fresh output directory; verification needs an existing output")
    scratch = root / "manifests" / (out.name + "-grounding-index")
    if args.verify_only:
        verify(root, out, scratch / "mapping.sqlite")
        return

    pinned = sha(catalog / "report.json")
    if read(base / "provenance.json").get("catalog_report_sha256") != pinned:
        raise ContractError("base VQA manifest must be prepared from this catalog")
    if scratch.exists():
        raise ContractError("grounding index already exists; use a new output name")
    scratch.parent.mkdir(exist_ok=True)
    audit_grounding_sources(root, catalog, scratch, ROOT, pinned)
    gate = scratch / "qa-regions"
    build_candidates(root, catalog, scratch / "mapping.sqlite", gate, read(catalog / "report.json"))
    out.mkdir(parents=True)
    export_review(root, base, scratch, gate, out)


if __name__ == "__main__":
    try:
        main()
    except (AssertionError, ContractError, OSError, ValueError, KeyError, sqlite3.Error) as exc:
        print(f"Grounding preparation failed ({type(exc).__name__}). Check the pinned sources, "
              "private paths, checksums, and prior completion locally.", file=sys.stderr)
        sys.exit(1)
