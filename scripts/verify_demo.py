"""Demo verification for the Thorax app (TAS-26): latency, no-store privacy headers, backup and restore checks.

  latency  Sign in as a doctor and time N worklist and N viewer opens (study + image bytes).
           Password comes from THORAX_VERIFY_PASSWORD, never from the command line.
  backup   Copy the SQLite database (online backup API) and the image folder, with a hash manifest.
  check    Verify a live or restored database and image folder: integrity, foreign keys, image hashes.

Standard library only. Prints one JSON object; exits 1 when a check fails.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import time
import urllib.request
from http.cookiejar import CookieJar


def nearest_rank(values: list[float], percent: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percent / 100 * len(ordered)) - 1)]


def latency(base: str, origin: str, email: str, password: str, runs: int, target_ms: float) -> dict:
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
    leaks: list[str] = []

    def call(path: str, body: dict | None = None):
        request = urllib.request.Request(base.rstrip("/") + path, method="POST" if body is not None else "GET",
                                         data=json.dumps(body).encode() if body is not None else None,
                                         headers={"Origin": origin, "Content-Type": "application/json"})
        with opener.open(request, timeout=30) as response:
            data = response.read()
            if response.headers.get("Cache-Control") != "no-store":
                leaks.append(path)  # clinical responses must never be cached
            return json.loads(data) if response.headers.get_content_type() == "application/json" else data

    call("/api/v1/auth/login", {"email": email, "password": password})
    patients = call("/api/v1/patients")["items"]
    studies = call(f"/api/v1/patients/{patients[0]['id']}/studies")["items"]
    study_id = studies[0]["id"]
    timings: dict[str, list[float]] = {"worklist": [], "viewer": []}
    failures = 0
    for _ in range(runs):
        for name in timings:
            started = time.perf_counter()
            try:
                if name == "worklist":
                    call("/api/v1/patients")
                else:
                    study = call(f"/api/v1/studies/{study_id}")
                    image = study["images"][0]
                    if hashlib.sha256(call(image["content_url"])).hexdigest() != image["sha256"]:
                        raise ValueError("image bytes do not match the recorded hash")
            except Exception:  # noqa: BLE001 - every failed run is counted, not hidden
                failures += 1
                continue
            timings[name].append((time.perf_counter() - started) * 1000)
    result = {"base": base, "runs_per_operation": runs, "failures": failures, "cache_leaks": sorted(set(leaks)),
              "target_p95_ms": target_ms, "timing": "warm, sequential, wall clock incl. network"}
    for name, values in timings.items():
        result[name] = {"count": len(values), "p50_ms": round(nearest_rank(values, 50), 1) if values else None,
                        "p95_ms": round(nearest_rank(values, 95), 1) if values else None,
                        "max_ms": round(max(values), 1) if values else None}
    result["passed"] = not failures and not leaks and all(
        result[name]["count"] == runs and result[name]["p95_ms"] < target_ms for name in timings)
    return result


def backup(db: Path, images: Path, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=False)
    source = sqlite3.connect(db)
    target = sqlite3.connect(out / "thorax.sqlite3")
    try:
        source.backup(target)  # consistent copy even while the API is running
    finally:
        target.close()
        source.close()
    shutil.copytree(images, out / "thorax-images")
    manifest = {str(p.relative_to(out / "thorax-images").as_posix()): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted((out / "thorax-images").rglob("*")) if p.is_file()}
    (out / "manifest.json").write_text(json.dumps({"images": manifest}, indent=2), encoding="utf-8")
    return {"backup": str(out), "image_files": len(manifest)}


def check(db: Path, images: Path) -> dict:
    if not db.is_file():
        return {"passed": False, "problems": ["database file is missing"]}
    problems = []
    connection = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            problems.append("integrity_check failed")
        broken = connection.execute("PRAGMA foreign_key_check").fetchall()
        if broken:
            problems.append(f"{len(broken)} foreign key violations")
        rows = connection.execute("SELECT id, storage_key, sha256 FROM images").fetchall()
        for image_id, key, digest in rows:
            path = images / key
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                problems.append(f"image {image_id} is missing or does not match its hash")
        tables = {name for (name,) in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        unsigned = connection.execute(
            "SELECT COUNT(*) FROM reports WHERE status = 'final' AND (approved_by IS NULL OR approved_at IS NULL)"
        ).fetchone()[0] if "reports" in tables else 0
        if unsigned:
            problems.append(f"{unsigned} final reports lack a signature")
        finals = connection.execute("SELECT COUNT(*) FROM reports WHERE status = 'final'").fetchone()[0] if "reports" in tables else 0
    finally:
        connection.close()
    return {"passed": not problems, "problems": problems, "images_checked": len(rows), "final_reports": finals}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    timed = commands.add_parser("latency")
    timed.add_argument("--base", default="http://127.0.0.1:3000", help="web app or API origin")
    timed.add_argument("--origin", default="http://127.0.0.1:3000", help="browser origin the API accepts")
    timed.add_argument("--email", required=True)
    timed.add_argument("--runs", type=int, default=20)
    timed.add_argument("--target-ms", type=float, default=2000)
    for name in ("backup", "check"):
        sub = commands.add_parser(name)
        sub.add_argument("--db", type=Path, required=True)
        sub.add_argument("--images", type=Path, required=True)
        if name == "backup":
            sub.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "latency":
        password = os.environ.get("THORAX_VERIFY_PASSWORD")
        if not password:
            parser.error("set THORAX_VERIFY_PASSWORD")
        result = latency(args.base, args.origin, args.email, password, args.runs, args.target_ms)
    elif args.command == "backup":
        result = backup(args.db, args.images, args.out)
        result["passed"] = True
    else:
        result = check(args.db, args.images)
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
