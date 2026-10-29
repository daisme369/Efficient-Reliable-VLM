# Indexed WebDataset and DALI input pipeline

The new backend is opt-in. Existing configs default to `io.backend: files` and
`io.decoder: pil`. B0 generation, B1 training/evaluation and U-Net accept the
sharded backend. YAML sweeps inherit the same `io` section through `base`.

## Prepare once

Run on the existing CPU environment using an ordinary experiment config and its
audited canonical manifest. For MedGemma, the exact processor revision must
already be in the private Hugging Face cache; packing never downloads weights
or loads the model. It does load the processor. U-Net packing needs no processor.

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
.venv/Scripts/python.exe -m cxr_research pack --config C:/private/b1.json --output C:/private/shards --shard-size-mb 512
```

The output directory must be new. For real data it must be outside the source
checkout. The packer verifies source assets, excludes test/calibration payloads,
and writes split-specific uncompressed `.tar` shards, `index.json` and
`pack.json`. A shard may exceed the target size by one image group; an image and
its QA records are never split between shards. Original JPEG/PNG bytes are
preserved without re-encoding. Each image appears once per split, with all of
its task-specific QA records in the matching `.json` member. Masks use matching
basename extensions. Keep this entire output private.

Packing retains the source files and creates another copy of selected image
bytes. Budget disk space for both copies plus tokens/indexes; a 1 TB SSD cannot
hold two 600 GB copies. Pack only the selected manifest population or use a
separate private destination disk. The packer never deletes source data.

This is the [WebDataset tar format](https://github.com/webdataset/webdataset/blob/main/README.md)
with an indexed, local, map-style reader. It does not use the Python
`webdataset` streaming API. It supports random access for the finite epoch and
resume behavior of Hugging Face Trainer. It is not an HTTP/object-store reader
or a guarantee of purely sequential I/O. Keep shards on the training host's
local SSD. The index records offsets, source bindings and content hashes; the
reader never extracts tar files or deserializes pickle.

## Configure runs

Copy [io_webdataset.example.yaml](io_webdataset.example.yaml) into the sweep's
`base`, or use equivalent fields in a single JSON config. Set `shard_index` to
an absolute path and `shard_index_sha256` to the checksum printed by `pack`.
On CPU use `decoder: pil`. On the approved Linux CUDA environment use `dali`.
Training uses H100, H200 or RTX PRO 6000 Blackwell under the existing GPU preflight.
The RTX 3060/4090 U-Net training exception remains pending; RTX 5090 is inference-only.

```bash
PYTHONPATH=src python -m cxr_research validate --config /private/project/b1.json
PYTHONPATH=src python -m cxr_research train --config /private/project/b1.json
PYTHONPATH=src python -m cxr_research sweep --config /private/project/sweep.yaml
```

`validate` checks full shard checksums. During training, manifest metadata and
patient splits are audited, the pinned index is checked, and every uncached
member read is checked against its recorded hash. This avoids re-reading all
original images before every sweep run. Original image/mask files are no longer
required for a shard-backed run, but canonical manifest/provenance files are.
Do not modify a mounted dataset during a run: cached, previously verified bytes
are reused. Rebuild changed datasets and bind the new index checksum.

The `io` settings participate in config fingerprints. Record new protocol
fingerprints before real runs. Existing checkpoints made before this config
schema addition will not match newly computed fingerprints; never edit their
recorded hashes just to bypass the mismatch.

## Token cache and batching

Packing uses the real multimodal processor to produce both supervised and
prompt-only token fields. It retains image token expansion, attention masks,
token type IDs and answer-only labels, plus expected pixel tensor shapes.
The cache identity includes model revision, processor/image-processor config,
tokenizer vocabulary, normalization, merge rules and special tokens, chat template,
prompt, maximum length and Transformers/tokenizers versions. Changes invalidate
the cache. Seeds and LoRA settings
can reuse a cache, but a changed prompt or processor requires new shards.

Training batches use right dynamic padding; generation uses left padding and
never feeds cached reference answers. CPU workers collate labels before the
Trainer counts supervised tokens for gradient accumulation. Length bucketing
uses full multimodal token lengths, a seeded shuffle and bounded sorting windows.
All samples occur exactly once per epoch, including the final short batch.
The sampler exposes `set_epoch` for Trainer/Accelerate. Distributed training
and variable token-budget batches are outside this single-GPU runner.

Changing worker count or decoder is not assumed to preserve bitwise resume.
The synthetic CPU Trainer test compares uninterrupted training with a restart
from a checkpoint inside an epoch and checks identical final weights. Actual
MedGemma LoRA/QLoRA checkpoint resume still needs validation with the real GPU/model
environment.

## DALI and preprocessing

The container installs `nvidia-dali-cuda130==2.3.0` through the GPU requirements.
No CUDA packages are installed in native Windows. The decoder runs only in the
main process, accepts compressed bytes, uses DALI's mixed image decoder, and
returns CHW GPU tensors through DLPack. JPEG uses available acceleration; PNG
uses DALI's supported CPU decode path. A requested DALI backend fails explicitly
if unavailable rather than silently switching backends.

MedGemma retains its checkpoint's tensor-native image processor for resizing,
pan/scan defaults, rescaling and normalization. DALI outputs do not return to
PIL/CPU. The resulting shape must match cached image-token layout. A processor
with only a PIL backend is rejected for DALI. Backend changes may introduce
pixel rounding differences; compare real processor outputs and model results
before interpreting cross-backend experiments as equivalent.

U-Net's PIL backend retains the existing paired transformations. Its DALI path
uses device tensor letterboxing and paired rotation with nearest interpolation
for masks/valid pixels. Brightness/contrast affect only the image. Tensor and PIL
interpolation are not claimed bit-identical. Their backend is recorded in the
experiment config. For controlled comparisons, keep the backend fixed.

Workers read encoded bytes only and use `spawn`. `cache_bytes_mb` bounds the
compressed-member cache **per process**, so account for workers and separate
evaluation loaders when budgeting RAM. File handles are bounded and reopened
per process. Workers are not persistent across U-Net epochs, so epoch-dependent
augmentation reaches each fresh worker correctly. Prefetch depth controls CPU
read-ahead; the DALI decoder currently processes the supplied batch without a
separate multi-batch scheduling layer.

## Verification and limits

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests/research -p test_io_pipeline.py -v
```

The CPU suite covers tar round-trip, checksums, missing original images,
split coverage, cached tokens versus a real Gemma3 processor with a tiny local
tokenizer, dynamic padding, bucket coverage, a spawned reader worker, a tiny
Trainer run with gradient accumulation/evaluation and U-Net CLI train/evaluation.
This uses synthetic images, no pretrained weights and no clinical data.

On the approved Linux/CUDA container, first pass the repository GPU preflight,
then opt into the DALI JPEG/PNG, variable-batch and tensor-lifetime check:

```bash
python scripts/ci/check_gpu_environment.py --role train
CXR_TEST_DALI=1 PYTHONPATH=src python -m unittest discover -s tests/research -p test_io_pipeline.py -v
```

The GPU tests have passed inside Docker Linux on the user-approved RTX 3060
synthetic-only profile. See [the smoke evidence](../docker/IO_SMOKE_STATUS.md)
for the image, coverage and remaining build failure. This is not native Windows
CUDA validation. Before paid full runs, verify the final container build, the real MedGemma processor/model, decoder pixel
differences, checkpoint resume and throughput/VRAM on representative approved
data. No speedup is claimed from CPU correctness tests.
