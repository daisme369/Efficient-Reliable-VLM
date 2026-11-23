# Prepare and run the U-Net branch

The U-Net branch predicts one binary mask for the union of the left and right
lungs. It starts from random weights. It does not predict the heart, individual
lung classes, lesions or VQA answers. CheXmask targets are anatomical pseudo-labels.
Dice/IoU against those targets measure agreement with the source masks, not
clinical accuracy or independent lesion grounding.

The user-selected full MIMIC plus NIH run is documented under
[Local MIMIC and NIH training](#local-mimic-and-nih-training). The 32/8 limits below
belong to the earlier engineering pilot, not the available training population.

Run commands from the source checkout on C. Keep real inputs, masks, manifests,
transfer inventories, configs and checkpoints under the private data directory
on E. Commit source, templates, documentation and synthetic tests only.

## 1. Prepare a separate segmentation manifest

Copy `configs/unet-preparation.template.json` to private storage and resolve its
access, license, quality-policy and segmentation-decision fields. Pin the actual
catalog `report.json` SHA-256. The template selects AP/PA views and a separate
smoke-only budget of 32 train images and 8 validation images.

The proposed pilot RCA cutoff is 0.7, following the source's
[data dictionary](https://physionet.org/content/chexmask-cxr-segmentation-data/1.0.0/DATA_DICTIONARY.md).
Record the choice before preparation. A final experiment's threshold and
population still require review; do not select them from test results.

```powershell
.\.venv\Scripts\python.exe scripts/gpu/unet.py prepare `
  --spec E:/Project/Efficient-Reliable-VLM/data/rental/unet-pilot-20261025/preparation.json
```

The command checks the report and image/source/crosswalk export hashes. It audits
all official MIMIC image identities and patient splits before view, quality or
budget filtering, and seals the union of official and source-VQA test patients.
It does not open development/test QA payload files or decode test masks.

Eligible image IDs are sorted lexically within each split before applying a
smoke budget. Counts reconcile every exclusion: sealed patient, absent image,
view, absent mask reference, RCA and image budget. Selection does not depend on
answers, model predictions or test-mask quality. A small lexical subset is an
engineering check, not a representative benchmark.

The original mask CSV is hashed against its indexed source identity before its
byte offsets are used. This can read many gigabytes even for a small pilot.
Selected image bytes are checked against publisher SHA-256 where available.
Each selected mask reference must match the image ID, original dimensions and
RCA metadata. Left/right masks are decoded with the existing original-resolution
RLE decoder and combined without resize, flip or crop. Empty lung targets stop
preparation for review. Lung overlap is counted, not silently edited.

Output under the new `prepared_dir` contains:

```text
manifest.jsonl     canonical schema 3, task=unet, image and mask hashes
provenance.json   source/policy/code fingerprints, selection and exclusions
masks/*.png       original-resolution binary lung union
status.json       complete only after canonical validation
```

Existing output directories are never overwritten. An interrupted/failed run
retains its status and files; inspect it and choose a new directory for a retry.
The separate MedGemma QA manifest is not a substitute for this manifest.

For an approved full preparation, use a new spec/directory, `purpose: training`
and `image_limit_per_split: null`. This only prepares data. It does not approve
or start training. Do not run a full preparation as part of the bounded pilot.

## 2. Generate local and rented-GPU configs

```powershell
.\.venv\Scripts\python.exe scripts/gpu/unet.py configure `
  --prepared-dir E:/Project/Efficient-Reliable-VLM/data/prepared/unet-pilot-20261025 `
  --output E:/Project/Efficient-Reliable-VLM/data/rental/unet-pilot-20261025-run `
  --jira-issue MVD-3
```

Use the actual issue for the work. `MVD-3` tracks the existing data/feasibility
work; setting its key does not approve a U-Net training protocol or complete that
issue. The configuration generator writes:

- `unet-local.json` and `protocol-unet-local.json` for local manifest validation.
- `unet.json` and `protocol-unet.json` with Linux `/private/project/` run paths.
- `transfer-files.jsonl`, a private list of only selected images, masks and
  canonical manifest/provenance with sizes and hashes. It does not upload files.
- `readiness.json` with source checks and outstanding run gates.

Each protocol binds the config, manifest and provenance hashes. Protocols remain
`draft`. They describe the source pseudo-labels, binary target, no-augmentation
pilot, valid-pixel Dice/IoU and the separate external-access review. Do not set
`approved` without the actual review. Local and Linux data paths produce different
config fingerprints, so their protocols are distinct.

Validate the real local manifest without training:

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
.\.venv\Scripts\python.exe -m cxr_research validate `
  --config E:/Project/Efficient-Reliable-VLM/data/rental/unet-pilot-20261025-run/unet-local.json
```

`validate` does not approve a protocol or verify a GPU. U-Net has no MedGemma
weights, tokenizer, HF token or model-cache prerequisite.

## 3. Preflight and train on approved hardware

After protocol/access review and rental authorization, stage the exact source
checkout, private selected assets and configs in the paths recorded by the
Linux config. Keep original relative image paths. Run in the verified Linux
Docker environment as `researcher` with one visible H100, H200 or RTX PRO 6000 Blackwell and a driver of
release 580 or newer. Record the actual immutable image ID in
`CXR_CONTAINER_IMAGE_ID`. The dated local U-Net exception below permits the
exact RTX 3060 through a separate profile. Standard training still rejects
RTX 3060, RTX 4090 and RTX 5090. RTX PRO 6000 Blackwell supports both
training and inference, subject to the normal preflight.

```bash
cd /workspace/repo
python scripts/gpu/unet.py check --config /private/project/configs/unet.json
python scripts/gpu/unet.py train --config /private/project/configs/unet.json
```

`check` verifies image/mask hashes, nonempty train/validation populations, the
reviewed matching protocol, Git provenance, a fresh run ID, output writability
and the existing training-role GPU preflight. It does not train. `train` performs
the same checks and delegates to the existing U-Net trainer. It does not bypass
the protocol, hardware or image-identity gates.

The initial config uses the existing 256-pixel letterbox, width 32, batch 2,
two pilot epochs, FP16 and no augmentation. The second epoch leaves room to
exercise recovery from epoch one without changing the experiment fingerprint.
Validate real loss, finite backward/update,
mask output quality, memory, artifact integrity and recovery on the pilot before
running the full population. A successful CPU test does not establish those facts.

## 4. Recover and evaluate

Checkpoints are saved at epoch boundaries. There is no guaranteed recoverable
checkpoint before the first epoch finishes. To resume, retain the original
fingerprint and full parent run, set a new run ID, bind its actual protocol, then:

```bash
python scripts/gpu/unet.py train --config /private/project/configs/unet-resume.json \
  --resume /private/project/runs/unet/parent/epoch-0001.pt
```

The filename is an example, not a pre-existing result. Use a checkpoint that has
not already reached the configured total epoch budget. An interrupted one-epoch
pilot can be restarted if it has no checkpoint; it cannot resume beyond its
completed budget. The generated two-epoch pilot can exercise an interrupted
epoch-two run from the intact epoch-one checkpoint. Checkpoint content and config/manifest/provenance hashes must
match. Keep `best.json` and the selected earlier best checkpoint with the parent.

Standalone validation uses the existing `cxr_research evaluate --config ...
--checkpoint ...` command, a fresh run ID and the policy's inference hardware
profile. The current CLI deliberately does not run a final test set.

## Source checks before commit

```powershell
.\.venv\Scripts\python.exe scripts/ci/check_repository.py --scope repository
.\.venv\Scripts\python.exe -m compileall -q src scripts tests
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
git diff --check
```

Stage explicit reviewed source paths. Do not stage `.env`, data, row-level
manifests, inventories, masks, weights or checkpoints. Include the new adapter,
script, template and tests together. Review staged contents and run the staged
repository check before creating a commit.

## Local MIMIC and NIH training

UNET-LOCAL-2026-10-30 records the user's request to train locally, followed by
their choice to use MIMIC plus NIH and defer VinDr until patient grouping exists.
The local audit found no patient, study or SOP identity in any of 18,000 VinDr
DICOM headers. Image IDs must not be substituted for patient IDs.

`scripts/gpu/unet.py prepare-pool --spec <private-spec.json>` prepares the full
MIMIC/NIH population. The spec fixes `datasets` to
`["mimic-cxr", "nih-chestxray14"]`, `purpose` to `training`,
`image_limit_per_split` to `null`, and `split_policy` to
`mimic-official+nih-patient-hash-v1`. It retains the original source/access fields
and adds `sources.nih_images`, `nih_validation_fraction: 0.1` and
`nih_split_seed: 42`. The catalog report and exports must match their pinned hashes.

MIMIC retains its official split and union source-VQA test-patient seal. NIH
retains its official test. SHA-256 of seed, namespace and patient assigns each
NIH `train_val` patient to one development split. The audit covers the entire
included source population before view, RCA or mask filtering. It rejects
patient/path conflicts and duplicate image bytes across development splits.

The manifest stores `mask_format: chexmask_rle` and a bounded byte reference
instead of writing a PNG for every mask. The source CSV hash is verified once
per load, and every source image has its own verified hash. During loading,
the reference must match the source image ID and original dimensions. Both
lungs must be nonempty. The loader decodes their union with the existing
one-based row-major decoder, then applies the same paired letterbox as the PNG
path. No test mask or QA payload is decoded during preparation. Full pixel
decoding happens during training, so an invalid selected RLE/image still stops
the run rather than being silently dropped.

Fully opaque RGBA PNGs use the existing QC rule: discard alpha only when its
entire range is 255, then convert to grayscale. Non-opaque alpha and unsupported
bit depths stop preparation/loading. A spec may select
`publisher_hash_policy: verify_at_train_boundary` to retain the pinned publisher
hash while assembling the manifest. The files backend still hashes every image
and CSV before GPU/model startup. Images without publisher hashes are hashed
during preparation. A completed preparation is not a passed training data gate.

Use the `files` I/O backend for these references. PNG shard packing rejects RLE
references before writing output. The added sample fields also change per-sample
shard identities; retain old caches with their source version or regenerate them.

The approved local protocol selects `hardware_profile: local-unet-rtx3060`.
The runner verifies U-Net `S1`, then invokes `--role train-unet-local`, requiring
exactly NVIDIA GeForce RTX 3060 and `sm86`. All Linux, driver, CUDA and dependency
checks remain active. The normal training role and MedGemma permissions do not
change. The initial full run is bounded to one epoch, seed 42, width 32,
256 pixels, FP16, batch 2 and learning rate 0.0002. The explicit user request is
the execution authorization; do not invent a lead's approval or completed review.

Mount source and input data read-only, outputs separately, and disable container
networking. Record the immutable image ID and the actual source worktree state.
`progress.json` reports aggregate batch progress every 100 batches and after the
first batch. Epoch checkpoints, `history.json`, `best.json`, `metrics.json` and
`run.json` retain their existing meanings. A running job has no completed
validation result until an epoch finishes. One-seed results measure exploratory
agreement with anatomical pseudo-labels.

For a slow Windows bind mount, prepare lossless indexed input shards on CPU:

```powershell
.venv/Scripts/python.exe scripts/gpu/unet.py cache --config <private-local-config.json> --output <new-private-cache-directory>
```

The default stops on any empty lung target. QC-UNET-2026-10-30 records the
user's approval to exclude exactly 45 MIMIC training examples with both lungs
empty. For that specific cohort, add `--empty-mask-policy exclude-both-empty
--expected-exclusions 45 --qc-decision QC-UNET-2026-10-30`. One empty side,
other sources/splits and a mismatched exclusion count still fail. The retained
cohort has 306,302 training and 10,790 validation images. Validation is unchanged.

The cache hashes source assets, decodes only development examples, uses the
existing paired PIL letterbox, then stores the valid rectangle as lossless PNG.
It verifies the image, mask and valid pixels after re-letterboxing for every
pair, including PNG round-trip. The index fixes size and geometry; a loader
with another resolution is rejected. Select `webdataset` with `decoder: pil`
and bind the child manifest, provenance and index hashes to a new run protocol.
Keep the original canonical preparation and all failed attempts.

For local Docker training, copy completed shards into a private Linux volume,
verify every target checksum, mount inputs and source read-only, and run the
full shard validation before training. The local one-shot supervisor records
cache, copy, validation and training phases in its private `pipeline.json`.
Only actual batch progress proves that optimization has started. An approved
source export can use `CXR_SOURCE_SNAPSHOT` and `CXR_SOURCE_ARCHIVE`; the runner
checks the archive and every exported file against the protocol and retains
the original Git commit and dirty state in `run.json`.
