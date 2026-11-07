# MedGemma BF16 LoRA: AWS to training

The optimized full-JPEG launcher and offline validation are prepared separately:
[`MIMIC_DOWNLOADER_HANDOFF.md`](MIMIC_DOWNLOADER_HANDOFF.md). The running Vast
downloader is unchanged. Deploying or stopping it requires the user's explicit
handoff approval; the new code preserves the frozen plan and CLI rollback path.

Implemented engineering workflow, 2026-10-30. Rental is a separate explicit
operator action. None of these tools creates, starts, stops or destroys a Vast
instance, approves a protocol, accepts model terms or publishes an image.

Current operator order, requested on 2026-10-30: download the **entire** pinned
JPEG release, verify every image against the publisher SHA256 and size, then
build the full catalog and canonical QA mapping. Earlier pilot artifacts remain
pilot evidence. The selected-image recipe in section 3 is an optional small
diagnostic; it is not the current full-download workflow.

On Vast 58351663 the private data root is `/private/project/data` (`data/` under
`/private/project`). JPEGs are under `mimic-cxr-jpg/2.1.0/files/`, source VQA under
`mimic-ext-mimic-cxr-vqa/1.0.0/`, and mapped exports under `prepared/`. Source and
model cache remain separate. Manifest image paths are relative to this data root.

`scripts/gpu/bulk_dataset_sync.py` supplies a full-release planner and resumable
downloader with an injected S3 client; it imports only the standard library.
Planning joins publisher image checksums to a complete paginated S3 inventory,
independently of QA, and fails on missing, duplicate or unknown images. The
current inventory has 377,110 JPEGs totaling 612,235,462,201 bytes. Fetching binds
the plan hash, uses conditional GET, verifies SHA256/size, preserves conflicting
files, rehashes reusable files and writes a private SQLite receipt. Its bounded
queue emits aggregate progress only. Full mapping must wait for the complete
verification certificate. Downloading test images for storage/integrity does
not permit using test QA for development or model selection.

Use the locked Linux amd64 image, one approved training GPU and private storage.
Run research commands as `researcher` (UID 10001). The generated Vast transport
recipe additionally pins Debian `awscli=2.9.19-1` (Apache-2.0, operational S3
reads only); research Python/CUDA pins are unchanged. Build and verify that
recipe before renting. AWS CLI is already installed on the current Windows
workstation; CPU preparation can run there with an absolute private data root.
Do not install the research GPU stack into the Windows conda environment.

## 1. Configure storage and source credentials

The following commands run **inside an already provisioned, reviewed Linux
container**, from the source checkout. Ensure the mounted private directory
belongs to UID 10001 before running them. Keep the source checkout and runtime
secrets separate; never copy the whole workstation `.env` to the rental.

```bash
umask 077
export PYTHONPATH="$PWD/src"
export DATA=/private/project/data
export SETUP=/private/project/setup/b1-smoke-v1
mkdir -p "$DATA" "$SETUP"
```

Provision a private env file `/private/project/secrets/aws.env` containing only
the AWS credential/settings keys and the two dataset sources. `dataset_sync.py`
accepts `MIMIC_CXR_JPG_S3_URI` and `MIMIC_CXR_VQA_S3_URI`. Existing lowercase
`mimic_cxr_jpg` / `mimic_cxr_vqa` values containing `aws s3 sync` or `aws s3 cp`
are parsed for their single S3 URI; their commands/options/destinations are
never executed. Bucket names, access-point aliases and S3 access-point ARN URIs
are supported. Source prefixes must identify the locked releases.

```bash
python scripts/gpu/dataset_sync.py --env-file /private/project/secrets/aws.env check
```

This checks local syntax and presence only. Set `ACCESS_REVIEW` below to the
actual review record authorizing this destination's storage/access/retention.
A credential that can read S3 does not authorize transfer to any rental host.
No dataset keys, object names, credentials or provider error bodies appear in
the tool's stdout. Plans and receipts contain restricted paths and stay private.

## 2. Plan, budget and download metadata

```bash
python scripts/gpu/dataset_sync.py --env-file /private/project/secrets/aws.env plan-metadata \
  --data-root "$DATA" --output "$SETUP/metadata-plan.json" --access-review "$ACCESS_REVIEW"
```

The plan includes official image split/metadata CSVs, publisher checksum/license
files and all three VQA tables. Source test membership is needed to seal the
union of test patients; test QA is never exported to development supervision.
HEAD reads known image-metadata keys; paginated LIST discovers VQA table paths.
Review the aggregate byte count before fetching. Bind the exact returned
`plan_sha256` and an explicit total byte budget:

```bash
python scripts/gpu/dataset_sync.py --env-file /private/project/secrets/aws.env fetch \
  --plan "$SETUP/metadata-plan.json" --plan-sha256 "$METADATA_PLAN_SHA256" \
  --max-bytes "$METADATA_BYTE_BUDGET"
python -m cxr_research catalog --data-root "$DATA" \
  --output "$DATA/manifests/b1-metadata-v1" --split-policy mimic-official
```

GET pins VersionId when available and always uses If-Match with the planned
ETag. ETag is an identity condition, **not an assumed MD5**. Transfers validate
size, publisher SHA256 when present and local SHA256 receipts. Temporary files
are not completion evidence. Retry reuses verified final files, downloads only
missing objects and refuses to overwrite a conflicting destination. The explicit
budget is checked before GET. Completed objects survive interruption; an
interrupted object restarts from its beginning. A hard-killed transfer can leave
a `.lock`/`.part`; inspect privately before removing its lock or stale temporary
file. Do not remove another running transfer's lock. Do not run overlapping plans
against the same data root concurrently.

## 3. Download only QA-linked development images

```bash
python scripts/gpu/dataset_sync.py --env-file /private/project/secrets/aws.env plan-images \
  --data-root "$DATA" --catalog "$DATA/manifests/b1-metadata-v1" \
  --output "$SETUP/images-plan.json" --access-review "$ACCESS_REVIEW" --smoke
python scripts/gpu/dataset_sync.py --env-file /private/project/secrets/aws.env fetch \
  --plan "$SETUP/images-plan.json" --plan-sha256 "$IMAGE_PLAN_SHA256" \
  --max-bytes "$IMAGE_BYTE_BUDGET"
python -m cxr_research catalog --data-root "$DATA" \
  --output "$DATA/manifests/b1-images-v1" --split-policy mimic-official
```

The initial catalog has no development payload exports while images are absent.
Image selection reads its checksum-bound, payload-free crosswalk: official
train/validation, AP/PA, and no patient sealed by either source. `--smoke` selects
lexical image IDs, at most 32 train / 8 validation images. Counts are images,
not QA counts. Omit `--smoke` for the full development image plan, with a new
plan/directory and reviewed capacity/budget. This selected-image recipe does not
download grounding, segmentation, auxiliary datasets or test images. The current
full-raw workflow above includes the complete JPEG release while retaining the
same test seal for all later development supervision.

## 4. Prepare canonical mapped supervision

```bash
python scripts/gpu/setup_b1.py draft --data-root "$DATA" \
  --catalog "$DATA/manifests/b1-images-v1" \
  --output "$DATA/rental/b1-smoke-data-v1" --smoke
```

Complete the generated private `catalog-preparation.json` access/license and QA
derivation fields from actual records. The script supplies only the documented
engineering data policy: original QA-linked AP/PA image, semantic_type
`verify/choose → closed`, `query → open`, Unicode normalization, source-order
answer join using `; `, and exclusion of empty answer sets from supervision.
It retains review/exclusion sidecars. Primary template IDs stay empty and all
retained QA remain secondary until eligibility review. The converter audits
the full image population and test seal before selecting data. Raw sources must
remain immutable; VQA bytes changing between indexing passes now fail closed.

```bash
python scripts/gpu/setup_b1.py prepare \
  --spec "$DATA/rental/b1-smoke-data-v1/catalog-preparation.json"
python scripts/gpu/setup_b1.py configure \
  --prepared-dir "$DATA/prepared/b1-smoke-data-v1" \
  --output "$DATA/rental/b1-smoke-run-v1" \
  --revision "$MEDGEMMA_REVISION" --jira-issue "$JIRA_ISSUE"
```

The outputs are `b1-local.json`, Linux `b1.json`, separate draft protocols,
deduplicated `transfer-files.jsonl`, and aggregate `readiness.json`. The local
and remote config fingerprints differ because their input paths differ. Stage
the Linux `b1.json` and `protocol-b1.json` under `/private/project/configs/`;
keep image/manifest relative paths under `/private/project/data`. If preparing
directly at those Linux paths, these are still review drafts, not approval.

The model stays `google/medgemma-1.5-4b-it`, unquantized BF16 frozen base with
FP32 trainable language-only LoRA adapters. Scientific hyperparameters and
epoch-based best-model selection are unchanged. Resolve the exact 40-hex model
revision and accept model terms separately; cache the pinned weights using
`rental_access.py cache-model` as described in [RENTAL.md](../docker/RENTAL.md).
Read-only Hugging Face metadata on 2026-10-30 resolved revision
`91850547d9f0b2fdd21aa7c5f4f3d1a8a52c243b`; this is a concrete candidate for
`MEDGEMMA_REVISION`, subject to the run's revision/terms review, not an automatic
change to an existing experiment. No weights were downloaded by that check.

An actual reviewer must complete the protocol decision/metric/pretraining-overlap
and target-host access records and approve the protocol bound to these exact
hashes. The configure command never changes `draft` to `approved`. Full runs
also require the plan's B0/data gates; a synthetic engineering test does not
satisfy them.

## 5. Preflight, pilot and monitoring

Set `CXR_CONTAINER_IMAGE_ID` to the verified deployed immutable image ID. Cache
weights privately before paying for training where possible. Inside the
approved container:

```bash
python scripts/gpu/bootstrap.py --config /private/project/configs/b1.json \
  --cache /private/project/cache/hf
```

Default bootstrap only checks data/hash/protocol/Git/storage/GPU/cache gates.
It does not start training or prove that the full model fits. Actual training
requires `--train` after these pass. Keep it alive across SSH disconnection with
an explicitly started private background job, for example:

```bash
nohup python -u scripts/gpu/bootstrap.py --config /private/project/configs/b1.json \
  --cache /private/project/cache/hf --train \
  > /private/project/setup/b1-smoke-v1/training.log 2>&1 < /dev/null &
echo $! > /private/project/setup/b1-smoke-v1/training.pid
python scripts/gpu/monitor_b1.py --run /private/project/runs/b1/b1-smoke-run-v1 --watch
```

Optional `--pid` checks that Linux process ID; PID reuse is possible, so inspect
the process privately when troubleshooting. Without PID, heartbeat and stored
run state are evidence of progress, not proof of a live process. A long model
load or evaluation may legitimately have no recent optimizer-step heartbeat.

`progress.json` is an atomic snapshot updated per optimizer step;
`progress.jsonl` contains numeric aggregate loss/LR/gradient/VRAM events every
`train.logging_steps` (default 10, plus first step). No remote tracker is enabled.
`run.json` records terminal status, GPU-hours and peak VRAM. A device/host kill
may leave `running`; monitor flags stale progress and an exited supplied PID.

`train.checkpoint_steps` (default 100) requests a full recovery checkpoint every
100 optimizer updates in addition to epoch checkpoints. It does not trigger
extra validation or change epoch-based best selection. Checkpoints contain
optimizer/scheduler/RNG state and an integrity inventory. The latest pointer is
published after inventory completion; monitor separately reports availability.
Resume verifies the actual hashes, never just the pointer.

Both new cadence fields participate in config fingerprints. Generate fresh
protocols/runs; this also changes legacy U-Net fingerprints because shared
training settings are serialized. Do not edit old identity/hashes to force a cross-version resume.
With small pilots, epoch saving can happen before the 100-step recovery interval.
Measure actual supervised forward/backward, finite loss, VRAM, adapter reload,
checkpoint/resume and throughput on the approved GPU before starting full data.

## 6. Recovery and completion

Use a new run ID, preserve scientific settings and the parent run directory:

```bash
python scripts/gpu/bootstrap.py --config /private/project/configs/b1-resume.json \
  --cache /private/project/cache/hf \
  --resume /private/project/runs/b1/b1-smoke-run-v1/checkpoint-100 --train
```

Select an existing inventoried checkpoint from the actual run. Keep parent
identity and earlier best checkpoints available. A killed partial checkpoint
without a valid inventory is not recoverable evidence. A full-data run needs a
separate manifest/config/protocol and fresh model initialization; do not resume
the smoke checkpoint into a different manifest.

Before terminating compute, verify run/metrics/adapter/processor/identity and
checkpoint inventories, copy private outputs to approved persistent storage,
and verify copied hashes. The pipeline does not terminate or back up a rental
automatically. Validation answer-quality evaluation is a separate existing CLI
operation; the full paper/reliability metric suite and final-test execution are
outside this B1 training setup.

## Current verification boundary

RUNTIME-2026-10-30 separately authorizes preparing a CUDA 12.8 profile on the
user-rented Vast instance 58351663. Set `runtime_profile: medgemma-bf16-cu128`,
BF16, quantization none and files/PIL. Preserve the existing Torch2.11.0+cu128 /
TorchVision0.26.0+cu128 pair via `requirements/gpu-cu128.txt`; install
`requirements/medgemma-cu128.txt` from PyPI with that pair constrained. Never
install the default root requirements into this environment, because those
require Torch2.14.1 and TorchVision0.29.1. No bitsandbytes/DALI is needed in this
explicit profile. The checker accepts driver ≥570.124.06 only for this profile;
default cu130 still requires driver580+ and its exact CUDA13 packages.

`setup_b1.py configure --runtime-profile medgemma-bf16-cu128` generates matching
private configs/protocol drafts. The profile field changes fingerprints for
legacy configs as well; new protocols/runs are required. Preserve historical
identity records. Actual immutable image provenance and reviewed data/protocol
gates still apply. The provider API currently exposes a mutable image tag for
this instance; no immutable running-image ID has been established yet.

Docker Desktop was updated locally to4.97.0 with verified official checksum and
signature; Linux engine29.9.2 works. The local dependency-image build was canceled
following the user's Vast-only execution clarification; no MedGemma/MIMIC run was
started locally. Source and the cu128 environment are being verified on Vast.

Synthetic S3 → official split/test seal → selected download → canonical manifest
→ BF16 config/protocol generation is covered by `tests/ci/test_b1_pipeline.py`.
Tiny CPU Trainer interruption/recovery is covered by
`tests/research/test_training_progress.py`. These checks establish engineering
behavior only. On Vast58351663 the explicit cu128 package/hardware gate and a
synthetic BF16 GPU forward/backward have passed. The pinned 4B model was also
loaded offline on that GPU: one BF16 LoRA optimizer update on a generated image,
adapter export and exact adapter/logit reload checks passed. These are synthetic
engineering results; they do not establish MIMIC training, full-data throughput
or model-scale Trainer checkpoint recovery. Those and the new prebuilt transport
image remain unverified. Immutable running-image identity and a final approved,
manifest-bound protocol are still required before real-data training.

API/package references: [AWS get-object](https://docs.aws.amazon.com/cli/latest/reference/s3api/get-object.html),
[AWS paginated listing](https://docs.aws.amazon.com/cli/latest/reference/s3api/list-objects-v2.html),
[Trainer callbacks](https://huggingface.co/docs/transformers/main_classes/callback),
[Debian AWS CLI pin](https://packages.debian.org/bookworm/awscli).
