# CI/CD Contract

This repository uses GitHub Actions for lightweight, CPU-only repository checks and audited research-source releases. It does not deploy a model, clinical service, dataset, checkpoint, or adapter.

## Local gates

Run the same checks before requesting review:

```powershell
python scripts/ci/check_repository.py --scope repository
python -m compileall -q scripts tests
python -m unittest discover -s tests -p "test_*.py" -v
```

After staging an authorized change, scan the staged blobs rather than only the working tree:

```powershell
python scripts/ci/check_repository.py --scope staged
```

The safety checker rejects common restricted-data paths and formats, model weights, credential material, Git LFS pointers, oversized tracked files, notebook outputs, unsafe workflow triggers, secret references, mutable action tags, unpinned Docker base images, whole-context Docker copies, and permissive Docker build contexts. A passing scan only means that these rules passed. It is not proof that content is de-identified or free of secrets.

## Continuous integration

`.github/workflows/ci.yml` runs on pull requests, pushes to `main`, manual dispatch, and calls from the release workflow. It has read-only repository permissions, uses GitHub-hosted runners, receives no dataset or compute credentials, and runs only synthetic or repository-local tests.

Configure the `Repository quality and safety` job as a required status check in the GitHub branch protection rules for `main`. Keep the repository's default Actions token permission at read-only.

## Research-source delivery

`.github/workflows/release.yml` supports two paths:

- Manual dispatch validates and uploads a temporary source bundle. It does not create a GitHub Release.
- Pushing an exact `vMAJOR.MINOR.PATCH` tag whose commit belongs to `main` runs CI, builds the same bundle, and creates or refreshes a draft GitHub Release.

The release archive is built from an explicit allowlist in `scripts/ci/build_release_bundle.py`. It contains source, configuration, tests, approved documentation, and a manifest. The allowlist and safety scan are designed to exclude medical datasets and derivatives, model weights and adapters, raw predictions, reports, experiment outputs, credentials, and local environments; human review remains mandatory. Publishable builds require a clean Git state and record the source commit. Development bundles label their provenance as a worktree and do not claim that their bytes equal a commit. Every bundle includes SHA-256 checksums.

Create a protected GitHub environment named `release` and require a maintainer reviewer. A human must inspect the draft assets, licensing, privacy boundary, and release notes before publication. Agents must not create a tag, publish a draft, or publish a release without explicit authorization.

## Current boundary

The project does not yet contain training or inference code. CI statically checks the GPU environment policy and unit-tests its preflight logic, but it does not build the multi-gigabyte image or access a GPU. CI therefore makes no claim about model quality, clinical correctness, real GPU compatibility, reproducibility of experiments, or deployment readiness. The first rented instance must build the image and pass the role-specific preflight; add stronger gates only when the corresponding implementation and synthetic test fixtures exist.
