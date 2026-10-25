# Running research experiments

These templates are development settings, not approved research protocols. `medgemma_b0.json` and `medgemma_b1.json` share the model and prompt settings; B1 adds language-only QLoRA. `unet.json` describes a binary anatomical segmentation pilot with augmentation disabled initially.

## Local CPU smoke run

Use the existing project environment. The package can run from source without installing it:

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
New-Item -ItemType Directory -Force .test-tmp | Out-Null
.venv/Scripts/python.exe -m cxr_research synthetic --output .test-tmp/cxr-smoke
.venv/Scripts/python.exe -m cxr_research validate --config .test-tmp/cxr-smoke/unet.json
.venv/Scripts/python.exe -m cxr_research train --config .test-tmp/cxr-smoke/unet.json
```

Use a new output path if the synthetic directory already exists. The generated data contains rectangles, not radiographs. U-Net smoke settings are 32-pixel images, width 8, CPU FP32, 3 epochs. This is a plumbing check, not a clinical experiment.

For normal package installation in a prepared environment, `python -m pip install --no-deps --no-build-isolation -e .` exposes `cxr-research`. This command is optional and was not executed by the agent. Runtime dependencies stay in `requirements.txt`; Linux GPU additions and the CUDA wheel lock remain separate. Package installation does not grant model or dataset access.

## Sequential YAML training sweeps

Use one YAML file to describe a shared `base` experiment config, named `variants`,
and `seeds`. See [unet_sweep.yaml](unet_sweep.yaml) for a complete development
template. Two variants and three seeds expand to six independent training runs.
This example does not approve a dataset, augmentation policy or GPU exception.

```bash
PYTHONPATH=src python -m cxr_research sweep --config /private/project/sweep.yaml --dry-run
PYTHONPATH=src python -m cxr_research sweep --config /private/project/sweep.yaml
```

On PowerShell, set `$env:PYTHONPATH = (Resolve-Path src).Path` and use
`.venv/Scripts/python.exe -m cxr_research sweep --config <file.yaml>`.
An installed package also exposes `cxr-research sweep --config <file.yaml>`.

The YAML schema is version 1:

- `sweep_id`: a unique directory name under `base.output_root`.
- `base`: the normal experiment settings, excluding `run_id` and `seed`.
  Include all data paths and shared model/training settings here. Existing
  experiment defaults fill omitted optional fields.
- `variants`: an ordered list of `name` and optional `overrides` mappings.
  Nested dictionaries merge, so overriding `train.learning_rate` preserves the
  base batch size. Overrides may not set `run_id`, `seed` or `output_root`.
- `seeds`: a nonempty list of distinct integers from 0 through 4294967295.
- `on_error`: `stop` by default, or `continue` to attempt remaining runs after a
  failure. Both return a nonzero exit status if any run failed.
- `protocols`: optional mapping from generated run IDs to absolute protocol
  paths. Real runs still need a protocol whose config, manifest and provenance
  fingerprints match that specific variant and seed. This mapping overrides
  the experiment's `protocol` path; it does not generate or approve protocols.

Runs execute in variant order, then seed order. The ID format is
`<variant>-seed-<seed>`. Variant names must be unique ignoring case. Duplicate
YAML keys, aliases, custom object tags and unknown configuration fields are
rejected. Use decimal notation for learning rates, for example `0.0002`.
All data, output and per-run protocol paths must be absolute. Relative paths
are not implicitly rebased to the YAML file's directory.

`--dry-run` validates every resolved config and storage location, prints run IDs,
seeds and config fingerprints, and writes nothing. It does not read datasets,
approve protocols, check GPU compatibility or estimate cost. Before each actual
training run, the existing CLI performs its data/protocol/GPU checks. B0 is
frozen and cannot appear in a training sweep. MedGemma B1 uses the same sweep
schema with `task: medgemma`, `baseline: B1`, a pinned model revision and its
normal MedGemma settings.

Each run starts a fresh Python process and trains from its configured initial
model, without sharing weights or optimizer state with previous runs. Process
exit releases its CUDA allocation. The runner uses the current Python
environment and executes one child at a time.

Artifacts are written under `base.output_root/<sweep_id>/`:

```text
sweep.json                    # YAML checksum, ordering, statuses and exit codes
configs/<variant>-seed-N.json  # Fully resolved config for each planned run
<variant>-seed-N.log           # Private child stdout/stderr
runs/<variant>-seed-N/         # Normal run record, metrics and checkpoints
```

The sweep never overwrites or automatically resumes an existing directory.
Use a new `sweep_id` for a rerun. For checkpoint resume, use the existing single
`train --resume` command with a new run ID. A handled interrupt records
`interrupted`, retains completed artifacts and leaves later runs `not_run`.
An abrupt machine termination may leave `running` in the last saved status;
inspect artifacts before attempting recovery. Logs and resolved configs for
real data stay in private storage, just like checkpoints.

## Data preparation

For the separate binary-lung U-Net manifest, private pilot configuration and
policy-gated startup, see [the U-Net pipeline](UNET_PIPELINE.md). That path uses
existing CheXmask catalog references and does not require a MedGemma cache.

For tar shards, token caching, length grouping and GPU decoding, see
[the I/O pipeline guide](IO_PIPELINE.md). These options work with both single
experiment configs and the YAML sweep's `base.io` section.

Copy `preparation.template.json` to private storage. Supply explicit source field mappings; the code does not guess the schema of downloaded datasets. The image table must already identify an image path, patient, image ID, view and split. Derived VQA data must provide an explicit split mapping, which is reconciled before rows are selected. The complete image index is checked for patient and image conflicts before QA joins or RCA filtering.

Supported source formats are CSV, gzipped CSV, JSONL, and JSON with an explicit records array. CheXmask conversion requires original-resolution coordinates, image-dimension agreement and one-based row-major RLE. Left and right lung masks are combined into a binary target. No DICOM conversion or multi-source pooling is implied.

```bash
PYTHONPATH=src python -m cxr_research prepare --spec /private/project/preparation.json
```

Preparation writes `manifest.jsonl`, `provenance.json`, masks and a status file under the specified private directory. Manifest/provenance schema 3 binds image and mask SHA-256 values, resolved source access/license metadata, derived-QA provenance and auditable exclusion stages. Final-test labels are omitted from the development manifest. Validation streams each unique asset once; on large datasets this is an I/O cost. Keep the source image tree immutable during an experiment. A failed preparation retains its status and is never silently reused.

The provenance includes source table versions/checksums, mapping specification, row/exclusion counts and split policy. The loader checks all splits before selecting train or validation rows. Patient namespaces must be consistent across tasks for the same source population.

## Real-run gates

1. Resolve data access, source versions, patient split, view/input selection, uncertain labels, template eligibility and mask quality/target policy.
2. For MedGemma, accept model terms independently and replace `UNRESOLVED` with the exact 40-character model revision. Download weights separately into private cache; the default loader is offline.
3. Copy the relevant experiment config outside the repository and set absolute private input/output paths. Run `validate` to obtain config, manifest and provenance fingerprints. This command checks data, not GPU compatibility or protocol approval.
4. Set a real Jira issue key in the experiment config. Complete `protocol.template.json` with the actual decision record and reviewers' decisions. Bind all three fingerprints. Do not change `status` solely to bypass unresolved research choices.
5. Run the GPU commands inside the locked Linux Docker environment with source mounted read-only, restricted data and output mounted separately, and one GPU visible. Set `CXR_CONTAINER_IMAGE_ID` to the immutable `sha256:<64 hex>` image ID used for the run. The runtime invokes the repository's existing hardware preflight and records the image ID, driver release, CUDA/runtime versions, role and selected device. Its current roles permit H100/H200 training and RTX inference. The requested RTX 3060/4090 U-Net training exception is still pending; no GPU gate is bypassed here.

No real data, model, run directory or row-level output belongs in the source checkout. Private Hugging Face caches also stay outside the checkout. The CLI does not send predictions to remote trackers or the Hub.

```bash
PYTHONPATH=src python -m cxr_research evaluate --config /private/project/b0.json
PYTHONPATH=src python -m cxr_research train --config /private/project/b1.json
PYTHONPATH=src python -m cxr_research evaluate --config /private/project/b1-eval.json --checkpoint /private/project/runs/b1/adapter
```

For evaluation, use a new `run_id` and retain the scientific configuration. Output paths/run names do not change its fingerprint. B1 export is an adapter; resumable Trainer checkpoints also contain optimizer/scheduler/RNG state. `train --resume <checkpoint>` verifies parent identity and content hashes and creates a new run directory. Keep the source MedGemma run and its checkpoints available during resume because Trainer may refer to an earlier best checkpoint.

U-Net uses `train --resume <epoch-NNNN.pt>` and `evaluate --checkpoint <epoch-NNNN.pt>`. Resume starts at an epoch boundary and copies the earlier selected best into the new run. Use the latest epoch checkpoint if the source run selected a better checkpoint after the one supplied. To change hyperparameters or total epochs, start a new experiment; exact resume requires the original fingerprint.

The CLI currently evaluates validation only. VQA reports exploratory normalized match and closed-answer macro-F1, with primary and secondary populations separated. It does not yet implement the full BLEU/ROUGE/BERTScore/clinical/reliability evaluation contract. Generation timing is explicitly unwarmed and is not a latency benchmark. U-Net Dice/IoU against CheXmask measure agreement with generated anatomical labels, not lesion grounding.

## Verification

```powershell
python scripts/ci/check_repository.py --scope repository
python -m compileall -q src scripts tests
python -m unittest discover -s tests -p "test_*.py" -v
.venv/Scripts/python.exe -m unittest discover -s tests/research -p "test_*.py" -v
```

Dependency-free CI skips tensor/image tests if CPU ML dependencies are absent. The last command must run with the project CPU environment to execute those tests. All test scratch data stays under `.test-tmp`, and is synthetic.
