"""Expand YAML variants and seeds, then run isolated training processes in order."""

from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from cxr_research.experiments.config import ExperimentConfig, check_storage, config_from_dict
from cxr_research.utils.errors import ContractError
from cxr_research.utils.hashing import file_hash
from cxr_research.utils.io import write_json


@dataclass(frozen=True)
class SweepRun:
    variant: str
    config: ExperimentConfig


@dataclass(frozen=True)
class SweepPlan:
    sweep_id: str
    directory: Path
    on_error: str
    source_sha256: str
    runs: tuple[SweepRun, ...]

    def preview(self) -> dict:
        return {"sweep_id": self.sweep_id, "run_count": len(self.runs), "on_error": self.on_error,
                "data_protocol_gpu_checks": "not_checked",
                "runs": [{"run_id": r.config.run_id, "variant": r.variant,
                          "seed": r.config.seed, "config_sha256": r.config.fingerprint}
                         for r in self.runs]}


def _read_yaml(path: Path) -> dict:
    try:
        import yaml
    except ImportError:
        raise ContractError("YAML sweeps require the pinned PyYAML project dependency") from None

    class UniqueSafeLoader(yaml.SafeLoader):
        def construct_mapping(self, node, deep=False):
            result = {}
            for key_node, value_node in node.value:
                key = self.construct_object(key_node, deep=deep)
                if not isinstance(key, str) or key in result:
                    raise ContractError("YAML keys must be unique strings; quote protocol run IDs")
                result[key] = self.construct_object(value_node, deep=deep)
            return result

    try:
        text = path.read_text(encoding="utf-8")
        if any(isinstance(token, yaml.tokens.AliasToken) for token in yaml.scan(text)):
            raise ContractError("YAML aliases are unsupported; use base and variant overrides")
        result = yaml.load(text, Loader=UniqueSafeLoader)
    except yaml.YAMLError:
        # Parser messages include the offending source line, which may contain private data.
        raise ContractError("invalid YAML sweep document") from None
    if not isinstance(result, dict):
        raise ContractError("sweep must be a YAML mapping")
    return result


def _merge(base: dict, overrides: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in overrides.items():
        result[key] = (_merge(result[key], value)
                       if isinstance(value, dict) and isinstance(result.get(key), dict)
                       else copy.deepcopy(value))
    return result


def load_sweep(path: Path, repository: Path) -> SweepPlan:
    raw = _read_yaml(path)
    required = {"schema_version", "sweep_id", "base", "variants", "seeds"}
    if not required <= raw.keys() or raw.keys() - required - {"on_error", "protocols"}:
        raise ContractError("unknown or missing sweep fields")
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        raise ContractError("unsupported sweep schema version")
    sweep_id = raw["sweep_id"]
    if not isinstance(sweep_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,39}", sweep_id):
        raise ContractError("invalid sweep ID")
    seeds = raw["seeds"]
    if (not isinstance(seeds, list) or not seeds
            or any(type(seed) is not int or not 0 <= seed < 2**32 for seed in seeds)
            or len(set(seeds)) != len(seeds)):
        raise ContractError("seeds must be distinct integers in [0, 2**32)")
    base, variants = raw["base"], raw["variants"]
    if not isinstance(base, dict) or {"run_id", "seed"} & base.keys():
        raise ContractError("base must be a config mapping without run_id or seed")
    if not isinstance(base.get("output_root"), str) or not Path(base["output_root"]).is_absolute():
        raise ContractError("base.output_root must be an absolute path")
    if not isinstance(variants, list) or not variants:
        raise ContractError("variants must be a nonempty list")
    on_error = raw.get("on_error", "stop")
    if on_error not in ("stop", "continue"):
        raise ContractError("on_error must be stop or continue")
    protocols = raw.get("protocols", {})
    if not isinstance(protocols, dict) or any(not isinstance(p, str) or not Path(p).is_absolute()
                                               for p in protocols.values()):
        raise ContractError("protocols must map run IDs to absolute protocol paths")
    directory = Path(base["output_root"]).resolve() / sweep_id
    runs, names = [], set()
    for variant in variants:
        if not isinstance(variant, dict) or set(variant) - {"name", "overrides"}:
            raise ContractError("variant accepts only name and overrides")
        name, overrides = variant.get("name"), variant.get("overrides", {})
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,39}", name):
            raise ContractError("invalid variant name")
        if name.casefold() in names:
            raise ContractError("variant names must be unique ignoring case")
        names.add(name.casefold())
        if not isinstance(overrides, dict) or {"run_id", "seed", "output_root"} & overrides.keys():
            raise ContractError("variant overrides cannot set run_id, seed or output_root")
        for seed in seeds:
            run_id = f"{name}-seed-{seed}"
            resolved = _merge(base, overrides)
            resolved.update(run_id=run_id, seed=seed, output_root=str(directory / "runs"))
            if run_id in protocols:
                resolved["protocol"] = protocols[run_id]
            try:
                config = config_from_dict(resolved)
                check_storage(config, repository)
            except ContractError:
                raise
            except (TypeError, ValueError, AttributeError):
                raise ContractError("malformed resolved experiment configuration") from None
            if config.baseline == "B0":
                raise ContractError("training sweeps cannot include frozen B0; use evaluate")
            runs.append(SweepRun(name, config))
    if protocols.keys() - {r.config.run_id for r in runs}:
        raise ContractError("protocols contains an unknown run ID")
    return SweepPlan(sweep_id, directory, on_error, file_hash(path), tuple(runs))


def run_sweep(plan: SweepPlan, repository: Path) -> int:
    """Never reuse a sweep directory. Each child executes the existing train gates."""
    try:
        plan.directory.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        raise ContractError("sweep output already exists; choose a new sweep_id") from None
    configs_dir = plan.directory / "configs"
    configs_dir.mkdir()
    entries = []
    for run in plan.runs:
        config = run.config
        write_json(configs_dir / f"{config.run_id}.json", config.as_dict())
        entries.append({"run_id": config.run_id, "variant": run.variant, "seed": config.seed,
                        "config_sha256": config.fingerprint, "status": "pending",
                        "exit_code": None})
    summary = {"schema_version": 1, "sweep_id": plan.sweep_id,
               "source_sha256": plan.source_sha256, "on_error": plan.on_error,
               "status": "running", "started_at_unix": time.time(), "runs": entries}
    summary_path = plan.directory / "sweep.json"
    write_json(summary_path, summary)
    env = os.environ.copy()
    package_root = str(Path(__file__).resolve().parents[2])
    env["PYTHONPATH"] = package_root + os.pathsep + env.get("PYTHONPATH", "")
    entry = None
    try:
        for index, entry in enumerate(entries, start=1):
            entry.update(status="running", started_at_unix=time.time())
            write_json(summary_path, summary)
            config_path = configs_dir / f"{entry['run_id']}.json"
            print(f"Training {index}/{len(entries)}: {entry['run_id']}", flush=True)
            started = time.monotonic()
            with (plan.directory / f"{entry['run_id']}.log").open("w", encoding="utf-8") as log:
                result = subprocess.run(
                    [sys.executable, "-m", "cxr_research", "--repository", str(repository),
                     "train", "--config", str(config_path)],
                    cwd=repository, env=env, stdout=log, stderr=subprocess.STDOUT, check=False)
            entry.update(exit_code=result.returncode, elapsed_seconds=time.monotonic() - started)
            if result.returncode == 130:
                raise KeyboardInterrupt
            entry["status"] = "failed"
            if result.returncode == 0:
                record_path = plan.directory / "runs" / entry["run_id"] / "run.json"
                try:
                    record = json.loads(record_path.read_text(encoding="utf-8"))
                    if (record.get("status") == "complete"
                            and record.get("config_sha256") == entry["config_sha256"]):
                        entry["status"] = "complete"
                except (OSError, ValueError, AttributeError):
                    pass
            write_json(summary_path, summary)
            print(f"Run {index}/{len(entries)}: {entry['status']}", flush=True)
            if entry["status"] == "failed" and plan.on_error == "stop":
                break
        summary["status"] = "complete" if all(e["status"] == "complete" for e in entries) else "failed"
    except BaseException as error:
        summary["status"] = "interrupted" if isinstance(error, KeyboardInterrupt) else "failed"
        summary["error_type"] = type(error).__name__
        if entry is not None and entry["status"] == "running":
            entry["status"] = summary["status"]
        raise
    finally:
        for pending in entries:
            if pending["status"] == "pending":
                pending["status"] = "not_run"
        summary["finished_at_unix"] = time.time()
        write_json(summary_path, summary)
    return 0 if summary["status"] == "complete" else 1
