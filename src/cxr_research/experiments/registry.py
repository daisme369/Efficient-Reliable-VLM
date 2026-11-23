"""Validate the experiment registry, gate completion and redraw result charts."""

from __future__ import annotations

import json
import re
from pathlib import Path
from cxr_research.utils.errors import ContractError
from cxr_research.utils.io import write_json

FIELDS = ("experiment_id", "jira_key", "commit", "config", "dataset_version",
          "seed", "hardware", "status", "artifact_path")
OPTIONAL = ("notes",)
STATUSES = ("planned", "running", "complete", "failed", "interrupted")
# Every train/evaluate run directory written by experiments.runs holds these two files.
REQUIRED_OUTPUTS = ("run.json", "metrics.json")
JIRA_KEY = re.compile(r"^[A-Z][A-Z0-9]+-[0-9]+$")
COMMIT = re.compile(r"^[0-9a-f]{7,40}$")


def record_problems(record, status=None) -> list[str]:
    """Return field names and rules that fail; `status` checks a planned transition."""
    if not isinstance(record, dict):
        return ["record must be an object"]
    status = status or record.get("status")
    problems = [f"unknown field: {key}" for key in record if key not in FIELDS + OPTIONAL]
    for field in FIELDS:
        value = record.get(field)
        # Only a planned run may still lack an output directory.
        if field == "artifact_path" and value is None and status == "planned":
            continue
        if value is None or value == "":
            problems.append(f"missing {field}")
        elif field == "seed":
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2**32 - 1:
                problems.append("seed must be an integer from 0 through 4294967295")
        elif not isinstance(value, str):
            problems.append(f"{field} must be a string")
        elif field == "jira_key" and not JIRA_KEY.match(value):
            problems.append("jira_key must look like MVD-9")
        elif field == "commit" and not COMMIT.match(value):
            problems.append("commit must be 7-40 lowercase hex characters")
    if status not in STATUSES:
        problems.append(f"status must be one of {', '.join(STATUSES)}")
    if "notes" in record and not isinstance(record["notes"], str):
        problems.append("notes must be a string")
    return problems


def load_registry(path: Path) -> dict:
    try:
        registry = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ContractError(f"cannot read registry: {type(error).__name__}") from None
    if not isinstance(registry, dict) or registry.get("schema_version") != 1:
        raise ContractError("registry schema_version must be 1")
    if not isinstance(registry.get("experiments"), list):
        raise ContractError("registry experiments must be a list")
    return registry


def check_registry(registry: dict) -> list[str]:
    problems, seen = [], set()
    for index, record in enumerate(registry["experiments"]):
        problems += [f"experiments[{index}]: {problem}" for problem in record_problems(record)]
        identifier = record.get("experiment_id") if isinstance(record, dict) else None
        if identifier in seen:
            problems.append(f"experiments[{index}]: duplicate experiment_id")
        seen.add(identifier)
    for index, chart in enumerate(registry.get("charts", [])):
        if not isinstance(chart, dict) or not isinstance(chart.get("metric"), str) or not chart["metric"]:
            problems.append(f"charts[{index}]: metric must be a dotted key such as primary.closed.closed_accuracy")
    return problems


def mark_complete(path: Path, experiment_id: str) -> dict:
    """Set one record to complete only when every required field is present."""
    registry = load_registry(path)
    if problems := check_registry(registry):
        raise ContractError("registry is invalid: " + "; ".join(problems))
    matches = [record for record in registry["experiments"] if record["experiment_id"] == experiment_id]
    if not matches:
        raise ContractError("experiment_id is not in the registry")
    record = matches[0]
    if problems := record_problems(record, status="complete"):
        raise ContractError("cannot mark complete: " + "; ".join(problems))
    directory = artifact_directory(path, record)
    missing = [name for name in REQUIRED_OUTPUTS if not (directory / name).is_file()]
    if missing:
        raise ContractError("cannot mark complete: artifact_path lacks " + ", ".join(missing))
    try:
        run_status = json.loads((directory / "run.json").read_text(encoding="utf-8")).get("status")
    except (OSError, json.JSONDecodeError, AttributeError):
        run_status = None
    if run_status != "complete":
        raise ContractError("cannot mark complete: run.json status is not complete")
    record["status"] = "complete"
    write_json(Path(path), registry)
    return record


def artifact_directory(registry_path: Path, record: dict) -> Path:
    # Relative artifact paths are read from the registry file's folder.
    return Path(registry_path).parent / record["artifact_path"]


def metric_value(metrics: dict, key: str):
    value = metrics
    for part in key.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def render_charts(path: Path, output: Path) -> list[str]:
    """Draw one bar chart per registry chart entry from complete runs' metrics.json."""
    registry = load_registry(path)
    if problems := check_registry(registry):
        raise ContractError("registry is invalid: " + "; ".join(problems))
    if not registry.get("charts"):
        raise ContractError("registry has no charts to draw")
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot

    complete = [record for record in registry["experiments"] if record["status"] == "complete"]
    metrics = {}
    for record in complete:
        file = artifact_directory(path, record) / "metrics.json"
        try:
            metrics[record["experiment_id"]] = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ContractError(f"cannot read metrics.json for {record['experiment_id']}: "
                                f"{type(error).__name__}") from None
    Path(output).mkdir(parents=True, exist_ok=True)
    written = []
    for chart in registry["charts"]:
        key = chart["metric"]
        points = [(name, metric_value(values, key)) for name, values in metrics.items()]
        points = [(name, value) for name, value in points if value is not None]
        if not points:
            raise ContractError(f"no complete experiment reports {key}")
        figure, axis = pyplot.subplots(figsize=(max(4, 1.2 * len(points) + 2), 3.5))
        axis.bar([name for name, _ in points], [value for _, value in points], color="#4C72B0")
        axis.set_title(chart.get("title") or key)
        axis.set_ylabel(key)
        axis.tick_params(axis="x", labelrotation=30)
        figure.tight_layout()
        target = Path(output) / (re.sub(r"[^A-Za-z0-9._-]+", "_", key) + ".png")
        # Fixed metadata keeps regenerated PNG bytes stable across runs.
        figure.savefig(target, dpi=150, metadata={"Software": None})
        pyplot.close(figure)
        written.append(str(target))
    return written
