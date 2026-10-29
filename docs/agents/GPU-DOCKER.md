# Linux Docker and GPU execution

Read [AGENTS-INSTRUCTIONS.md](../../AGENTS-INSTRUCTIONS.md) first. This module applies when the task routing table names it.

## 22. Linux Docker and GPU execution policy

Run every GPU workload inside the Linux `amd64` environment defined by `docker/Dockerfile.gpu`; do not install or validate research CUDA packages in a native Windows environment. Treat `requirements.txt` as the cross-platform direct dependency set, `requirements/gpu-runtime.txt` as the PyPI GPU addition, and `requirements/gpu-cu130.txt` as the official PyTorch CUDA 13.0 wheel lock. Do not change either base-image digest, the PyTorch wheel index, CUDA line, or GPU-only pins as part of an unrelated task. Resolve PyPI and the PyTorch index separately; do not enable an unsafe best-match multi-index strategy.

Hardware roles are locked unless an approved dependency or experiment decision changes them:

- H100 and H200 (`sm90`), and RTX PRO 6000 Blackwell (`sm120`), are training hardware. The RTX PRO addition is recorded as HW-2026-10-29 in `DETAILED_ACTION_PLAN.md`.
- RTX 4090 (`sm89`), RTX 5090 (`sm120`), and RTX PRO 6000 Blackwell (`sm120`) are test, inference, and deployment-research hardware.
- User-approved exception, 2026-10-17: local RTX 3060 (`sm86`) may run synthetic DALI/CUDA smoke tests only, using `--role smoke`. This does not authorize real-data training/inference or research performance claims. The experiment runner continues to select only `train` or `inference`.
- The host must expose an NVIDIA release 580 or newer driver through NVIDIA Container Toolkit.

Blackwell training requires both compute capability 12.0 and the RTX PRO 6000 Blackwell model identity (Server, Workstation or Max-Q Workstation Edition). RTX 5090 remains inference-only even though it also uses `sm120`. See [NVIDIA's compute-capability table](https://developer.nvidia.com/cuda/gpus). Hardware eligibility does not replace a successful preflight on the actual host or approve a rental budget.

Before starting a paid GPU workload, run `python scripts/ci/check_gpu_environment.py --role train` or `--role inference` inside the container. Stop if the preflight fails; do not bypass the role, driver, architecture, CUDA, or dependency check. Record the successful output, image identifier, host driver, and selected devices in the experiment manifest.

The `.dockerignore` allowlist is a privacy boundary. Do not broaden the Docker build context to include datasets, credentials, model caches, checkpoints, raw predictions, or experiment outputs. Inject approved secrets only at runtime through the compute provider's secret manager and mount restricted data from access-controlled storage. Never bake those materials into an image or publish them to a registry.

