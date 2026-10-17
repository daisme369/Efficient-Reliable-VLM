# Proposed rented-GPU workflow

Status: engineering proposal, 2026-10-17. No rental or dataset transfer has been
executed. Real runs still require the existing data-access and protocol reviews.

## Prepare before renting

Build and smoke-test the locked Linux image. Pin its digest and the source commit
used by each run. The Dockerfile now includes pinned Git because the previous
image failed the provenance smoke test without it. AWS CLI still needs an
explicit provisioning step or a reviewed image addition. Do not assume a clone contains uncommitted local
implementation work. Commit/push and publishing the image are separate actions.

Use the environment already built into the container. A fresh empty venv hides
those packages and can cause a second PyTorch/CUDA installation. If a venv is
required, build and activate one inside the image, with the same dependency locks,
before renting. Do not rebuild the environment during each paid experiment.

## Execution stages

1. Rent an approved GPU with the tested image and adequate CPU, RAM, local SSD,
   bandwidth and persistent-storage allocation.
2. Clone/check out a pinned source commit; verify the image and GPU preflight.
3. Mount private data/model/artifact directories and inject runtime credentials.
4. Validate S3 source settings and authorization; create a download plan for the
   selected experiment, including sizes and free-space requirements.
5. Download missing selected objects to private storage with retry and integrity
   checks. Record source version and completion state. Repeated startup must not
   restart a completed download or overwrite a completed experiment.
6. Prepare canonical manifests, reconcile patient splits and provenance, then
   pack task-specific WebDataset shards and token caches once.
7. Validate shards and run a small real-model sanity check under the approved
   protocol. Run B0 evaluation or B1/U-Net training, then the YAML sweep.
8. Export checkpoints, configuration, inventories and permitted metrics to
   approved private storage. Verify the export before releasing compute.

The existing package implements preparation, packing, validation and task runs.
The AWS environment helper below loads credentials for explicit CLI commands.
S3 download planning and the startup orchestrator are not implemented yet.
This document does not authorize automatically starting a rental.

## Dataset and credentials

Select dependencies per run instead of downloading all five registered datasets.
For B0/B1, prepare the selected MIMIC-CXR-JPG images and VQA metadata; grounding
and segmentation runs declare their own additional annotations and source images.
CheXmask masks are not a substitute for their corresponding original images.

Use portable environment keys such as `MIMIC_CXR_JPG_S3_URI`,
`MIMIC_CXR_VQA_S3_URI`, `CHEXMASK_S3_URI`, `CHEST_IMAGENOME_S3_URI`, and
`VINDR_CXR_S3_URI`. Existing hyphenated dotenv keys need an explicit parser and
mapping; they cannot be sourced as ordinary Bash assignments. AWS CLI does not
automatically load a project `.env`. Credential environment keys are
`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and, for temporary credentials,
`AWS_SESSION_TOKEN`. Local `aws configure` settings do not appear in a rented
container automatically. Access-point aliases/ARNs identify resources; they do
not replace authentication or PhysioNet authorization.

### Load AWS settings from `.env`

From the repository root, after installing the package in the active environment:

```sh
python -m cxr_research.utils.aws_env --env-file .env --check
python -m cxr_research.utils.aws_env --env-file .env -- --version
```

`--check` reads the file and prints only whether each supported setting is set.
It makes no AWS request and does not verify credentials or dataset access.
To run an AWS command, put its arguments after `--`, for example
`-- s3 ls s3://YOUR-ACCESS-POINT-ALIAS/`. This example contacts AWS and may print
restricted object names, so run it only in a private terminal. AWS CLI must
already be installed; the helper forwards its output and exit code unchanged.

The helper accepts uppercase or lowercase versions of `AWS_ACCESS_KEY_ID`,
`AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, `AWS_REGION`, `AWS_DEFAULT_REGION`
and `AWS_PROFILE`. Existing process settings take precedence. An existing
credential group or `AWS_PROFILE` suppresses all credentials from the file.
A partial access-key/secret pair is rejected instead of being completed from a
different source. If neither source supplies credentials, AWS CLI can use its
normal profile/role providers. An explicitly selected file must exist.

Supported syntax is one `KEY=value` per line, optional `export`, single or double
quotes, blank lines and comments. An unquoted inline comment starts with whitespace
followed by `#`. UTF-8 BOM and CRLF are accepted. Values are literal; multiline
values, quote escapes and variable expansion are not supported. Duplicate supported
keys and empty values are errors. Unknown keys, including PhysioNet passwords and
the five dataset access-point keys, are ignored. Dataset mapping belongs to the
future download planner; this helper only loads AWS authentication/settings.

Python callers can reuse the same loader:

```python
import subprocess
from cxr_research.utils.aws_env import load_aws_environment

environment = load_aws_environment(".env")
subprocess.run(["aws", "--version"], env=environment, check=True)
```

This changes only the child environment. It does not modify the parent shell,
write `~/.aws/credentials`, or insert secrets into command arguments. Do not
serialize the returned dictionary into run configuration or experiment artifacts.

Keep credentials outside Git and image layers, scoped to required read access.
Prefer temporary credentials where available. Use TLS and access-controlled,
encrypted private storage. Retain sanitized transfer status, not object keys or
row-level content in public logs. For storage owned by the project, enable
appropriate access auditing/metrics and encrypt log destinations; the project
cannot configure logging on PhysioNet's buckets.

## Storage and cost

Vast volumes currently attach only to the same physical machine. They can retain
data across instances on that machine but do not make data available on an
arbitrary new host. Keep a recoverable private copy of needed artifacts elsewhere.
Stopped instances still incur storage charges. Check the chosen offer's compute,
storage and bandwidth prices together.

Downloading and packing during an active rental consumes paid time even before
training. Prefer reusing prepared data across consecutive runs. The current packer
keeps raw files and creates shards, so peak capacity includes both, model cache,
temporary files and checkpoints. A 1 TB disk cannot hold two 600 GB copies.

## Indexed reads versus streaming

The current reader uses WebDataset tar format and local indexed random access.
This simplifies finite epochs, per-QA length sampling and Trainer checkpoint
resume. It is not the library's streaming API and does not guarantee sequential
disk reads. There is no measured throughput advantage yet.

Streaming is compatible with deterministic finite epochs and length grouping,
but requires deliberate shard/worker partitioning, buffered sample shuffling,
bounded length buckets and a tested resume policy. A future streaming backend
should consume prepared private shards with a bounded local cache. Raw JPEG
objects in a PhysioNet S3 source do not automatically become a WebDataset stream;
annotation joins and token preparation remain necessary. Benchmark local indexed
reads against sequential shard streaming before changing the default.

## References

- [WebDataset library](https://github.com/webdataset/webdataset)
- [AWS access-point naming and aliases](https://docs.aws.amazon.com/AmazonS3/latest/userguide/access-points-naming.html)
- [AWS credential environment variables](https://docs.aws.amazon.com/boto3/latest/guide/credentials.html)
- [PhysioNet cloud-download FAQ](https://www.physionet.org/about/faqs/)
- [Vast Docker execution environment](https://docs.vast.ai/guides/instances/docker-environment)
- [Vast volumes](https://docs.vast.ai/guides/instances/storage/volumes)
- [Vast billing](https://docs.vast.ai/guides/reference/faq/billing)
