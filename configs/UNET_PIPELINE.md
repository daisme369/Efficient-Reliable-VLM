# Prepare and run the U-Net branch

The U-Net branch predicts one binary mask for the union of the left and right
lungs. It starts from random weights. It does not predict the heart, individual
lung classes, lesions or VQA answers. CheXmask targets are anatomical pseudo-labels.
Dice/IoU against those targets measure agreement with the source masks, not
clinical accuracy or independent lesion grounding.

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
Docker environment as `researcher` with one visible H100/H200 and a driver of
release 580 or newer. Record the actual immutable image ID in
`CXR_CONTAINER_IMAGE_ID`. Current policy does not authorize real U-Net training
on the local RTX 3060 or an RTX inference profile.

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
