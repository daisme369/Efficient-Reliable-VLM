# Task execution and review

Read [AGENTS-INSTRUCTIONS.md](../../AGENTS-INSTRUCTIONS.md) first. This module applies when the task routing table names it.

## 4. Required task contract

At the start of every agent task, derive and state this contract in the work log or first progress update:

```text
Jira issue:
Goal:
In-scope files/components:
Out-of-scope work:
Inputs and locked versions:
Deliverables:
Acceptance checks:
Compute/time budget:
Stop conditions:
Reviewer:
Execution mode: single-agent or multi-agent
Delegation plan: subagent count, roles, model class, effort, and owned outputs
External research: none, official documentation, web research, or primary papers
```

If information is missing, perform only reversible discovery and local smoke-test work. List the missing decisions. Do not fill a consequential research choice with an assumption.

## 5. Smallest valid change

- Implement only the smallest change that satisfies the active issue.
- Do not refactor unrelated code, rename broad APIs, change formatting across the repository, or update unrelated dependencies.
- Preserve user changes and unrelated uncommitted work.
- New dependencies require a reason, a pinned version, license compatibility, and a record of the affected environment.
- A discovered bug outside the current acceptance criteria becomes a separate issue unless it blocks the current task.
- Never change a metric definition merely to make a result improve.

## 13. Role boundaries and review

- Model architecture and grounding logic: Lê Đức Anh owns; Giáp Hoàng Gia Khánh reviews research alignment.
- Reliability metrics, statistics, and paper claims: Gia Khánh owns; Đức Anh reviews implementation implications.
- Data schema, split, provenance, and counterfactual data: Cáp Việt Anh owns; Gia Khánh reviews validity.
- Tasks labeled `junior-task`: Nguyễn Văn Tuấn Kiệt may execute only the bounded checklist. Cáp Việt Anh or Gia Khánh must review before results are consumed.

Agents must not impersonate a reviewer or mark human review complete.

## 14. Required completion report

Every agent completion message must contain:

1. Jira issue and exact outcome.
2. Files changed and why.
3. Commands/tests/experiments run, including failures.
4. Results with units and protocol; do not use vague statements such as "performance improved."
5. Acceptance criteria met and unmet.
6. Assumptions, limitations, privacy implications, and remaining risks.
7. Proposed follow-up work separated from completed in-scope work.

Do not fabricate citations, experiments, metrics, reviewer feedback, or clinical validation. Label planned, partial, simulated, and measured results explicitly.

## 20. Maintaining these controls

When the same agent mistake or scope correction occurs twice, do not only add another prose warning. Propose a structural control such as a schema validator, split-leakage test, experiment-config validator, restricted-file pre-commit check, Jira template, pull-request checklist, or machine-readable scope lock. Keep this file concise by linking to the enforceable control after it is implemented.

