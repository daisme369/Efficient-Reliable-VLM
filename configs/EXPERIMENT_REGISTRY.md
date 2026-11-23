# Experiment registry, smoke tests and charts

Jira: [MVD-9](https://vlminmedical.atlassian.net/browse/MVD-9). The registry is one JSON file that lists every experiment the team may cite. It records where a result came from. It does not change metrics, model logic or research protocols. A designated reviewer (Gia Khánh or Cáp Việt Anh) signs off before a registry result is used in a report.

Start from [experiment_registry.template.json](experiment_registry.template.json). Its two records are examples, not real runs. Keep the working registry with the private run outputs, outside Git, when it points at restricted-data runs.

## Record fields

| Field | Rule |
| --- | --- |
| `experiment_id` | Unique non-empty string, for example the run ID |
| `jira_key` | Issue key such as `MVD-6` |
| `commit` | Git commit of the source, 7–40 lowercase hex characters |
| `config` | Path of the config file used |
| `dataset_version` | Dataset or manifest version, for example `mimic-cxr-jpg-2.1.0+mimic-ext-vqa-1.0.0` |
| `seed` | Integer from 0 through 4294967295 |
| `hardware` | GPU model and count, or `cpu` |
| `status` | `planned`, `running`, `complete`, `failed` or `interrupted` |
| `artifact_path` | Run output directory. Only a `planned` record may use `null`. A relative path is read from the registry file's folder |
| `notes` | Optional string |

Other fields are rejected. Keep failed and interrupted runs in the registry; do not delete them.

The optional top-level `charts` list names the metrics to draw. Each entry has a dotted `metric` key into `metrics.json`, such as `primary.closed.closed_accuracy` (VQA) or `dice` (U-Net), and an optional `title`.

## Commands

Run from the repository root with `PYTHONPATH=src` (PowerShell: `$env:PYTHONPATH = (Resolve-Path src).Path`).

```bash
# Check every record. Exit code 1 lists the problems.
python -m cxr_research registry check --registry /private/project/registry.json

# Mark one run complete. Refused when a required field is missing, artifact_path
# lacks run.json or metrics.json, or run.json does not say "complete".
python -m cxr_research registry complete --registry /private/project/registry.json --experiment-id <id>

# Redraw every chart (one PNG per metric) from complete runs' metrics.json.
python -m cxr_research registry charts --registry /private/project/registry.json --output /private/project/charts
```

Always change a record to `complete` with `registry complete`, not by hand. Charts read only records with status `complete` and skip runs that do not report the metric. Each chart shows the metric exactly as `metrics.json` stores it.

## Smoke test

Use this to confirm the pipeline runs end to end on synthetic data before a real run. It needs the project CPU environment from [DEPENDENCIES.md](../DEPENDENCIES.md). The data are rectangles, not radiographs, so the scores are not evidence.

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
.venv/Scripts/python.exe -m cxr_research synthetic --output .test-tmp/cxr-smoke
.venv/Scripts/python.exe -m cxr_research validate --config .test-tmp/cxr-smoke/unet.json
.venv/Scripts/python.exe -m cxr_research train --config .test-tmp/cxr-smoke/unet.json
```

The run directory is `<output_root>/<run_id>` from `unet.json`. Add it to a registry with `dataset_version: "synthetic-fixture"` and `hardware: "cpu"`, then run `registry complete` and `registry charts` with a `dice` chart.

## Required-output checklist

Tick each item before asking the reviewer to sign off a run.

- [ ] The registry record passes `registry check`.
- [ ] `run.json` exists and its `status` is `complete` (or the record says `failed`/`interrupted`).
- [ ] `metrics.json` exists, and the charted metrics are present in it.
- [ ] `run.json` names the same commit, config and seed as the registry record.
- [ ] Training runs keep their checkpoint or adapter directory; MedGemma exports also have `model_identity.json` and `artifact_hashes.json`.
- [ ] Charts were regenerated with `registry charts`, not edited by hand.
- [ ] Nothing restricted is in Git: no MIMIC images, reports, identifiers, predictions or row-level results. Only aggregate charts approved by the reviewer leave the private storage.
- [ ] Synthetic or single-seed results are labelled exploratory.
- [ ] A designated reviewer signed off before the result is used in a report.
