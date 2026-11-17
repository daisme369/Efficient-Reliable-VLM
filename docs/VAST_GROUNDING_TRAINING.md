# Chuẩn bị dữ liệu và chạy MedGemma trên Vast.ai

Runbook này dành cho thành viên bắt đầu từ checkout mới. Dataset, manifest,
mapping, secrets, model cache và checkpoint nằm trên vùng lưu trữ riêng của
instance. Git chỉ chuyển mã và tài liệu.

Chọn [luồng B1](#4-kiểm-tra-và-fine-tune-b1) để train answer-only hoặc
[luồng M1](#7-chạy-m1-trên-tập-phát-triển) để train thêm grounding và sinh
Impression. Hai luồng dùng chung bước tải, dựng manifest và xác minh
mapping. M1 kết hợp tín hiệu câu trả lời và grounding để chọn khi nào
abstain theo [kế hoạch nghiên cứu](../DETAILED_ACTION_PLAN.md).

## 1. Chọn instance và chuẩn bị quyền truy cập

- Mỗi người dùng credential PhysioNet của chính mình, có quyền và DUA của từng
  bản phát hành. Xác nhận bằng hồ sơ access review rằng host, volume, quyền
  truy cập và thời hạn lưu dữ liệu đáp ứng DUA trước khi cấp quyền AWS hoặc
  tải dữ liệu thật. Nếu chưa xác nhận được quyền của bên vận hành host hay cách
  xử lý dữ liệu còn lại sau khi thuê, dùng synthetic hoặc chuyển sang môi
  trường đã được duyệt. [PhysioNet FAQ](https://physionet.org/about/faqs/)
- Làm theo [Docker và Vast](../docker/RENTAL.md) để dựng image Linux amd64
  theo Dockerfile pin, cấp SSH key và chọn một H100, H200 hoặc RTX PRO 6000
  Blackwell đủ driver theo policy. Dùng image đúng digest và chế độ SSH.
  Tạo Dockerfile transport có AWS CLI bằng
  `python scripts/gpu/prepare_vast_image.py --output /absolute/private/Dockerfile.vast`,
  rồi trỏ `docker build --platform linux/amd64 -f` vào file đó, với checkout
  làm build context. Triển khai image qua registry đã được duyệt.
  Kiểm tra dung lượng volume trước khi tạo instance. Riêng JPEG cần khoảng
  612 GB; dự toán thêm chỗ cho các release phụ, kiểm tra và checkpoint.
  [Kết nối SSH trên Vast](https://docs.vast.ai/guides/instances/connect/ssh)
- Dùng đúng access point của từng release và inject AWS credential qua cơ
  chế bí mật được duyệt cho instance. Cấp riêng HF token có quyền đọc model
  để tải weight. Giữ các bí mật ngoài repo và tắt shell tracing.

Từ trang instance, dùng lệnh **SSH** của Vast. Mở shell của user
`researcher` (UID 10001) có quyền với volume private. Đặt `REPO_URL`
và `REPO_COMMIT` theo repo và commit đã review. Dùng quyền Git riêng
đã cấu hình cho user đó.

```bash
set -euo pipefail
umask 077
git clone "${REPO_URL:?set the repository URL}" /workspace/efficient-reliable-vlm
cd /workspace/efficient-reliable-vlm
git checkout --detach "${REPO_COMMIT:?set the reviewed commit}"
git rev-parse HEAD
export PYTHONPATH="$PWD/src"
export DATA=/private/project/data
export AWS_PAGER=
mkdir -p "$DATA"
df -h "$DATA"
aws --version
python --version
python -m pip check
python scripts/ci/check_gpu_environment.py --role train
```

Image đã cài requirements theo lock `requirements.txt`,
`requirements/gpu-runtime.txt` và `requirements/gpu-cu130.txt`. Nếu
`pip check` hoặc GPU preflight lỗi, kiểm tra lại image và offer trước
khi tải dữ liệu. Khi cần vừa train vừa đánh giá trên cùng một instance,
chọn RTX PRO 6000 Blackwell. H100 và H200 qua vai trò train, còn lệnh
`evaluate` yêu cầu một GPU được duyệt cho inference.

## 2. Tải các release lên instance đã được duyệt

Cung cấp năm biến dưới dạng **S3 URI tới đúng prefix của release**, chẳng hạn
access-point alias và prefix do PhysioNet cấp. Dùng URI của tài khoản hiện
tại. Các biến dưới đây chứa URI thuần, không chạy chuỗi lệnh từ `.env`
Windows.

| Biến | Release |
| --- | --- |
| `MIMIC_CXR_JPG_S3_URI` | MIMIC-CXR-JPG 2.1.0 |
| `MIMIC_CXR_VQA_S3_URI` | MIMIC-Ext-MIMIC-CXR-VQA 1.0.0 |
| `CHEST_IMAGENOME_S3_URI` | Chest ImaGenome 1.0.0 |
| `MS_CXR_S3_URI` | MS-CXR 1.1.0 |
| `LLAVA_RAD_S3_URI` | LLaVA-Rad MIMIC-CXR annotations 1.0.0 |

Cho JPEG, tải trực tiếp từ S3 vào volume của instance để tránh trung chuyển
qua máy cá nhân. Chạy với mặc định của AWS CLI trước. Nếu NIC và đĩa còn
dư tải, có thể thử tăng số request đồng thời trong profile AWS của phiên
này. Ví dụ cấu hình khởi điểm để đo, đơn vị là số request.

```bash
aws configure set s3.max_concurrent_requests 32 --profile "${AWS_PROFILE:-default}"
```

Đo tốc độ trên các lô mới có kích thước tương đương. Tính số byte tải thành
công chia cho thời gian, đồng thời theo dõi lỗi, retry và tải đĩa. Giữ mức
concurrency thấp nhất gần tốc độ tốt nhất. Tốc độ tối đa phụ thuộc host,
đường truyền và S3. Nếu đã có plan JPEG riêng của instance, có thể dùng
downloader giữ receipt với benchmark 16 rồi 32 workers theo
[quy trình downloader](../configs/MIMIC_DOWNLOADER_HANDOFF.md#phase-4--bounded-benchmark-after-the-approved-handoff).
Chỉ dùng một downloader cho cùng thư mục. [Cấu hình song song của AWS CLI](https://docs.aws.amazon.com/cli/latest/topic/s3-config.html)

Chạy từng lệnh tương ứng với release có S3 URI. Nếu một release chỉ cấp
HTTPS, dùng lệnh tải trên trang release và lưu vào đúng thư mục đích.

```bash
: "${MIMIC_CXR_JPG_S3_URI:?set the MIMIC JPEG release URI before the S3 commands}"
aws s3 sync "${MIMIC_CXR_JPG_S3_URI%/}/" "$DATA/mimic-cxr-jpg/2.1.0/" --only-show-errors
```

```bash
if [[ -n "${MIMIC_CXR_VQA_S3_URI:-}" ]]; then
  aws s3 sync "${MIMIC_CXR_VQA_S3_URI%/}/" "$DATA/mimic-ext-mimic-cxr-vqa/1.0.0/" --only-show-errors
fi
if [[ -n "${CHEST_IMAGENOME_S3_URI:-}" ]]; then
  aws s3 sync "${CHEST_IMAGENOME_S3_URI%/}/" "$DATA/chest-imagenome/1.0.0/" --only-show-errors
fi
if [[ -n "${MS_CXR_S3_URI:-}" ]]; then
  aws s3 sync "${MS_CXR_S3_URI%/}/" "$DATA/ms-cxr/" --only-show-errors
fi
if [[ -n "${LLAVA_RAD_S3_URI:-}" ]]; then
  aws s3 sync "${LLAVA_RAD_S3_URI%/}/" "$DATA/llava-rad-mimic-cxr-annotations-1.0.0/" --only-show-errors
fi
```

Chạy lại cùng lệnh `sync` sau khi mất mạng. Với cả HTTPS và S3, giữ
`LICENSE.txt` và `SHA256SUMS.txt` ở gốc release. Giữ split nguồn, gold
và danh sách images-to-avoid để xác định bệnh nhân holdout.
[AWS CLI sync](https://docs.aws.amazon.com/cli/latest/reference/s3/sync.html)
· [MS-CXR](https://physionet.org/content/ms-cxr/1.1.0/)
· [Chest ImaGenome](https://physionet.org/content/chest-imagenome/1.0.0/)
· [LLaVA-Rad](https://physionet.org/content/llava-rad-mimic-cxr-annotation/1.0.0/)

Xác minh checksum JPEG trước khi tạo catalog. Lỗi checksum đồng nghĩa với
download chưa đủ hoặc file cần tải lại. Nếu `sync` bỏ qua một file đã sai
checksum, tải lại đúng object bằng `aws s3 cp` và kiểm tra lại. Giữ log chi
tiết tại volume riêng.

```bash
mkdir -p "$DATA/qc"
(
  cd "$DATA/mimic-cxr-jpg/2.1.0"
  sha256sum -c --quiet SHA256SUMS.txt
) >"$DATA/qc/mimic-jpg-sha256.log" 2>&1
```

## 3. Dựng catalog, manifest và mapping

Chọn tên mới cho mỗi lần xuất. Các thư mục dưới đây thuộc volume private.

```bash
export CATALOG="$DATA/manifests/mimic-grounding-v1"
export BASE_NAME=b1-grounding-base-v1
export BASE="$DATA/prepared/$BASE_NAME"
export DRAFT="$DATA/rental/$BASE_NAME"
export BUNDLE="$DATA/prepared/grounding-context-v1-review"

python -m cxr_research catalog --data-root "$DATA" --output "$CATALOG" \
  --split-policy mimic-official
python scripts/gpu/setup_b1.py draft --data-root "$DATA" --catalog "$CATALOG" \
  --output "$DRAFT"
```

Điền các mục `UNRESOLVED` của `$DRAFT/catalog-preparation.json` bằng hồ sơ
thực tế: `access_review`, `sources.images.access/license`,
`sources.qa.access/license` và `sources.qa.derivation` (producer, method,
source_report_section, filters). Dùng release card [MIMIC-CXR-JPG](https://physionet.org/content/mimic-cxr-jpg/2.1.0/)
và [MIMIC VQA](https://physionet.org/content/mimic-ext-mimic-cxr-vqa/1.0.0/)
cùng hồ sơ của nhóm. Pipeline đã đặt AP/PA, chuẩn hóa Unicode, giữ nguyên
ý nghĩa đáp án và không xuất đáp án rỗng thành supervision.

```bash
python scripts/gpu/setup_b1.py prepare --spec "$DRAFT/catalog-preparation.json"
python scripts/gpu/prepare_grounding.py --data-root "$DATA" --catalog "$CATALOG" \
  --base-manifest-dir "$BASE" --output "$BUNDLE"
python scripts/gpu/prepare_grounding.py --data-root "$DATA" --catalog "$CATALOG" \
  --base-manifest-dir "$BASE" --output "$BUNDLE" --verify-only
```

Giữ cả `$BUNDLE` và index nội bộ
`$DATA/manifests/grounding-context-v1-review-grounding-index` để kiểm tra
lại. Nếu export chưa có `report.json`, giữ thư mục lỗi để kiểm tra riêng
và dùng tên `BUNDLE` mới khi chạy lại. Một gói đã xác minh có
`verification.json` với status `passed`.

## 4. Kiểm tra và fine-tune B1

- `manifest.jsonl` và `provenance.json` dùng schema B1. Chạy luồng
  [configure → preflight → train của B1](../configs/B1_PIPELINE.md#5-preflight-pilot-and-monitoring)
  sau khi reviewer duyệt protocol và checkpoint theo đúng hash.
- `mapping.sqlite` chứa `chest_imagenome_bbox` theo QA sample ID,
  `ms_cxr_phrase` theo ảnh/finding/phrase và `context_image` theo đúng
  ảnh. `context_study` chọn một ảnh AP/PA cho mỗi study. Tọa độ bbox là
  `xyxy` theo pixel ảnh gốc. Báo cáo được tham chiếu bằng byte range.
- Trainer B1 chỉ học đáp án. Lệnh M1 ở phần 7 đọc mapping và giữ bbox cùng
  Impression trong target huấn luyện. Giữ file dữ liệu, output và
  checkpoint ở volume private.

Kiểm tra `verification.json` của bundle đã qua và giữ `report.json`
cùng số mẫu theo split. Tạo config B1 answer-only với revision MedGemma
40 ký tự đã duyệt và Jira issue của run. Chạy `cache-model` với `HF_TOKEN`
của người đã chấp nhận điều khoản model. Lệnh chỉ tải weight theo revision.

```bash
export RUN_DRAFT="$DATA/rental/b1-grounding-answer-only-v1"
export HF_CACHE=/private/project/cache/hf
python scripts/gpu/setup_b1.py configure --prepared-dir "$BUNDLE" \
  --output "$RUN_DRAFT" --revision "${MEDGEMMA_REVISION:?set the model revision}" \
  --jira-issue "${JIRA_ISSUE:?set the run issue}"
python scripts/gpu/rental_access.py cache-model --config "$RUN_DRAFT/b1-local.json" --cache "$HF_CACHE"
```

Hoàn tất review trên `$RUN_DRAFT/protocol-b1-local.json` và export
`CXR_CONTAINER_IMAGE_ID` theo image thực tế. Chạy preflight. Lệnh kiểm tra
manifest và file ảnh, protocol, GPU, quyền ghi và model cache offline.

```bash
python scripts/gpu/bootstrap.py --config "$RUN_DRAFT/b1-local.json" --cache "$HF_CACHE"
```

Khi thấy `preflight_passed`, chạy pilot theo [B1 pipeline](../configs/B1_PIPELINE.md#5-preflight-pilot-and-monitoring)
trước full run. Sau khi pilot đạt và full run có protocol đã duyệt, chạy
trong phiên SSH bền hoặc dùng lệnh dưới đây. B1 nạp base MedGemma đã cache
và khởi tạo adapter mới, trừ khi đang khôi phục cùng một run.

```bash
export TRAIN_DIR="$DATA/runs/b1/$(basename "$RUN_DRAFT")"
nohup python -u scripts/gpu/bootstrap.py --config "$RUN_DRAFT/b1-local.json" \
  --cache "$HF_CACHE" --train >"$RUN_DRAFT/training.log" 2>&1 < /dev/null &
TRAIN_PID=$!
printf '%s\n' "$TRAIN_PID" >"$RUN_DRAFT/training.pid"
```

Khi `$TRAIN_DIR/run.json` xuất hiện, chạy monitor. Nếu tiến trình thoát
trước đó, đọc `$RUN_DRAFT/training.log` ở volume private.

```bash
python scripts/gpu/monitor_b1.py --run "$TRAIN_DIR" \
  --pid "$(cat "$RUN_DRAFT/training.pid")" --watch
```

Nếu SSH ngắt hoặc máy bị dừng giữa chừng, giữ nguyên các file và xem
[cách resume checkpoint B1](../configs/B1_PIPELINE.md#6-recovery-and-completion).

## 5. Đánh giá B1 trên validation

Dùng adapter đã xuất trong `$TRAIN_DIR/adapter` khi `run.json` báo
`complete`. Tạo một run ID đánh giá mới và giữ nguyên cấu hình khoa học
cùng dataset, manifest và đường dẫn đã bind. Nếu đánh giá trên instance
khác, giữ cùng đường dẫn private và điều kiện truy cập đã duyệt.

```bash
export EVAL_ID="$(basename "$RUN_DRAFT")-validation"
export EVAL_CONFIG="$RUN_DRAFT/b1-validation.json"
python - "$RUN_DRAFT/b1-local.json" "$EVAL_CONFIG" "$EVAL_ID" <<'PY'
import json
from pathlib import Path
import sys

source, output, run_id = sys.argv[1:]
config = json.loads(Path(source).read_text(encoding="utf-8"))
config["run_id"] = run_id
with Path(output).open("x", encoding="utf-8") as stream:
    json.dump(config, stream, indent=2)
PY
export HF_HOME="$HF_CACHE" HF_HUB_CACHE="$HF_CACHE/hub"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
python scripts/ci/check_gpu_environment.py --role inference
python -m cxr_research evaluate --config "$EVAL_CONFIG" --checkpoint "$TRAIN_DIR/adapter"
export EVAL_DIR="$DATA/runs/b1/$EVAL_ID"
```

`metrics.json` có normalized match và metric closed-ended trên validation.
Số đo thời gian hiện tại chỉ tính generation, batch 1, chưa warm-up.
Benchmark latency để báo cáo cần warm-up, lặp lại và ghi p50/p95 dưới cùng
điều kiện. Chỉ mở test với protocol cuối đã duyệt.

## 6. Kiểm tra và tải artifact về

Trên instance, sau khi train và validation báo `complete`, kiểm tra SHA-256
của adapter và từng checkpoint được giữ lại. Đóng gói run, config, protocol
và các báo cáo tổng hợp. Các bản dự đoán từng QA ở lại volume private.

```bash
export EXPORT_DIR=/private/project/exports
mkdir -p "$EXPORT_DIR"
python - "$TRAIN_DIR" "$EVAL_DIR" <<'PY'
import json
from pathlib import Path
import sys

from cxr_research.experiments.checkpoints import verify_artifacts

train, evaluation = map(Path, sys.argv[1:])
for run in (train, evaluation):
    if json.loads((run / "run.json").read_text(encoding="utf-8"))["status"] != "complete":
        raise SystemExit("Run is incomplete")
    if not (run / "metrics.json").is_file():
        raise SystemExit("Run metrics are missing")
verify_artifacts(train / "adapter")
for checkpoint in sorted(train.glob("checkpoint-*")):
    if checkpoint.is_dir():
        verify_artifacts(checkpoint)
print("Adapter, checkpoints and completed run records checked.")
PY
export ARCHIVE="$(basename "$RUN_DRAFT").tar"
tar -cf "$EXPORT_DIR/$ARCHIVE" --exclude="$(basename "$EVAL_DIR")/predictions.jsonl" \
  -C "$DATA/runs/b1" "$(basename "$TRAIN_DIR")" "$(basename "$EVAL_DIR")" \
  -C "$RUN_DRAFT" b1-local.json b1-validation.json protocol-b1-local.json \
  -C "$BUNDLE" provenance.json report.json verification.json
(
  cd "$EXPORT_DIR"
  sha256sum "$ARCHIVE" >"$ARCHIVE.sha256"
)
```

Trên máy nhận có vùng lưu trữ đã duyệt, đặt `ARCHIVE` cùng tên ở trên,
`VAST_SSH_PORT` và `VAST_SSH_TARGET` theo lệnh SSH của instance. Dùng Bash
có `scp`, `sha256sum` và `tar`, chẳng hạn WSL.

```bash
set -euo pipefail
umask 077
scp -P "$VAST_SSH_PORT" "$VAST_SSH_TARGET:/private/project/exports/$ARCHIVE" .
scp -P "$VAST_SSH_PORT" "$VAST_SSH_TARGET:/private/project/exports/$ARCHIVE.sha256" .
sha256sum -c "$ARCHIVE.sha256"
tar -tf "$ARCHIVE" >"$ARCHIVE.files"
```

Chỉ kết thúc thuê sau khi bản sao đã qua kiểm tra và danh sách file có
adapter, checkpoint cần giữ, config, protocol, identity và metrics.

## 7. Chạy M1 trên tập phát triển

### Tạo và duyệt config

Hoàn tất các bước 1–3. M1 khởi tạo LoRA mới từ cùng revision MedGemma với
B1. Dùng protocol và run ID riêng. `--resume` chỉ dành cho checkpoint
M1 có cùng mapping, manifest và config khoa học.

```bash
export RUN_DRAFT="$DATA/rental/m1-grounding-impression-v1"
export HF_CACHE=/private/project/cache/hf
python scripts/gpu/setup_b1.py configure --prepared-dir "$BUNDLE" \
  --output "$RUN_DRAFT" --revision "${MEDGEMMA_REVISION:?set the model revision}" \
  --jira-issue "${JIRA_ISSUE:?set the run issue}"
sha256sum "$BUNDLE/report.json"
```

Trong `$RUN_DRAFT/protocol-b1-local.json`, giữ fingerprint đã sinh, điền
các trường protocol chung, rồi thêm đối tượng `m1`. Điền SHA-256 của
`$BUNDLE/report.json`. Các trường còn lại cần giá trị thực từ reviewer
trước khi chuyển trạng thái protocol sang `approved`.

| Trường trong `m1` | Giá trị |
| --- | --- |
| `bundle_report_sha256` | Digest từ lệnh trên |
| `bbox_review` | Hồ sơ review nhãn và quyền dùng bbox |
| `impression_supervision_decision` | Quyết định dùng Impression làm target ảnh → báo cáo |
| `grounding_protocol` | Hồ sơ duyệt quy tắc chấm bbox và sử dụng MS-CXR |
| `target_risk` | Ngưỡng risk mục tiêu đã duyệt, số thực giữa 0 và 1 |
| `pilot` | `true` cho pilot, `false` cho full run |

### Kiểm tra và train

Export `CXR_CONTAINER_IMAGE_ID` theo image thực tế và cấp `HF_TOKEN`
để cache model nếu cache còn thiếu. Dùng config `b1-local.json` sinh ở trên
cho backbone và LoRA. Entry point riêng ghi `variant: M1` trong run record.

```bash
python scripts/gpu/rental_access.py cache-model --config "$RUN_DRAFT/b1-local.json" --cache "$HF_CACHE"
python scripts/gpu/run_m1.py validate --config "$RUN_DRAFT/b1-local.json" --cache "$HF_CACHE"
```

Kiểm tra số mẫu trong output `m1_preflight_passed`. Mỗi QA, grounding
Chest ImaGenome, nhóm phrase MS-CXR và Impression LLaVA-Rad là một mẫu
riêng trong luồng M1. Grounding Chest ImaGenome thêm một mẫu localization
cho QA đã có. Impression rỗng bị loại và được đếm. Bbox được lượng tử hóa
về lưới 0–1000 dựa trên kích thước ảnh gốc. Dùng `pilot: true` để giới
hạn mỗi tác vụ còn tối đa 8 mẫu train và 8 mẫu ở mỗi nhóm bệnh nhân
validation. Pilot vẫn kiểm tra toàn bộ bundle và checksum. Sau khi
pilot kiểm tra được token budget, VRAM và adapter reload, tạo config,
protocol và run ID mới với `pilot: false` để train đầy đủ.

```bash
export TRAIN_DIR="$DATA/runs/b1/$(basename "$RUN_DRAFT")"
nohup python -u scripts/gpu/run_m1.py train --config "$RUN_DRAFT/b1-local.json" \
  --cache "$HF_CACHE" >"$RUN_DRAFT/training.log" 2>&1 < /dev/null &
printf '%s\n' "$!" >"$RUN_DRAFT/training.pid"
```

Theo dõi `$TRAIN_DIR` với `monitor_b1.py` sau khi có `run.json`.
Khi cần khôi phục, dùng config có run ID mới và cùng fingerprint, rồi
thêm `--resume /absolute/private/path/to/checkpoint-N` vào lệnh train.

### Đánh giá và tải artifact

Tạo `$RUN_DRAFT/b1-validation.json` theo lệnh tạo config ở phần 5.
Giữ config và protocol đã bind, đổi run ID cho lượt đánh giá rồi chạy
lệnh M1 trên GPU qua vai trò inference.

```bash
export EVAL_ID="$(basename "$RUN_DRAFT")-validation"
export EVAL_DIR="$DATA/runs/b1/$EVAL_ID"
python scripts/gpu/run_m1.py evaluate --config "$RUN_DRAFT/b1-validation.json" \
  --cache "$HF_CACHE" --checkpoint "$TRAIN_DIR/adapter"
```

M1 chia bệnh nhân validation cố định thành nhóm chọn checkpoint, fit
calibration và báo cáo. Tín hiệu answer dùng xác suất token sinh đáp án.
Tín hiệu grounding dùng xác suất token của bbox hợp lệ do model sinh.
Risk được fit bằng logistic regression trên nhóm calibration, sau đó
áp ngưỡng abstain trên nhóm báo cáo. Bbox tham chiếu và báo cáo nguồn
chỉ dùng để chấm. Nếu thiếu hai lớp đúng/sai khi fit, metrics ghi rõ
calibration chưa đủ dữ liệu. Chỗ thiếu nhãn bbox không đưa vào mẫu số
grounding dương tính.

Đọc `$EVAL_DIR/metrics.json` để xem VQA, grounding theo nguồn, độ khớp
từ vựng của Impression, Brier, ECE, AURC, coverage, risk và latency
batch 1. Điểm report là lexical F1. Phần ablation đo ba cách dùng tín
hiệu trên cùng adapter. Kết quả là exploratory validation. So sánh
paper-facing còn cần các baseline B1/B2/B3 độc lập cùng cohort, review
nhãn và protocol benchmark đã khóa. Nếu không có câu MS-CXR validation
hoặc bbox silver đủ ở nhóm báo cáo, metric tương ứng ghi số mẫu và null.

Dùng phần 6 để xác minh và tải adapter, checkpoint, config, protocol,
`data_summary.json` và metrics về. Giữ bản sao private đã xác minh
trước khi kết thúc thuê.
