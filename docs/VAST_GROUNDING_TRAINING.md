# Dựng manifest MedGemma trên Vast.ai

Runbook này dành cho thành viên bắt đầu từ checkout mới. Dataset, manifest,
mapping, secrets, model cache và checkpoint nằm trên vùng lưu trữ riêng của
instance. Git chỉ chuyển mã và tài liệu.

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

Trên instance, clone repo vào đường dẫn riêng với data, checkout commit đã
review rồi đi vào thư mục repo. Các lệnh sau chạy bằng user có quyền với
volume private và trong runtime của image đã chọn.

```bash
set -euo pipefail
umask 077
export PYTHONPATH="$PWD/src"
export DATA=/private/project/data
export AWS_PAGER=
mkdir -p "$DATA"
df -h "$DATA"
aws --version
python scripts/ci/check_gpu_environment.py --role train
```

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

```bash
: "${MIMIC_CXR_JPG_S3_URI:?missing MIMIC JPEG release URI}"
: "${MIMIC_CXR_VQA_S3_URI:?missing VQA release URI}"
: "${CHEST_IMAGENOME_S3_URI:?missing Chest ImaGenome release URI}"
: "${MS_CXR_S3_URI:?missing MS-CXR release URI}"
: "${LLAVA_RAD_S3_URI:?missing LLaVA-Rad release URI}"

aws s3 sync "${MIMIC_CXR_JPG_S3_URI%/}/" "$DATA/mimic-cxr-jpg/2.1.0/" --only-show-errors
aws s3 sync "${MIMIC_CXR_VQA_S3_URI%/}/" "$DATA/mimic-ext-mimic-cxr-vqa/1.0.0/" --only-show-errors
aws s3 sync "${CHEST_IMAGENOME_S3_URI%/}/" "$DATA/chest-imagenome/1.0.0/" --only-show-errors
aws s3 sync "${MS_CXR_S3_URI%/}/" "$DATA/ms-cxr/" --only-show-errors
aws s3 sync "${LLAVA_RAD_S3_URI%/}/" "$DATA/llava-rad-mimic-cxr-annotations-1.0.0/" --only-show-errors
```

Chạy lại cùng lệnh `sync` sau khi mất mạng. Nếu PhysioNet chỉ cung cấp tải
HTTPS cho một release, dùng lệnh tải trên trang release với tài khoản đã được
cấp quyền, rồi giữ đúng cấu trúc thư mục ở trên, gồm `LICENSE.txt` và
`SHA256SUMS.txt`. Giữ split nguồn, gold và danh sách images-to-avoid để xác
định bệnh nhân holdout. [AWS CLI sync](https://docs.aws.amazon.com/cli/latest/reference/s3/sync.html)
· [MS-CXR](https://physionet.org/content/ms-cxr/1.1.0/)
· [Chest ImaGenome](https://physionet.org/content/chest-imagenome/1.0.0/)
· [LLaVA-Rad](https://physionet.org/content/llava-rad-mimic-cxr-annotation/1.0.0/)

Xác minh checksum JPEG trước khi tạo catalog. Lỗi checksum đồng nghĩa với
download chưa đủ hoặc file cần tải lại. Giữ log chi tiết tại volume riêng.

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

## 4. Chuẩn bị chạy

- `manifest.jsonl` và `provenance.json` dùng schema B1. Chạy luồng
  [configure → preflight → train của B1](../configs/B1_PIPELINE.md#5-preflight-pilot-and-monitoring)
  sau khi reviewer duyệt protocol và checkpoint theo đúng hash.
- `mapping.sqlite` chứa `chest_imagenome_bbox` theo QA sample ID,
  `ms_cxr_phrase` theo ảnh/finding/phrase và `context_image` theo đúng
  ảnh. `context_study` chọn một ảnh AP/PA cho mỗi study. Tọa độ bbox là
  `xyxy` theo pixel ảnh gốc. Báo cáo được tham chiếu bằng byte range.
- Trainer B1 chỉ học đáp án. Để train cả bbox và tạo báo cáo cần trainer
  đọc mapping và loss tương ứng, một protocol đã duyệt, cùng review nhãn
  silver. Thiết kế image-only VQA giữ báo cáo và bbox ground truth ra khỏi
  prompt. Giữ các file dữ liệu, output và checkpoint ở volume private.

Tạo config B1 answer-only với revision MedGemma 40 ký tự đã duyệt và Jira
issue của run. Trong config local, đường dẫn protocol trỏ ngay vào bản draft
được sinh trong cùng thư mục. Hoàn tất review, điền `CXR_CONTAINER_IMAGE_ID`
thực tế và cache model theo tài liệu B1 trước khi chạy bootstrap.

```bash
export RUN_DRAFT="$DATA/rental/b1-grounding-answer-only-v1"
python scripts/gpu/setup_b1.py configure --prepared-dir "$BUNDLE" \
  --output "$RUN_DRAFT" --revision "$MEDGEMMA_REVISION" --jira-issue "$JIRA_ISSUE"
python scripts/gpu/bootstrap.py --config "$RUN_DRAFT/b1-local.json" \
  --cache /private/project/cache/hf
```

Bootstrap mặc định chỉ preflight. Sau khi toàn bộ gate đạt, thêm `--train`
vào chính lệnh bootstrap để bắt đầu B1 answer-only.

Phạm vi handoff là checkout → tải → xác minh → chuẩn bị dữ liệu. Không có
Jira được cung cấp. Chạy local CPU để xác minh code và artifact, không cần
thuê GPU cho việc bàn giao.
