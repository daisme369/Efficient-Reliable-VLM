"""Plan and fetch only B1 metadata/selected development images from private S3."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from cxr_research.data.manifest import local_file
from cxr_research.utils.aws_env import load_aws_environment
from cxr_research.utils.errors import ContractError
from cxr_research.utils.hashing import file_hash
from cxr_research.utils.io import write_json

RELEASES = {"images": ("mimic-cxr-jpg", "2.1.0"),
            "qa": ("mimic-ext-mimic-cxr-vqa", "1.0.0")}
ALIASES = {"images": {"mimic_cxr_jpg_s3_uri", "mimic_cxr_jpg"},
           "qa": {"mimic_cxr_vqa_s3_uri", "mimic_cxr_vqa"}}


def private_path(path, repository=ROOT):
    path = Path(path)
    if not path.is_absolute() or path.resolve().is_relative_to(repository.resolve()):
        raise ContractError("S3 plans and datasets require absolute private paths outside the checkout")
    return path.resolve()


def destination(root, relative):
    if (not isinstance(relative, str) or "\\" in relative or ":" in relative
            or any(ord(c) < 32 for c in relative) or PurePosixPath(relative).is_absolute()
            or ".." in PurePosixPath(relative).parts):
        raise ContractError("transfer destinations require confined portable relative paths")
    return local_file(root, relative, must_exist=False)


def source_settings(env_file):
    """Accept URI fields or legacy aws s3 commands as DATA; never evaluate them."""
    values = {}
    for line in Path(env_file).read_text(encoding="utf-8-sig").splitlines():
        match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z_0-9]*)\s*=\s*(.*?)\s*$", line)
        if not match:
            continue
        key, value = match[1].lower(), match[2].strip()
        kind = next((k for k, aliases in ALIASES.items() if key in aliases), None)
        if kind is None:
            continue
        if kind in values:
            raise ContractError("duplicate dataset source field")
        if value.startswith(("'", '"')):
            quote = value[0]
            end = value.find(quote, 1)
            if end < 0 or (value[end + 1:].strip() and not value[end + 1:].strip().startswith("#")):
                raise ContractError("malformed dataset source field")
            value = value[1:end]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0]
        if value.startswith("aws "):
            tokens = shlex.split(value)
            if (tokens[:3] not in (["aws", "s3", "sync"], ["aws", "s3", "cp"])
                    or any(c in value for c in "`;|&\n\r") or "$(" in value):
                raise ContractError("unsupported legacy dataset source syntax")
            uris = [t for t in tokens[3:] if t.startswith("s3://")]
            if len(uris) != 1:
                raise ContractError("legacy dataset setting must contain exactly one S3 URI")
            value = uris[0]
        arn = re.fullmatch(r"s3://(arn:(?:aws|aws-cn|aws-us-gov):s3:[a-z0-9-]+:[0-9]{12}:accesspoint/[A-Za-z0-9-]+)(?:/(.*))?", value)
        uri = urlsplit(value)
        bucket = arn[1] if arn else uri.netloc
        source_path = (arn[2] or "") if arn else uri.path.lstrip("/")
        if (uri.scheme != "s3" or (not arn and not re.fullmatch(r"[A-Za-z0-9.-]+", uri.netloc))
                or uri.query or uri.fragment or ".." in PurePosixPath(uri.path).parts
                or "\\" in uri.path or any(ord(c) < 32 for c in value)):
            raise ContractError("dataset setting requires a plain S3 bucket/access-point-alias URI")
        slug, version = RELEASES[kind]
        prefix = source_path.rstrip("/")
        # Support a bucket root, dataset root, or exact pinned release root.
        if not prefix:
            prefix = f"{slug}/{version}"
        elif prefix.endswith(slug):
            prefix += f"/{version}"
        elif not prefix.endswith(f"{slug}/{version}"):
            raise ContractError("S3 source must identify the pinned dataset release")
        values[kind] = {"bucket": bucket, "prefix": prefix + "/"}
    if set(values) != set(RELEASES):
        raise ContractError("both MIMIC image and VQA S3 sources are required")
    return values


class S3:
    def __init__(self, env_file):
        self.env = load_aws_environment(env_file)
        self.env.update(AWS_PAGER="", AWS_MAX_ATTEMPTS="4", AWS_RETRY_MODE="standard")
        self.executable = shutil.which("aws", path=self.env.get("PATH"))
        if not self.executable:
            raise ContractError("AWS CLI must be provisioned before dataset setup")

    def call(self, operation, params, outfile=None):
        if operation not in {"list-objects-v2", "head-object", "get-object"}:
            raise ContractError("only S3 listing and object reads are enabled")
        command = [self.executable, "s3api", operation,
                   "--output", "json", "--no-paginate", "--cli-connect-timeout", "20",
                   "--cli-read-timeout", "120"]
        if operation == "get-object":
            # Streaming-output commands do not support --cli-input-json/yaml.
            # Identity conditions must reach the CLI as explicit arguments too.
            options = {"Bucket": "--bucket", "Key": "--key",
                       "IfMatch": "--if-match", "VersionId": "--version-id"}
            if (outfile is None or not {"Bucket", "Key", "IfMatch"}.issubset(params)
                    or set(params) - set(options)
                    or any(not isinstance(value, str) or not value for value in params.values())):
                raise ContractError("conditional S3 GET requires known arguments and an output file")
            for name, option in options.items():
                if name in params:
                    command += [option, params[name]]
        else:
            command += ["--cli-input-json", json.dumps(params)]
        if params.get("Bucket", "").startswith("arn:"):
            command += ["--region", params["Bucket"].split(":")[3]]
        if outfile is not None:
            command.append(str(outfile))
        result = subprocess.run(command, env=self.env, capture_output=True, text=True, shell=False)
        if result.returncode:
            raise ContractError("S3 read failed; check authorization, source identity and connectivity privately")
        return json.loads(result.stdout)

    def listing(self, source):
        token, seen = None, set()
        while True:
            params = {"Bucket": source["bucket"], "Prefix": source["prefix"], "MaxKeys": 1000}
            if token:
                params["ContinuationToken"] = token
            page = self.call("list-objects-v2", params)
            yield from page.get("Contents", [])
            if not page.get("IsTruncated"):
                break
            token = page.get("NextContinuationToken")
            if not token or token in seen:
                raise ContractError("S3 listing pagination is incomplete")
            seen.add(token)


def object_entry(client, source, key, relative, sha256=None):
    meta = client.call("head-object", {"Bucket": source["bucket"], "Key": key})
    if type(meta.get("ContentLength")) is not int or meta["ContentLength"] <= 0 or not meta.get("ETag"):
        raise ContractError("S3 object has incomplete identity or size metadata")
    return {"bucket": source["bucket"], "key": key, "path": relative,
            "bytes": meta["ContentLength"], "etag": meta["ETag"],
            "version_id": meta.get("VersionId"), "sha256": sha256}


def save_plan(output, root, entries, purpose, access_review, repository=ROOT):
    root, output = private_path(root, repository), private_path(output, repository)
    if output.exists() or not entries or not isinstance(access_review, str) or not access_review.strip() or access_review.strip().upper() == "UNRESOLVED":
        raise ContractError("a new plan and an explicit target-storage access review are required")
    paths = set()
    for entry in entries:
        path = destination(root, entry["path"])
        if path in paths:
            raise ContractError("duplicate transfer destination")
        paths.add(path)
    plan = {"schema": "cxr-s3-plan-v1", "data_root": str(root), "purpose": purpose,
            "access_review": access_review, "entries": entries}
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, plan)
    return {"status": "planned", "objects": len(entries), "bytes": sum(e["bytes"] for e in entries),
            "plan_sha256": file_hash(output), "download_started": False}


def metadata_plan(env_file, root, output, access_review, repository=ROOT):
    private_path(root, repository)
    private_path(output, repository)
    sources, client = source_settings(env_file), S3(env_file)
    entries = []
    names = ("mimic-cxr-2.0.0-split.csv.gz", "mimic-cxr-2.0.0-metadata.csv.gz", "SHA256SUMS.txt", "LICENSE.txt")
    source = sources["images"]
    for name in names:
        entries.append(object_entry(client, source, source["prefix"] + name, f"mimic-cxr-jpg/2.1.0/{name}"))
    source = sources["qa"]
    wanted = {"train.json", "valid.json", "test.json", "LICENSE.txt", "SHA256SUMS.txt"}
    found = {}
    for item in client.listing(source):
        key = item["Key"]
        name = PurePosixPath(key).name
        if name not in wanted:
            continue
        if name in found:
            raise ContractError("ambiguous VQA release table or license/checksum path")
        found[name] = key
    if not {"train.json", "valid.json", "test.json"}.issubset(found):
        raise ContractError("all three VQA source tables are required for the union test seal")
    for name, key in sorted(found.items()):
        relative = key.removeprefix(source["prefix"])
        entries.append(object_entry(client, source, key, f"mimic-ext-mimic-cxr-vqa/1.0.0/{relative}"))
    return save_plan(output, root, entries, "metadata_for_full_population_split_audit", access_review, repository)


def image_plan(env_file, root, catalog, output, access_review, limits=None, repository=ROOT):
    root, catalog = private_path(root, repository), private_path(catalog, repository)
    if not catalog.is_relative_to(root):
        raise ContractError("catalog must be under the private data root")
    report = json.loads((catalog / "report.json").read_text())
    if (report.get("status") != "complete" or report.get("split_policy") != "mimic-official"
            or report.get("official_patient_overlaps") != 0 or report.get("identity_conflicts") != 0):
        raise ContractError("an audited official-split catalog is required")
    for name in ("images.jsonl", "vqa_links.jsonl"):
        if file_hash(catalog / name) != report.get("output_sha256", {}).get(name):
            raise ContractError("catalog export checksum differs")
    views = {}
    with (catalog / "images.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            if row["dataset"] == "mimic-cxr":
                views[row["image_id"]] = row["view"]
    selected = {"train": {}, "validation": {}}
    with (catalog / "vqa_links.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            split = row["mimic_source_split"]
            if split in selected and row["test_sealed"] == 0 and views.get(row["image_id"]) in {"AP", "PA"}:
                selected[split][row["image_id"]] = row
    if limits is not None and (set(limits) != set(selected) or any(type(n) is not int or n <= 0 for n in limits.values())):
        raise ContractError("smoke limits require positive image counts for both development splits")
    client, source = S3(env_file), source_settings(env_file)["images"]
    entries = []
    for split, rows in selected.items():
        keys = sorted(rows)
        if limits:
            keys = keys[:limits[split]]
        if not keys:
            raise ContractError("selected development image split is empty")
        for key in keys:
            row = rows[key]
            relative = row["path"]
            prefix = "mimic-cxr-jpg/2.1.0/"
            if not relative.startswith(prefix):
                raise ContractError("catalog image is outside the pinned image release")
            entries.append(object_entry(client, source, source["prefix"] + relative[len(prefix):],
                                        relative, row.get("expected_sha256")))
    return save_plan(output, root, entries, "smoke_only" if limits else "development_images", access_review, repository)


def fetch_plan(env_file, plan_path, expected_hash, max_bytes, repository=ROOT):
    plan_path = private_path(plan_path, repository)
    if file_hash(plan_path) != expected_hash:
        raise ContractError("transfer plan checksum differs")
    plan = json.loads(plan_path.read_text())
    if plan.get("schema") != "cxr-s3-plan-v1" or not plan.get("access_review"):
        raise ContractError("reviewed S3 transfer plan is required")
    root = private_path(plan["data_root"], repository)
    entries = plan["entries"]
    if (type(max_bytes) is not int or max_bytes <= 0 or not entries
            or any(type(e.get("bytes")) is not int or e["bytes"] <= 0 for e in entries)
            or sum(e["bytes"] for e in entries) > max_bytes):
        raise ContractError("planned transfer exceeds the explicit byte budget")
    paths = [destination(root, e["path"]) for e in entries]
    if len(set(paths)) != len(paths):
        raise ContractError("duplicate transfer destination")
    receipt_path = plan_path.with_suffix(".downloads.json")
    previous = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
    if previous and previous.get("plan_sha256") != expected_hash:
        raise ContractError("download receipt belongs to another plan")
    receipt = previous or {"plan_sha256": expected_hash, "files": {}}
    missing_bytes = 0
    for entry, path in zip(entries, paths):
        if path.exists():
            sha = file_hash(path)
            expected = entry.get("sha256") or receipt["files"].get(entry["path"], {}).get("sha256")
            if path.stat().st_size != entry["bytes"] or not expected or sha != expected:
                raise ContractError("existing destination lacks matching integrity evidence; nothing overwritten")
        else:
            missing_bytes += entry["bytes"]
    root.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(root).free < missing_bytes + 1024 ** 3:
        raise ContractError("insufficient free space including the 1 GiB reserve")
    client = S3(env_file)
    lock = plan_path.with_suffix(".lock")
    # A crashed transfer leaves its lock for explicit operator inspection/removal.
    with lock.open("x"):
        pass
    try:
        write_json(receipt_path, receipt)
        for entry, path in zip(entries, paths):
            if path.exists() and entry["path"] not in receipt["files"]:
                receipt["files"][entry["path"]] = {"sha256": file_hash(path), "bytes": entry["bytes"]}
        write_json(receipt_path, receipt)
        for entry, path in zip(entries, paths):
            if path.exists():
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".part", delete=False) as stream:
                temporary = Path(stream.name)
            try:
                params = {"Bucket": entry["bucket"], "Key": entry["key"], "IfMatch": entry["etag"]}
                if entry.get("version_id"):
                    params["VersionId"] = entry["version_id"]
                meta = client.call("get-object", params, temporary)
                sha = file_hash(temporary)
                if (temporary.stat().st_size != entry["bytes"] or meta.get("ETag") != entry["etag"]
                        or (entry.get("sha256") and sha != entry["sha256"])):
                    raise ContractError("download content differs from the pinned transfer plan")
                # Write receipt first; a crash before publish safely re-downloads.
                receipt["files"][entry["path"]] = {"sha256": sha, "bytes": entry["bytes"]}
                write_json(receipt_path, receipt)
                # Same-filesystem atomic publish that cannot replace an existing file.
                if os.name == "nt":
                    os.rename(temporary, path)  # Windows rename refuses an existing destination.
                else:
                    os.link(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
    finally:
        lock.unlink(missing_ok=True)
    return {"status": "complete", "objects": len(entries), "downloaded_bytes": missing_bytes,
            "plan_sha256": expected_hash, "receipt_sha256": file_hash(receipt_path),
            "integrity": "conditional S3 identity, byte sizes, local SHA256 and publisher SHA256 where present"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="parse local source/auth settings; no AWS requests")
    for name in ("plan-metadata", "plan-images"):
        plan = sub.add_parser(name)
        plan.add_argument("--data-root", type=Path, required=True)
        plan.add_argument("--output", type=Path, required=True)
        plan.add_argument("--access-review", required=True)
        if name == "plan-images":
            plan.add_argument("--catalog", type=Path, required=True)
            plan.add_argument("--smoke", action="store_true", help="32 train/8 validation images, smoke-only")
    fetch = sub.add_parser("fetch")
    fetch.add_argument("--plan", type=Path, required=True)
    fetch.add_argument("--plan-sha256", required=True)
    fetch.add_argument("--max-bytes", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            sources = source_settings(args.env_file)
            env = load_aws_environment(args.env_file)
            result = {"sources_configured": sorted(sources), "credentials_present":
                bool(env.get("AWS_ACCESS_KEY_ID") and env.get("AWS_SECRET_ACCESS_KEY")),
                "aws_cli_present": bool(shutil.which("aws")), "network_called": False}
        elif args.command == "plan-metadata":
            result = metadata_plan(args.env_file, args.data_root, args.output, args.access_review)
        elif args.command == "plan-images":
            result = image_plan(args.env_file, args.data_root, args.catalog, args.output, args.access_review,
                                {"train": 32, "validation": 8} if args.smoke else None)
        else:
            result = fetch_plan(args.env_file, args.plan, args.plan_sha256, args.max_bytes)
        print(json.dumps(result), flush=True)
        return 0
    except KeyboardInterrupt:
        print("Transfer interrupted; completed files and integrity receipts retained.", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"Dataset setup blocked ({type(error).__name__}); no private values displayed.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
