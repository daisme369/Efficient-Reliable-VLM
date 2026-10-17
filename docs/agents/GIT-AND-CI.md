# Git, commits, and CI/CD

Read [AGENTS-INSTRUCTIONS.md](../../AGENTS-INSTRUCTIONS.md) first. This module applies when the task routing table names it.

## 15. Git state, branches, and commit authorization

Inspect `git status` before editing and before reporting completion. Treat existing modifications and untracked files as user-owned unless the active task says otherwise.

- Do not create a commit, branch, worktree, tag, push, pull request, merge, rebase, or release unless the user or the task contract explicitly authorizes that action.
- Do not change Git user name, email, signing configuration, hooks, remotes, or credential settings.
- Do not use `git reset --hard`, `git clean`, force push, history rewriting, or destructive checkout commands without explicit human approval.
- Do not amend an existing commit unless the user explicitly asks for an amendment.
- If a branch is authorized, follow the caller's required prefix and include the Jira key in the branch name. Do not invent or switch branches when the task only asks for file changes.
- Stage explicit paths with `git add -- <path>`. Do not use `git add .`, `git add -A`, or another broad staging command when unrelated files exist.
- Inspect `git diff --check`, `git diff`, and `git diff --cached` before a commit. Verify that the staged diff contains only the active task.
- Scan staged paths for credentials, restricted MIMIC content, generated caches, checkpoints, large artifacts, and patient or study identifiers.
- Keep each commit to one verifiable change. Separate refactors, data-pipeline changes, metric changes, model changes, and generated results when they can be reviewed independently.
- Do not commit a failing state unless the task explicitly requests a failing test or a checkpoint commit. State that exception in the commit body.
- Do not commit model weights, restricted raw outputs, local environments, caches, or rented-compute credentials. Commit only small aggregate results when the active task requires them and the data policy permits them.

An issue may require multiple commits. Create a commit when a reviewer can verify one coherent outcome, not after every small edit.

## 16. Commit message contract

Use this subject format when a commit is authorized:

```text
<type>(<scope>): <imperative summary> [<JIRA-KEY>]
```

Use a subject of at most 72 characters when practical. Use the imperative form and name the behavior changed. Do not use subjects such as `update`, `changes`, `work`, `final`, or `fix stuff`.

Allowed types are:

- `feat` for a user-visible or research-pipeline capability.
- `fix` for a defect.
- `data` for a schema, loader, split, or data-validation change.
- `eval` for a metric or evaluation-protocol change.
- `exp` for a reproducible experiment config or permitted aggregate result.
- `test` for tests only.
- `docs` for documentation only.
- `refactor` for a behavior-preserving code change.
- `build` for dependencies, packaging, CI, or environments.
- `chore` for repository maintenance that fits no type above.

Examples:

```text
eval(calibration): add AURC computation [MVD-5]
data(split): reject patient overlap [MVD-3]
feat(risk): add grounding-conditioned abstention [MVD-8]
docs(governance): define agent commit policy [SCRUM-7]
```

For a non-trivial change, add a body that explains why the change is needed, what validation ran, and any data or compute effect:

```text
Why:
- Replace the previous binning rule with the locked evaluation rule.

Validation:
- Run the hand-computed calibration fixtures.

Data and compute:
- Use synthetic fixtures only. No GPU run.
```

Do not put unsupported performance claims in a commit message. Use measured values only when the protocol and artifact path are available.

## 21. CI/CD policy

The repository automation contract is documented in `CI-CD.md`. The machine-enforced controls are `.github/workflows/ci.yml`, `.github/workflows/release.yml`, `scripts/ci/check_repository.py`, and `scripts/ci/build_release_bundle.py`. Keep this policy and those controls tracked so they exist in a clean checkout.

Before review, run:

```text
python scripts/ci/check_repository.py --scope repository
python -m compileall -q src scripts tests
python -m unittest discover -s tests -p "test_*.py" -v
```

After an authorized staging operation and before a commit, also run `python scripts/ci/check_repository.py --scope staged`. Never print flagged credentials, patient identifiers, report text, or raw predictions in errors, logs, summaries, caches, or artifacts.

Public CI is CPU-only and synthetic-data-only. It must not receive PhysioNet credentials, restricted datasets, row-level derivatives, rented-GPU credentials, model-download tokens, or access to self-hosted research machines. A passing safety scan establishes only that the implemented rules passed; it does not prove de-identification, privacy, clinical correctness, or absence of secrets.

All external GitHub Actions must use a reviewed full-length commit SHA. Keep workflow permissions read-only by default, prohibit `pull_request_target`, and grant `contents: write` only to the isolated draft-release publisher. Review any change to triggers, permissions, runners, action pins, artifact paths, or release commands as a security-sensitive change.

CD means an audited research-source release until an approved decision record defines a deployment target. Release only reviewed code, configuration, synthetic fixtures, documentation, manifests, and explicitly approved aggregate metrics. Dataset content or derivatives, model weights and adapters, raw reports, raw predictions, checkpoints, and experiment directories are excluded. Redistribution of a model, adapter, or dataset-derived artifact requires a separate license and privacy decision.

A manual release workflow run may build a temporary bundle but must not publish it. A `vMAJOR.MINOR.PATCH` tag on `main` may create a draft release after CI passes. Tag creation and publication still require explicit human authorization under [Section 15](#15-git-state-branches-and-commit-authorization). Configure the GitHub `release` environment with a required maintainer reviewer, keep the default Actions token read-only, and require the CI status check on `main`.

