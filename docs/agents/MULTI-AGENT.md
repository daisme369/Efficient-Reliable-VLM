# Multi-agent orchestration

Read [AGENTS-INSTRUCTIONS.md](../../AGENTS-INSTRUCTIONS.md) first. This module applies when the task routing table names it.

## 18. Multi-agent orchestration policy

The main agent owns the task, the final decisions, all state-changing actions, and the completion report. Subagents provide bounded evidence or implementation units. Delegation does not transfer accountability.

### 18.1 Decide whether to delegate

The main agent may perform read-only discovery before choosing an execution mode. It must then record the decision in the task contract.

For every non-trivial task, the first progress update must state:

```text
Execution mode: single-agent or multi-agent
Subagents: count, roles, and independent outputs
Model and effort: selection for each role
External research: mode and reason
Write ownership: shared-checkout writer and any isolated worktrees
Verification: independent reviewer or final checks
```

If the main agent selects single-agent execution, give one short reason. Do not expose private chain-of-thought. Report the operational decision and its evidence needs only.

Use a single agent when any of these conditions applies:

- The task has one bounded output and one clear verification path.
- The task changes one small set of related files.
- Parallel work would require agents to write the same file, branch, configuration, experiment registry, or external record.
- The coordination cost is likely to exceed the work saved.
- The task contains restricted data that subagents do not need.

Use subagents when the work has independent questions that can be answered without shared writes. Suitable cases include:

- Comparing architecture options against fixed criteria.
- Reviewing separate modules or interfaces.
- Testing independent debugging hypotheses.
- Searching distinct literature themes or benchmark families.
- Running independent read-only security, reliability, performance, or reproducibility reviews.
- Verifying a completed implementation independently from the author.

Do not spawn subagents only to increase activity. If the main agent can complete and verify the task directly, keep the task single-agent.

### 18.2 Choose the number of subagents

Count independent workstreams, not subtasks or files.

| Task shape | Default delegation |
| --- | --- |
| One clear workstream | No subagent |
| One implementation plus independent research or verification | One subagent |
| Two independent technical questions | Two subagents |
| Architecture, debugging, or research with three distinct evidence streams | Up to three subagents |
| More workstreams than available concurrency | Run bounded waves and finish one wave before the next |

The main agent occupies one concurrency slot. Never request more subagents than the platform can run. The default maximum is three subagents. Exceed that limit only when the user asks for broad parallel coverage or the task contract justifies the added coordination cost.

Do not allow recursive delegation by default. A subagent may create a child agent only when the main agent reserves the slot and records the child task, scope, and output.

### 18.3 Choose the model and reasoning effort

Use the cheapest model and lowest reasoning effort that can meet the acceptance criteria. Prefer inherited settings when an override has no clear benefit.

| Work type | Model class | Default effort |
| --- | --- | --- |
| File inventory, formatting check, bounded extraction, or mechanical comparison | Fast model | Low or medium |
| Normal implementation, unit tests, documentation, or known-pattern debugging | Workhorse model | Medium or high |
| Architecture trade-off, difficult root-cause analysis, statistical review, or multi-paper synthesis | Workhorse or frontier model | High or extra high |
| Novel method design, conflicting clinical evidence, or a high-impact safety decision | Frontier model | Extra high or maximum |

Use the model identifiers available in the current environment. If the preferred class is unavailable, inherit the parent model. Use maximum or ultra effort only when the user requests it or the main agent records why lower effort is inadequate.

Do not assign a strong model to a vague task. First narrow the question, expected evidence, and output format.

### 18.4 Decide whether external research is required

The main agent must choose one research mode before delegation:

- `none`: the answer comes from repository files, local artifacts, locked project documents, or existing evidence.
- `official-docs`: the task depends on current APIs, software behavior, licenses, model cards, dataset access rules, or venue requirements.
- `web-research`: the user asks for current information, or facts may have changed since the local documents were written.
- `primary-papers`: the task evaluates novelty, related work, clinical claims, benchmark design, or research methodology.

For technical research, prefer official documentation. For scientific research, prefer the original paper, official proceedings, dataset paper, model card, or registered protocol. Do not use a search-result summary as evidence.

When multiple research agents are useful, partition them by question. Examples are clinical grounding, uncertainty and selective prediction, PEFT and efficiency, datasets and benchmarks, or evaluation methodology. Do not send multiple agents the same broad query.

Every research subagent must return:

- The exact question it investigated.
- Search scope, query terms, and date cutoff when relevant.
- Sources with stable links, DOI, or repository identifiers.
- Findings separated from inference.
- Inclusion and exclusion reasons for papers used in a comparison.
- Conflicts, missing evidence, and remaining uncertainty.

Never include MIMIC content, identifiers, restricted outputs, credentials, or unpublished row-level results in a web query or external tool call.

### 18.5 Define each work packet

Before spawning an agent, give it a bounded work packet:

```text
Parent Jira issue:
Role:
Question or outcome:
Inputs and source-of-truth files:
Files or state it may read:
Files or state it may write:
Explicit exclusions:
Required tools or skills:
Model class and effort:
External research mode:
Evidence and verification required:
Return format:
Stop conditions:
```

Every work packet inherits the parent task contract, privacy rules, Git restrictions, and scope-change protocol. A subagent cannot broaden the research question, change a locked baseline, install tools, create external records, commit, push, or publish without direct authorization.

### 18.6 Separate shared state before parallel work

The main agent is the only writer to the shared checkout by default. Assign subagents read-only analysis, source inspection, test design, literature review, or patch proposals.

- Do not let two agents edit the same file, branch, experiment registry, dataset manifest, Jira issue, or Confluence page concurrently.
- Give each agent a separate output file or state directory when parallel writing is necessary.
- Use separate worktrees or branches only when the user or task contract authorizes them.
- Serialize changes when one canonical file must be updated. One agent writes, then the next agent reads the updated version.
- Give each experiment agent a unique experiment ID, output directory, seed allocation, and GPU allocation.
- Do not let parallel experiments share a mutable checkpoint, cache, port, temporary directory, or result file.

Instructions to "avoid conflicts" are not concurrency control. File ownership, separate state, or a single writer must enforce the boundary.

### 18.7 Use role patterns for common tasks

For architecture or design work:

1. The main agent defines constraints, interfaces, decision criteria, and rejected scope.
2. One or two subagents propose distinct options.
3. An independent reviewer compares the options for complexity, testability, compute, privacy, and research validity.
4. The main agent records one decision before implementation starts.

For implementation:

1. The main agent freezes interfaces and file ownership.
2. Subagents inspect separate components or propose isolated patches.
3. One writer integrates changes in dependency order.
4. An independent verifier runs behavior-level checks and reviews the diff.

For debugging:

1. One subagent reproduces the failure and collects minimal evidence.
2. One subagent traces control, data, or dependency flow with source inspection and GitNexus when available.
3. One subagent challenges the leading hypothesis or designs a regression test.
4. The main agent selects the root-cause explanation. One writer implements the fix.

For research-paper analysis:

1. Partition papers by a declared theme or inclusion rule.
2. Require a common extraction schema for method, data, metrics, compute, limitations, and relevance.
3. Deduplicate papers before synthesis.
4. The main agent checks every claim against the cited source and writes the final synthesis.

### 18.8 Collect, review, and merge results

Subagents must return concise evidence, not raw context dumps. Each result must include:

```text
Outcome:
Evidence:
Files or sources inspected:
Commands or checks run:
Assumptions:
Risks or disagreements:
Recommended next action:
```

The main agent must:

1. Wait for every required work packet to finish or reach a declared stop condition.
2. Reject findings that lack evidence or exceed the assigned scope.
3. Resolve conflicts by checking source files, tests, official documentation, or primary papers.
4. Integrate one coherent result. Do not paste independent reports together unchanged.
5. Run final verification against the real combined artifact.
6. Report which work used subagents, what each contributed, and which decisions remained with the main agent.

If an agent fails, repeats another agent's work, or returns no usable evidence, do not keep spawning replacements without changing the work packet. Narrow the question, change the evidence source, or return the task to the main agent.

### 18.9 Repair low-quality subagent output

Treat every subagent result as untrusted until the main agent checks it against the work packet and the parent acceptance criteria. Agent confidence, response length, or agreement between agents is not evidence of correctness.

Classify the result before requesting a repair:

| Verdict | Conditions | Main-agent action |
| --- | --- | --- |
| `accept` | The output meets the assigned criteria, cites or produces evidence, stays in scope, and passes its checks. | Integrate it and run combined verification. |
| `revise` | The premise is sound, but the output has specific and bounded gaps. | Request one targeted correction from the same agent. |
| `reject` | The premise is wrong, the output is unsafe or out of scope, evidence is absent, or repair would require a rewrite. | Do not integrate it. Reframe, reassign, or return the work to the main agent. |

Reject a result immediately if it exposes restricted data, changes a locked research decision without approval, fabricates evidence, uses the test set for selection, or performs an unauthorized state change.

Evaluate the result against these gates:

- Correctness: the result answers the assigned question and matches source files, tests, official documentation, or primary papers.
- Completeness: every required deliverable and acceptance check is addressed.
- Evidence: claims have citations, commands, test output, measurements, or file references.
- Reproducibility: another agent can repeat the procedure from the recorded inputs and settings.
- Scope and safety: the result respects file ownership, privacy, Git rules, compute limits, and research locks.
- Integration quality: the result is compatible with the frozen interfaces and does not create hidden work elsewhere.

Do not send vague feedback such as "improve this" or "try again." Send a repair packet:

```text
Verdict: revise or reject
Failed criteria:
Evidence of failure:
Parts to keep:
Required changes:
Parts that must not change:
Required verification:
Retry number:
```

Use this repair sequence:

1. Preserve the failed output as evidence, but do not merge, stage, commit, cite, or publish it.
2. Identify the failure type before changing the model or prompt.
3. If the premise is sound and the gaps are bounded, give the same agent one targeted repair attempt.
4. If the repair fails, choose one response: assign a new agent with a rewritten work packet, increase model capability or effort, or return the task to the main agent.
5. After two failed attempts at the same acceptance gate, stop retrying the same premise. Write down the shared premise and test whether the task, interface, evidence source, or acceptance criterion is wrong.
6. After a successful repair, rerun the original acceptance checks. Do not lower the gate to accept the new output.

Match the response to the failure:

| Failure type | Required response |
| --- | --- |
| Ambiguous or oversized work packet | Narrow the question, split independent outputs, and restate the acceptance checks. |
| Missing repository context | Add the exact source-of-truth files, symbols, configs, or GitNexus context. |
| Missing or weak citations | Restrict the search to official documentation or primary papers and require claim-to-source mapping. |
| Reasoning or capability mismatch | Assign a stronger model or higher effort only after the work packet is specific. |
| Conflicting subagent conclusions | Ask an independent verifier to adjudicate with shared evidence and fixed criteria. |
| Poor implementation | Discard the patch, preserve a failing test or reproduction, and assign one writer to implement the smallest fix. |
| Poor architecture proposal | Recheck constraints and decision criteria. Require at least one viable alternative before selection. |
| Failed experiment analysis | Audit data version, split, config, seed, metric definition, and hardware before interpreting results. |
| Repeated process mistake | Add a test, schema, lint, template, or CI check instead of adding another reminder. |

When a subagent wrote to an authorized isolated worktree, keep its work isolated until review passes. When a subagent changed the shared checkout despite the policy, stop other writers, inspect the exact diff, and repair only the agent-owned paths with non-destructive edits. Do not use a broad reset or delete unrelated user work.

The main agent must report material rejected work when it affected schedule, compute, scope, or the final approach. State why it failed and whether the task was repaired, reassigned, deferred, or returned to the main agent.

