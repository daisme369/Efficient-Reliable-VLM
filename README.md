# Efficient and Reliable VLM for Radiology VQA

Research repository for an efficient, clinically grounded, and selectively reliable Radiology Visual Question Answering system.

## Automation

The current CI validates repository safety controls and dependency-free unit tests. CD creates an audited research-source bundle and a draft GitHub Release; it does not deploy a clinical system or publish model weights.

See [CI-CD.md](CI-CD.md) for local commands, release behavior, security boundaries, and required GitHub settings.

## Environment

GPU research workloads use the locked Linux Docker environment. Build it from the repository root:

```bash
docker build --platform linux/amd64 --file docker/Dockerfile.gpu \
  --tag efficient-reliable-vlm:py312-cu130 .
```

See [docker/README.md](docker/README.md) for the H100/H200 training profile, the RTX 4090/5090/RTX PRO 6000 Blackwell inference profile, host requirements, runtime preflight, and data-security boundary.

For local CPU development, install the pinned foundational packages into `.venv` with:

```powershell
$env:UV_CACHE_DIR = Join-Path (Resolve-Path '.').Path '.uv-cache'
uv pip install --python .\.venv\Scripts\python.exe --requirements requirements.txt
```

See [DEPENDENCIES.md](DEPENDENCIES.md) for package scope, licenses, platform locks, verification, and deferred CUDA optimizations.
