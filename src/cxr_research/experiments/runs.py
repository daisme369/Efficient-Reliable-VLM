"""Own the lifecycle and status of each isolated run directory."""

from __future__ import annotations

import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from cxr_research.experiments.config import ExperimentConfig
from cxr_research.experiments.environment import environment
from cxr_research.utils.io import write_json


@contextmanager
def run_record(config: ExperimentConfig, manifest, operation: str, repository: Path,
               gpu_evidence: dict | None = None):
    directory = Path(config.output_root) / config.run_id
    directory.mkdir(parents=True, exist_ok=False)
    git = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repository, capture_output=True, text=True)
    status = subprocess.run(["git", "status", "--porcelain"], cwd=repository, capture_output=True, text=True)
    record = {"schema_version": 1, "operation": operation, "config": config.as_dict(),
              "config_sha256": config.fingerprint, "manifest_sha256": manifest.sha256,
              "provenance_sha256": manifest.provenance_sha256,
              "git_commit": git.stdout.strip() if git.returncode == 0 else "unknown",
              "git_dirty": bool(status.stdout) if status.returncode == 0 else None,
              "environment": environment(), "status": "running",
              "evidence": "synthetic" if config.data.synthetic else "exploratory",
              "started_at_unix": time.time(), "deterministic_algorithms": True}
    import torch
    if config.device == "cuda":
        if gpu_evidence is None:
            raise RuntimeError("GPU runs require recorded preflight evidence")
        torch.cuda.reset_peak_memory_stats()
        record["gpu"] = gpu_evidence
    else:
        record["gpu"] = None
    write_json(directory / "run.json", record)
    started = time.monotonic()
    try:
        yield directory, record
    except BaseException as error:
        record["status"] = "interrupted" if isinstance(error, KeyboardInterrupt) else "failed"
        record["error_type"] = type(error).__name__
        raise
    else:
        record["status"] = "complete"
    finally:
        try:
            if config.device == "cuda":
                torch.cuda.synchronize()
                record["peak_allocated_bytes"] = torch.cuda.max_memory_allocated()
                record["peak_reserved_bytes"] = torch.cuda.max_memory_reserved()
        except Exception:
            record["telemetry_status"] = "unavailable_after_device_error"
        finally:
            record["elapsed_seconds"] = time.monotonic() - started
            record["gpu_hours"] = record["elapsed_seconds"] / 3600 if config.device == "cuda" else 0
            write_json(directory / "run.json", record)
