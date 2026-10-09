# Linux GPU container

The GPU environment is a Linux `amd64` container shared by the approved training and inference hardware profiles. PyTorch is locked to CUDA 13.0 so one image can cover Ada (`sm89`), Hopper (`sm90`), and client Blackwell (`sm120`). Runtime policy still separates the roles:

| Role | Approved GPUs | Compute capability |
|---|---|---|
| Training | NVIDIA H100 or H200 | `sm90` |
| Test, inference, or deployment research | RTX 4090 | `sm89` |
| Test, inference, or deployment research | RTX 5090 or RTX PRO 6000 Blackwell | `sm120` |

This is a research environment, not a clinical deployment image.

## Host prerequisites

- Linux x86-64 host.
- Docker Engine with NVIDIA Container Toolkit configured.
- NVIDIA driver release 580 or newer for CUDA 13.0.
- Enough local storage for the image, model cache, and approved data volume.

Verify the host runtime before building:

```bash
nvidia-smi
docker run --rm --gpus all \
  nvidia/cuda:13.0.2-base-ubuntu24.04@sha256:605fb0c8acf8674e164d822da8a8521f3a655056e569f0899e72ae940e1fe7dc \
  nvidia-smi
```

Do not pass PhysioNet credentials, Hugging Face tokens, MIMIC data, model weights, or checkpoints as Docker build arguments or copy them into the image. Inject credentials at runtime through the compute provider's secret manager. Mount restricted data and caches from access-controlled host paths.

## Build

Run from the repository root:

```bash
docker build \
  --platform linux/amd64 \
  --file docker/Dockerfile.gpu \
  --tag efficient-reliable-vlm:py312-cu130 .
```

The build context is restricted by `.dockerignore` to the Dockerfile and dependency manifests. Application code and research data are not baked into this environment image.

## Runtime preflight

Mount the checked-out repository and run the matching preflight before a workload.

Training on H100 or H200:

```bash
docker run --rm --gpus all \
  --shm-size 16g \
  --volume "$PWD:/workspace:ro" \
  efficient-reliable-vlm:py312-cu130 \
  python scripts/ci/check_gpu_environment.py --role train
```

Test, inference, or deployment research on RTX 4090, RTX 5090, or RTX PRO 6000 Blackwell:

```bash
docker run --rm --gpus all \
  --shm-size 16g \
  --volume "$PWD:/workspace:ro" \
  efficient-reliable-vlm:py312-cu130 \
  python scripts/ci/check_gpu_environment.py --role inference
```

The preflight fails on Windows containers, an unsupported GPU architecture, a driver below release 580, a non-CUDA-13.0 PyTorch build, or a dependency mismatch. Record the successful preflight output with the experiment manifest. Do not silently fall back to a different CUDA image; update the dependency decision and re-resolve the lock first.

For an interactive shell after a successful preflight, rerun the container with `--entrypoint bash`. Keep the source mount read-only and use separate access-controlled mounts for data, model cache, and experiment outputs. Writable mounts must be owned by container UID 10001, or the provider must map an equivalent non-root user. Use a persistent cache to avoid repeatedly downloading model files, subject to the model license and access policy.
