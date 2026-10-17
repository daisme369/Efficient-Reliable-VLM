# Data governance and privacy

Read [AGENTS-INSTRUCTIONS.md](../../AGENTS-INSTRUCTIONS.md) first. This module applies when the task routing table names it.

## 6. Data governance and privacy

MIMIC data is restricted. Agents must assume that images, reports, subject/study identifiers, annotations derived at row level, caches, prompts containing report text, and raw model outputs may be sensitive.

- Never commit restricted data, credentials, access tokens, signed URLs, raw reports, or patient/study identifiers.
- Do not paste restricted data into Jira, chat prompts, third-party APIs, or public experiment trackers.
- Use synthetic fixtures for unit tests and CI.
- Store only aggregate, de-identified metrics in shareable artifacts.
- Validate patient-level train/validation/test separation before training or evaluation.
- Record dataset source, license/access terms, version, checksum/manifest, exclusions, and uncertain-label policy.
- Do not rely on `.gitignore` as the only privacy control; inspect staged files and generated artifacts.

Stop immediately if a requested action could expose restricted data or violate PhysioNet/data-use terms. Report the proposed data flow and wait for a human decision.

