# Local Linux/CUDA I/O smoke evidence

Date: 2026-10-17. Jira/reviewer: not supplied. All inputs were synthetic. No
dataset download, AWS authentication, pretrained model download or paid rental
was performed. No credentials or `.env` were mounted into test containers.

## Environment actually tested

- Docker Engine 29.7.2, Linux through Docker Desktop/WSL2.
- NVIDIA GeForce RTX 3060, 12 GB; driver 610.47; approved synthetic-only exception.
- Python 3.12.12, PyTorch 2.14.1+cu130, TorchVision 0.29.1+cu130, CUDA 13.0.
- NVIDIA DALI CUDA 13 package 2.3.0.
- Original runtime image ID:
  `sha256:427d7815770ce9deafc9ff0920fa53fa0c6a7e270b76a0f46ba588ab1ab0e867`.
- Derived local smoke image `efficient-reliable-vlm:io-smoke`, image ID:
  `sha256:01bb263283e3e6ed14f5d37f7158eb7c8b624aae9290822f0cba9cb1c5e0eca0`.

The smoke image extends the existing repository CUDA image with pinned DALI.
It passed `pip check`. It is not a successful clean rebuild of the final
Dockerfile. The full rebuild was cancelled to avoid downloading the entire CUDA
stack again. No image was published. Test execution used `--network none`.

## Results

| Check | Result |
| --- | --- |
| GPU preflight `--role smoke` | Passed on RTX 3060/sm86 |
| Linux I/O suite on original image | 11 passed, 1 failed, 2 GPU tests skipped |
| DALI integration class on derived image | 2 passed, no skips |
| Complete Windows CPU ML suite after changes | 77 passed, 2 Linux GPU tests skipped |
| Repository checker, compilation and diff whitespace checks | Passed |

GPU tests covered JPEG/PNG decoding, variable batch size, tensor lifetime across
pipeline runs, GPU pixel tensors after the real Gemma3 processor with a tiny local
tokenizer, cached input/label agreement, and binary U-Net mask transforms on GPU.
The Linux CPU suite also exercised the cached Trainer with gradient accumulation
and exact restart from a checkpoint inside an epoch. No MedGemma weights were loaded.

NVML emitted CPU-affinity warnings on WSL2. These did not fail the tests. No
throughput, latency, GPU-memory or clinical claim is derived from this smoke run.

## Failure and remaining verification

The Linux CLI pack/validate/train/evaluate test failed because the original image
did not include the `git` executable required by run provenance. The Dockerfile
now adds pinned Debian Git and checks the DALI version. Git installation and the
final full-image rerun are pending: APT connections to Debian package indexes
were refused in temporary containers. Python HTTPS access to one Debian Release
URL succeeded, but that does not establish that APT package installation works.

Docker Desktop initially failed on its AppData Unix socket when started from the
agent process. Starting Docker Desktop manually from the Windows Start Menu
restored the engine. No Docker reset, volume deletion or socket modification was
performed.

Before real runs, build the final Dockerfile, rerun the CLI smoke test, then
validate the actual pinned MedGemma processor/weights and QLoRA on approved
training hardware. This evidence does not validate S3 permissions, real dataset
mapping, full-model resume, or the proposed rental bootstrap.

Local diagnostic logs are retained under ignored `.test-tmp/`:
`docker-io-build.log`, `docker-io-incremental-build.log`,
`docker-io-cpu-tests.log`, and `docker-io-gpu-tests.log`.
