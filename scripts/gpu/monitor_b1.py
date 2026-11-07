"""Print aggregate run state; never print raw configs, logs, predictions or paths."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from cxr_research.utils.errors import ContractError
from dataset_sync import private_path

NUMBERS = {"global_step", "max_steps", "epoch", "elapsed_seconds", "loss", "eval_loss",
           "learning_rate", "grad_norm", "allocated_bytes", "reserved_bytes", "peak_allocated_bytes"}


def snapshot(directory, pid=None, stale_seconds=300, repository=ROOT):
    directory = private_path(directory, repository)
    record_path, progress_path = directory / "run.json", directory / "progress.json"
    record = json.loads(record_path.read_text()) if record_path.is_file() else {}
    progress = json.loads(progress_path.read_text()) if progress_path.is_file() else {}
    status = record.get("status")
    result = {"status": status if status in {"running", "complete", "interrupted", "failed"} else "not_recorded"}
    result.update({k: v for k, v in progress.items()
                   if k in NUMBERS and type(v) in (int, float) and math.isfinite(v)})
    updated = progress.get("updated_at_unix")
    age = max(0, time.time() - updated) if type(updated) in (int, float) and math.isfinite(updated) else None
    result.update(heartbeat_age_seconds=age, heartbeat_stale=age is None or age > stale_seconds)
    if pid is not None:
        if type(pid) is not int or pid <= 0:
            raise ContractError("a positive process ID is required")
        if os.name == "nt":
            raise ContractError("PID monitoring is supported inside the Linux training container")
        try:
            os.kill(pid, 0)
            alive = True
        except ProcessLookupError:
            alive = False
        except PermissionError:
            alive = None
        result["process_alive"] = alive
        if alive is False and status == "running":
            result["status"] = "process_exited_unfinalized"
    if status == "running" and result["heartbeat_stale"]:
        result["attention"] = "check process and GPU; stored running state does not prove liveness"
    latest = directory / "latest_checkpoint.json"
    if latest.is_file():
        pointer = json.loads(latest.read_text())
        step = pointer.get("global_step")
        available = (type(step) is int and step >= 0 and pointer.get("checkpoint") == f"checkpoint-{step}"
                     and (directory / f"checkpoint-{step}" / "artifact_hashes.json").is_file())
        result["latest_checkpoint_available"] = available
        if available:
            result["latest_checkpoint_step"] = step
        result["checkpoint_integrity_verified"] = False  # Resume performs the full hash check.
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--pid", type=int)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=int, default=30)
    args = parser.parse_args(argv)
    if not 5 <= args.interval <= 60:
        parser.error("interval must be between 5 and 60 seconds")
    try:
        while True:
            result = snapshot(args.run, args.pid)
            print(json.dumps(result, allow_nan=False), flush=True)
            if not args.watch or result["status"] in {"complete", "failed", "interrupted", "process_exited_unfinalized"}:
                return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        print(f"Monitor unavailable ({type(error).__name__}); no private values displayed.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
