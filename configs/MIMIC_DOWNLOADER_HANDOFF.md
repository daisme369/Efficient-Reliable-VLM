# Full-JPEG downloader: handoff and benchmark procedure

Prepared 2026-10-30 for Vast 58351663. **Phases 1–2 are local implementation
and offline validation only. Nothing in this document authorizes stopping,
deploying over, or replacing the active downloader. The user must explicitly
approve Phase 3 before any live handoff.** No second manager may run against
this dataset. No QA mapping or training is started by these tools.

## Implemented replacement

`scripts/gpu/download_mimic_jpeg.py` loads the existing externally hash-bound
plan first. It checks source, publisher checksum coverage and expected count;
it never calls LIST/HEAD or creates a replacement plan. Under the dataset's
exclusive lock, `fetch_plan` rehashes final JPEGs and checks **missing bytes +
reserve**. The old launcher incorrectly required room for the full release on
every restart. Its running copy has not been patched or replaced.

The default backend is low-level boto3 `get_object`. One client is initialized
before the thread pool, with retained pools of 32/64/128 for 16/32/64 workers.
Connect/read timeouts remain 20/120 seconds. Standard SDK request retries have
four total attempts. One recognized interrupted response body may restart from
a fresh temporary file/hash; the combined upper bound is two SDK calls/eight
HTTP attempts for that object. Exhausted request failures, 412, size/ETag/hash
failures and unexpected exceptions are not body-retried.

The explicit `--backend cli` fallback uses the same plan and corrected preflight.
It retains the existing CLI conditional GET behavior; backend switching is never
automatic after an authorization or integrity error. Standalone SDK dependencies
are pinned separately in `requirements/s3-download.txt`; the research/CUDA lock
is unchanged. A later live install should use a dedicated downloader venv,
leaving `/private/project/venvs/medgemma-cu128` unchanged.

Both paths preserve IfMatch, response ETag/size, publisher SHA-256, private
permissions, file/directory fsync, atomic no-overwrite publication, SQLite
receipts, the exclusive lock and rehash-on-resume. A certificate is atomically
published only after complete verification, while that lock is still held.
An interrupted run has no completion certificate. Duplicate managers fail before
updating the active status or receipts.

## Phase 3 — after explicit user approval

### Prepare an isolated release

Keep the currently running script and source generation intact. Stage reviewed
source into a **new versioned directory**, verify its file hashes and offline
Linux tests, and provision the optional SDK in a dedicated venv. Never copy the
workstation `.env`, private plan, dataset, model cache or credentials into a source
bundle. Approved AWS credentials are supplied through the existing encrypted
SSH stdin flow into process memory, or the approved runtime environment; they
must not be put in command arguments, files, shell history or logs.

Bind the existing plan/control inputs, not a fresh S3 listing:

- Data: `/private/project/data/mimic-cxr-jpg/2.1.0`
- Plan: `/private/project/setup/full-jpeg-v1/full-jpeg-plan.json`
- Plan SHA-256: `22a3164a04f6b714e0c521403b68ce5936b42565e26a5261faef4bcb27bc6702`
- Publisher: the existing `SHA256SUMS.txt` at the image-release root
- Receipt: image-release root `/.full-jpeg-v1.sqlite3`
- Status/certificate: the existing `full-jpeg-v1` control directory
- Source-only configuration: `/private/project/configs/s3-sources.env`
- Access review: `/private/project/setup/data-access-review.json`
- Access-review SHA-256:
  `252b3004457b2f6d7a5752269d83f4f8e328d66170b5a9120e8a4c64e436f076`
- Expected JPEGs: 377110; byte budget: 751619276800; reserve: 137438953472

Reverify these bindings on the host. A previously recorded digest is not a reason
to ignore a changed file. Do not expose plan entries, filenames or credentials.

After approval and quiescence, the proposed SDK16 invocation is below. The source
and venv paths are new generations to be provisioned during the approved handoff;
they do not exist merely because this document names them. The private credential
envelope goes through approved SSH stdin, never an echoed shell command.

```bash
PATH=/private/project/tools/bin:"$PATH" \
  /private/project/venvs/s3-download-v1/bin/python \
  /workspace/cxr-download-sdk-20261030-v1/scripts/gpu/download_mimic_jpeg.py \
  --backend boto3 --workers 16 --credentials-stdin \
  --plan /private/project/setup/full-jpeg-v1/full-jpeg-plan.json \
  --plan-sha256 22a3164a04f6b714e0c521403b68ce5936b42565e26a5261faef4bcb27bc6702 \
  --publisher /private/project/data/mimic-cxr-jpg/2.1.0/SHA256SUMS.txt \
  --data-root /private/project/data/mimic-cxr-jpg/2.1.0 \
  --receipt /private/project/data/mimic-cxr-jpg/2.1.0/.full-jpeg-v1.sqlite3 \
  --status /private/project/setup/full-jpeg-v1/download-status.json \
  --completion /private/project/setup/full-jpeg-v1/full-jpeg-completion.json \
  --source-env /private/project/configs/s3-sources.env \
  --access-review /private/project/setup/data-access-review.json \
  --access-review-sha256 252b3004457b2f6d7a5752269d83f4f8e328d66170b5a9120e8a4c64e436f076 \
  --expected-images 377110 --max-bytes 751619276800 --reserve-bytes 137438953472 \
  --stop-after-seconds 210
```

The last flag requests a bounded initial benchmark window plus its observed drain
tail. Rollback uses the same command with `--backend cli --workers 16`, after the
SDK manager has fully exited. The PATH prefix exposes the already approved AWS
CLI for that fallback; the SDK backend does not invoke it. No command in this
block has been run on Vast.

### Quiesce the current worker without overlapping writers

The old Python worker was PID 18660 during the audit. **Do not signal that number
without fresh identity checks.** Verify script/executable, UID10001, process start
time, current process tree/group and lock inode/content. Store only aggregate
inspection results in chat; child command lines contain restricted object keys.

The current worker has **no cooperative SIGTERM handler**. A plain SIGTERM is an
interrupted stop, not a graceful drain. It cannot promise publication of every
completed or queued future. A controlled protocol, executed only after approval,
is:

1. Freeze the verified Python coordinator TGID with SIGSTOP, and verify all its
   threads are stopped. Its existing AWS children can finish, but it cannot
   schedule new requests or publish additional futures while frozen.
2. Wait for its already-running AWS descendants to settle, using a bounded
   operator-monitored wait. Exited/zombie children have no active file handles;
   a frozen parent cannot reap them yet. Do not confuse zombies with live writers.
   If requests do not settle within the approved wait, stop and report the state;
   do not silently force-kill or leave a coordinator frozen indefinitely.
3. Queue SIGTERM to the same verified frozen coordinator, then SIGCONT **only to
   deliver that pending terminating signal**, never SIGCONT alone. Recheck identity
   before each signal. Reap/observe supervisor exit and prove there are no remaining
   live descendants able to write. Inspect the whole recorded tree, not only the
Python PID. No replacement starts until that proof succeeds.

This drains existing CLI requests where possible, but leaves uncommitted CLI or
bulk temporary files as evidence. It does not claim a cooperative old-worker drain.
No signal code or live stop command is executed by the Phase1/2 test suite.

The new launcher has a cooperative SIGTERM/SIGINT handler: stop scheduling, drain
already-submitted work, close/checkpoint receipts and release its lock. At most
2×workers futures are submitted, so the drain includes queued as well as active
work. Observe its exit; never infer exit merely because a stop signal was sent.

### Verify consistency, then resume once

Preserve every completed JPEG, all old CLI/bulk temporaries, and the SQLite
DB/WAL/SHM files. Do not delete temporaries, checkpoint an active database, restore
a stale receipt, or discard WAL. After all writers are gone:

1. Inspect private SQLite integrity/schema, plan binding and aggregate receipt
   counts through a read-only connection that includes its WAL. Do not use an
   immutable-mode connection that ignores pending WAL state. File hashes alone
   are not a substitute for checking that logical database state.
2. Record aggregate final-file/temporary counts and disk free space. A receipt
   entry, old status counter or orphan temporary is not sufficient completion
   evidence. Finals can legitimately be ahead of receipts after interruption.
3. Verify the stale lock is still the exact old inode/owner/plan and that no old
   writer remains. Only then remove that stale lock, as part of the approved
   handoff. Never auto-clear an unknown/live lock.
4. Start **one** new launcher at 16 workers with the same receipt/plan. Its locked
   preflight rehashes all final files before any GET, rejects conflicts, preserves
   old temporaries, and downloads only absent objects. Abort on mismatch rather
   than overwriting or deleting it. A run is complete only with its full certificate.

Actual live receipt/WAL/final-file consistency is intentionally **not** asserted
by offline tests. These checks belong to the approved quiescent handoff.

### Rollback

Request cooperative shutdown of the new launcher and wait for all its submitted
work and child processes to exit. Preserve all newly completed files and the same
SQLite DB/WAL/SHM. Inspect any remaining lock as above. Resume through the
**new launcher with `--backend cli --workers 16`**, retaining the fixed preflight,
same frozen plan, publisher, receipts, reserve and access review. Never run the
old buggy restart entrypoint, restore old receipt timestamps over new work,
redownload validated finals, or overlap CLI and SDK managers.

## Phase 4 — bounded benchmark after the approved handoff

The audit baseline is 13.21 verified MB/s, about 8.19 images/sec. A 2× target is
**26.42 verified MB/s**; it is a target, not a measured improvement or guarantee.

Run SDK16 first, then SDK32 if integrity and resource checks pass. Consider SDK64
only if 32 scales and CPU, disk, memory and retry headroom remain. The code supports
64 with a bounded queue, but support is not an operational recommendation.

Use 30 seconds warmup plus 180 seconds measurement per stage. If fewer than 1000 new
objects complete, extend only within a pre-approved 300-second measurement cap;
otherwise report the smaller sample. Keep matched size distributions, fixed client
settings, region, chunking, SHA/fsync/publication and reserve. Use disjoint remaining
objects, not repeat downloads. Never count rehashed existing files as network
throughput. All stages run serially, with at most one manager holding the lock.

The launcher `--stop-after-seconds` starts its timer after rehash preflight and
stops scheduling after the selected interval, then drains submitted futures.
Record tail time/bytes separately; the timed measurement cap is not a promise
that every pending request exits instantly. Plan the drain allowance explicitly.

Record post-preflight counter/time deltas for verified MB/s and images/sec;
`elapsed_seconds` includes preflight and must not be used directly for that rate.
Record CPU/quota, disk busy/await/queue, memory, NIC rate, header/body and whole-object
timings, SDK logical calls/observed HTTP attempts, SDK/body retries and failures.
Metrics expose bounded-histogram p50/p95 **upper bounds**, not exact quantiles.
Header time includes SDK retries and connection setup, not just server latency.
CLI HTTP retry/timing fields are explicitly unavailable, not zero.

Stop escalation for integrity/ETag/size failures, reserve violations, saturation,
rising retries/503/timeouts, or a throughput plateau with worse latency. Select the
lowest worker count near the measured maximum. Never weaken validation or fsync
to reach the 2× target. No live benchmark has been run in Phases 1–2.

## Offline validation

The transport tests use injected clients and deny socket connections. Integration
fixtures contain synthetic bytes and a synthetic Access Point ARN. They exercise
stream failure/retry, request failure/timeout telemetry, permanent integrity errors,
atomic publication, duplicate managers, remaining-capacity resume and clean drain.
No real AWS token, S3 request, MIMIC JPEG or QA row is needed.

The SDK retry algorithm is provided by the pinned SDK; mocks validate its configured
attempt budget, forwarding and telemetry, not an actual remote AWS retry event.
The live deployment and 2× throughput outcome remain unverified until approved.
