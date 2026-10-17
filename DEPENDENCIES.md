# Dependency contract

`requirements.txt` contains the pinned direct dependencies for the first chest X-ray VQA baseline. The pins were resolved on Windows x86-64 with Python 3.14.2 on 9 October 2026. That environment is for local CPU development and CI checks; it is not the research GPU runtime.

GPU workloads run in the Linux `amd64` image defined by `docker/Dockerfile.gpu`. Its Python and uv base images are pinned by digest. `requirements/gpu-cu130.txt` locks the official CUDA 13.0 builds of PyTorch 2.14.1 and TorchVision 0.29.1; `requirements/gpu-runtime.txt` locks bitsandbytes 0.50.2 and NVIDIA DALI CUDA 13 2.3.0. The earlier dependency resolution covered the environment before DALI was added. Rebuild and validate the extended environment on Linux before a GPU run.

The CUDA 13.0 profile is shared across the approved hardware, while runtime validation keeps the roles separate:

- Train on H100 or H200 (`sm90`).
- Run testing, inference, and deployment research on RTX 4090 (`sm89`), RTX 5090 (`sm120`), or RTX PRO 6000 Blackwell (`sm120`).
- Require an NVIDIA release 580 or newer host driver and NVIDIA Container Toolkit.
- Run `scripts/ci/check_gpu_environment.py` before every rented-GPU workload and record its output in the experiment manifest.

## Included packages

`pyproject.toml` packages `src/cxr_research` and reads its runtime dependencies from this repository's existing `requirements.txt`; it does not introduce a second runtime lock. The build backend is setuptools 84.0.0, the version already installed in the local development environment. Setuptools uses the MIT license. It is build tooling only; GPU pins, container images, wheel indexes and the runtime dependency list are unchanged. A source-path invocation using `PYTHONPATH=src` does not require an installation.

The dependency groups have bounded roles:

- PyTorch and TorchVision provide tensor, training, and image transforms.
- Transformers, Accelerate, PEFT, Datasets, Evaluate, and Hugging Face Hub provide MedGemma-compatible loading, data processing, and parameter-efficient fine-tuning. The MedGemma 1.5 model card requires Transformers 4.50.0 or newer.
- NumPy, pandas, SciPy, scikit-learn, Pillow, pydicom, PyYAML, and tqdm provide numerical processing, metadata handling, chest X-ray input handling, configuration, and progress reporting.
- BERTScore, ROUGE Score, SacreBLEU, and Matplotlib support the locked open-ended VQA metrics and result plots.
- safetensors, SentencePiece, and protobuf support model serialization and tokenizer or processor loading.
- bitsandbytes is confined to the Linux CUDA 13.0 profile for QLoRA and quantized inference. It is not installed in the Windows CPU environment.
- `nvidia-dali-cuda130==2.3.0` supplies mixed JPEG decoding and CPU fallback for PNG. Its published wheel targets Linux x86-64 and Python 3.10–3.14; the project declares Apache-2.0 licensing. This dependency was added for the requested I/O work, not installed or executed on Windows. See [NVIDIA's package metadata](https://pypi.org/project/nvidia-dali-cuda130/2.3.0/). CUDA/platform compatibility must still pass a real container build and smoke test.
- The Dockerfile adds Debian `git=1:2.39.5-0+deb12u3` for source checkout and run provenance. The prior image omitted Git and failed the Linux CLI training smoke test. Git is standalone GPL-2.0 tooling, not a Python runtime dependency. The version is listed by [Debian Bookworm](https://packages.debian.org/bookworm/git); installation requires the Debian repositories to be reachable. The base image and CUDA locks are unchanged.
- The local indexed reader writes and reads standard WebDataset tar shards using Python's `tarfile` and a checksum-bound sidecar index. It does not require the `webdataset` Python package and does not implement its remote streaming pipeline. This is an explicit local-storage choice for finite epochs, length grouping and Trainer resume.

The direct projects use Apache-2.0, BSD-family, MIT-family, PSF-compatible, or MPL-2.0 licensing. Binary wheels can include components under additional licenses and exceptions. Review the installed package metadata before redistributing a bundled environment. This dependency choice does not grant permission to redistribute MedGemma, MIMIC-CXR, another dataset, model weights, or adapters. Review those licenses separately.

## Deferred packages

Do not add `flash-attn`, `xformers`, DeepSpeed, vLLM, or another CUDA-specific package to the base requirements. Add one only after the backbone and measured bottleneck justify it, then lock it in a platform-specific profile. Do not move bitsandbytes into the cross-platform base requirements.

## Install and verify

Install into the repository virtual environment:

```powershell
$env:UV_CACHE_DIR = Join-Path (Resolve-Path '.').Path '.uv-cache'
uv pip install --python .\.venv\Scripts\python.exe --requirements requirements.txt
```

Verify dependency consistency and imports:

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe scripts\ci\check_environment.py
```

Create a separate lock for the selected CUDA platform before a GPU experiment. Record the Python, CUDA, driver, PyTorch, Transformers, PEFT, and GPU versions in every experiment manifest.

## Linux Docker GPU environment

Build and validate the locked GPU image as documented in `docker/README.md`. The two GPU manifests install:

```text
torch==2.14.1+cu130
torchvision==0.29.1+cu130
bitsandbytes==0.50.2
nvidia-dali-cuda130==2.3.0
```

The Dockerfile intentionally resolves PyPI packages and PyTorch CUDA wheels in separate uv operations. This avoids mixing indexes under an unsafe best-match policy while still replacing the cross-platform PyTorch pin with the explicit `+cu130` build.

The original and derived smoke images ran through Docker Desktop's WSL2 Linux daemon on the current Windows host, using synthetic data only. A clean build of the final Dockerfile remains incomplete because APT access failed, so the added Git package and final Linux CLI path still require validation. These local smoke checks do not establish compatibility on approved research hardware; the first rented instance must pass the real GPU preflight before training or inference.
