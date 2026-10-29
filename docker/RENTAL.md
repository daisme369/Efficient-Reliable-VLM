# Prepare a rented GPU run

Run source commands from the C checkout. Keep restricted inputs and generated
outputs under the E data root. Nothing in this procedure provisions a machine,
uploads data, accepts model terms, approves a protocol, or publishes an image.

## Prepare the existing local data

Create a new private draft directory from the existing catalog and completed QC:

```powershell
.\.venv\Scripts\python.exe scripts/gpu/prepare_rental.py `
  --data-root E:/Project/Efficient-Reliable-VLM/data `
  --qc-report E:/Project/Efficient-Reliable-VLM/data/qc/2026-10-19-processing-v1/report.json `
  --output E:/Project/Efficient-Reliable-VLM/data/rental/next-run
```

The command verifies catalog export checksums and QC input linkage, then writes
`catalog-preparation.json`, `b1.json`, `protocol-b1.json`, and `readiness.json`.
It refuses to overwrite existing drafts. These drafts do not authorize training.
They list unresolved fields without printing questions, answers, or identifiers.

Complete the preparation specification before running the existing converter:

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
.\.venv\Scripts\python.exe -m cxr_research prepare-catalog `
  --spec E:/Project/Efficient-Reliable-VLM/data/rental/next-run/catalog-preparation.json
```

The pilot data decision selected under user delegation on 2026-10-19 uses the
original QA-linked AP/PA image. It preserves Unicode-normalized source answers,
joins multiple answers in source order with `; `, and excludes empty answer sets
from supervision while retaining them in the catalog, QC, and exclusion sidecar.
This serialization is a project pilot choice, not an official evaluation rule.

Use `semantic_type` to map `verify` and `choose` to `closed`, and `query` to
`open`, following the [release schema](https://physionet.org/content/mimic-ext-mimic-cxr-vqa/1.0.0/).
That release uses a representative frontal AP/PA image for each study and permits
empty answer arrays. Empty sets must not become negative clinical labels.

Primary template eligibility remains unreviewed. An empty
`primary_template_ids` list marks every retained QA as secondary. This permits
data preparation without claiming that the primary benchmark or clinical labels
have been validated. Official MIMIC splitting and the union test seal remain in
effect. CheXmask masks and Chest ImaGenome annotations remain separate references.

## Build the locked image before renting

```bash
docker build --platform linux/amd64 -f docker/Dockerfile.gpu \
  -t efficient-reliable-vlm:rental-verified .
docker image inspect efficient-reliable-vlm:rental-verified --format '{{.Id}}'
```

The image contains dependencies, not source code, data, credentials, or weights.
Record the actual image ID and deploy that exact image. Build success does not
establish GPU functionality. See `IO_SMOKE_STATUS.md` for earlier smoke evidence.
Do not substitute an older smoke image for the final Dockerfile without recording
that difference.

## Vast.ai account and model preparation

Install the provider CLI in its own local environment using pip. It is not a
training dependency:

```powershell
.\.venv\Scripts\python.exe -m venv .test-tmp/vastai-cli
.test-tmp/vastai-cli/Scripts/python.exe -m pip install -r requirements/vastai-cli.txt
.test-tmp/vastai-cli/Scripts/vastai.exe --version
.test-tmp/vastai-cli/Scripts/python.exe scripts/gpu/rental_access.py --env-file .env credentials
.test-tmp/vastai-cli/Scripts/python.exe scripts/gpu/rental_access.py --env-file .env vast-status --storage-gib 350
```

The helper accepts `vastai_api_key` or `VAST_API_KEY`, and
`hf_full_access_token` or `HF_TOKEN`, from the local file. It never sources that
file as shell code, prints credentials, or puts keys in command arguments.
Its Vast operations authenticate and search on-demand offers only. The search
requires one H100/H200 (`sm90`) or RTX PRO 6000 Blackwell (`sm120` plus model identity), Linux amd64, driver 580+, CUDA 13 support, a
verified datacenter and reliability above 0.99. Advertised specifications still
need the real training preflight. A datacenter filter is not a data-use approval.

The helper searches Hopper and RTX PRO 6000 Blackwell separately, restricting
Blackwell model names before the provider's result limit, then merges offers by
price. It accepts the full Blackwell family name and official edition suffixes;
ambiguous provider abbreviations are excluded until their identity is verified.

After the account holder accepts the MedGemma model terms, download the pinned
files to private storage before renting:

```powershell
.\.venv\Scripts\python.exe scripts/gpu/rental_access.py --env-file .env cache-model `
  --config E:/Project/Efficient-Reliable-VLM/data/rental/2026-10-19-b1-v2/b1-pilot.json `
  --cache E:/Project/Efficient-Reliable-VLM/data/model-cache
```

The cache command checks metadata sizes and LFS SHA-256 hashes, then loads the
processor offline. It does not load the full model or prove BF16 LoRA/QLoRA compatibility.
Interrupted downloads remain cached for a retry. No dataset is sent to Hugging Face.

Before paying for a selected offer, review its specific host's access and retention
terms for restricted data. [Vast documents host access to instance files](https://github.com/vast-ai/docs/blob/main/guides/instances/manage-instances.mdx).
Keep the local access review distinct from authorization to transfer MIMIC to that host.
Prepare synthetic checks while that review is pending.

Plan disk capacity at creation: 64 GiB is a starting allocation for the small
pilot; 350 GiB is a provisional files-backend full-run allocation, subject to the
current selected-image inventory, model, image and checkpoint budget. Tar shards
need additional capacity if the original files are also retained. These sizes
are capacity proposals, not measured training requirements.

Vast runs the image directly; do not try to run Docker inside the rental.
Publish the verified dependency image only after registry authorization, and
record its registry digest as well as its image ID. Validate the selected launch
mode before data transfer: Vast SSH mode injects its own startup code, while the
base image runs as `researcher`. Generate a separate SSH transport recipe from
the canonical Dockerfile; it appends pinned OpenSSH, rsync and curl packages and
uses root for provider startup. Its package versions and source recipe hash are
recorded next to the generated file. The build context allowlist stays unchanged.

```powershell
.\.venv\Scripts\python.exe scripts/gpu/prepare_vast_image.py `
  --output E:/Project/Efficient-Reliable-VLM/data/rental/next-run/vast-image/Dockerfile.vast
docker build --platform linux/amd64 `
  -f E:/Project/Efficient-Reliable-VLM/data/rental/next-run/vast-image/Dockerfile.vast `
  -t efficient-reliable-vlm:vast-verified .
```

The recipe removes build-generated SSH host keys. Host keys and the account's
public SSH key belong to instance startup. Use the provider's SSH launch mode;
the image's default command simply waits. Run research commands as `researcher`
with access-controlled private mounts and explicit environment variables.
Local SSH validation does not prove the provider's injected startup works.
See [Vast's execution environment](https://github.com/vast-ai/docs/blob/main/guides/instances/docker-environment.mdx).
Keep private data, model cache and run artifacts outside the source checkout.
Stopped instances can still incur storage charges; back up outputs before destruction.

## Stage storage before starting GPU billing

Restore the exact Git checkout and overlay any audited development source bundle.
Keep its base commit, dirty state, and file hashes. A clone alone omits uncommitted
preprocessing changes, while a source ZIP alone does not restore Git provenance.
Exclude credentials and restricted material from source staging.

The default remote configuration uses:

```text
/workspace/repo/                 source checkout, read-only
/private/project/data/           selected images and prepared manifests, read-only
/private/project/configs/        resolved run configuration and reviewed protocol
/private/project/cache/hf/       private persistent Hugging Face cache, writable
/private/project/runs/           private persistent run outputs, writable
```

The data root is mounted separately from the source checkout. Preserve manifest
relative paths. Use the exact selected model revision in the private cache after
the account holder accepts its terms. Neither script downloads weights or sends
restricted content to a remote service. Review the target storage and access
terms before transferring any restricted data.

Ensure UID 10001 can write cache and runs, and that Git can read the staged
checkout with its actual ownership. Keep secrets in the provider's runtime secret
mechanism. Verify volume retention separately from container lifetime.

Use one visible H100, H200 or RTX PRO 6000 Blackwell for training, the locked Linux amd64 image, NVIDIA
Container Toolkit, and host driver release 580 or newer.

## Check the container, then start the pilot

Inside the container, set `CXR_CONTAINER_IMAGE_ID` to the actual immutable image
ID obtained from the deployed image. Complete the configuration with container
paths, an immutable model revision, the real Jira issue, and a reviewed protocol
whose config, manifest, and provenance fingerprints match those paths.

```bash
cd /workspace/repo
python scripts/gpu/bootstrap.py \
  --config /private/project/configs/b1.json \
  --cache /private/project/cache/hf
```

The default command checks storage permissions, canonical data, protocol,
Git provenance, shard contents when selected, the training GPU profile, cached
weight-file presence, and offline processor loading. It does not load the full
model or start training. A cache presence check does not prove weight integrity
or that BF16 LoRA/QLoRA will fit in memory. The current B0/B1 templates explicitly
select BF16 unquantized weights with `medgemma.quantization: "none"`; follow the
[profile and migration notes](../configs/README.md#medgemma-bf16-lora-profile)
when preparing a private config. The legacy NF4 default is not the BF16 profile.

After these checks pass, start a separately prepared small pilot:

```bash
python scripts/gpu/bootstrap.py \
  --config /private/project/configs/b1-pilot.json \
  --cache /private/project/cache/hf --train
```

Use a pilot manifest and its matching protocol. Do not call the full dataset a
short pilot or add nonexistent `--max_steps` flags. Validate real model loading,
supervised forward/backward, loss, memory, checkpoint export, and recovery before
starting the full run. No GPU or full-model validation is implied by CPU tests.

For bounded data preparation, set `preparation_purpose: smoke_only` and
`image_limit_per_split: {"train": 32, "validation": 8}` in a separate preparation
specification with a new output directory. The converter selects lexical image
IDs from eligible development links independently of answer contents, retaining
their QA after the declared supervision policy. Image limits are not QA limits.
It still audits the full official image population and test seal. The resulting
manifest is for smoke tests, not a representative performance benchmark.

## Recover and finish

Use a new run ID with the same scientific settings and an intact parent checkpoint:

```bash
python scripts/gpu/bootstrap.py \
  --config /private/project/configs/b1-resume.json \
  --cache /private/project/cache/hf \
  --resume /private/project/runs/parent-run/checkpoint-100 --train
```

`checkpoint-100` is an example path. Use an existing checkpoint from the actual
run. The trainer currently saves at epoch boundaries, so a job interrupted before
its first epoch ends may have no recoverable checkpoint. Keep the parent run for
identity and best-checkpoint references. The trainer performs the final parent
model identity check when loading the real model.

Verify `run.json`, `metrics.json`, adapter files, and checkpoint hashes. Back up
private artifacts and confirm their persistence before terminating compute.
Standalone evaluation uses the approved inference hardware profile; H100/H200
remain training hardware under the repository policy, while RTX PRO 6000 Blackwell supports both roles.
