# Detailed Action Plan — Capstone A Radiology VQA

**Phiên bản:** 2.2  
**Ngày cập nhật:** 15/10/2026  
**Phạm vi:** Computing Technology Project A và nghiên cứu tiếp nối để công bố; không lập lịch Capstone B  
**Chủ sở hữu kế hoạch:** Lê Đức Anh và Giáp Hoàng Gia Khánh  
**Quyết định thay đổi phạm vi:** [SCRUM-6, CR-2026-10-11 và CR-2026-10-15](https://vlminmedical.atlassian.net/browse/SCRUM-6)

## 1. Mục đích sử dụng

Tài liệu này là kế hoạch thực thi chung cho nhóm. Jira theo dõi trạng thái hằng ngày. Tài liệu giữ phạm vi nghiên cứu, trách nhiệm, mốc giao bài và tiêu chí đánh giá ổn định qua các sprint. Các task sau ngày 06/12 thuộc nghiên cứu để công bố, không được gắn nhãn là kế hoạch Capstone B.

Các deadline Capstone A được đối chiếu từ tài liệu môn học:

- Team & Project Plan: **25/10/2026, 20:59**.
- Sprint 1 Report và Contribution Summary: **15/11/2026, 20:59**.
- Sprint 2 Report, Contribution Summary và Video Presentation: **06/12/2026, 20:59**. MVP/POC và app demo phải sẵn sàng cho mốc này.

Lịch học hoặc đề tài của Capstone B chưa được xác nhận, nên tài liệu không đặt nhiệm vụ hay deadline cho môn đó.

## 2. Mục tiêu nghiên cứu và phạm vi

### 2.1 Mục tiêu

Xây dựng một Radiology VQA system tập trung vào **reliability** và một app MVP để supervisor thử trực tiếp. Hệ thống:

1. Trả lời câu hỏi trên chest X-ray với clinical factual accuracy có thể đo lường.
2. Cung cấp clinical grounding và sử dụng bằng chứng grounding để ước lượng rủi ro của câu trả lời.
3. Có khả năng abstain/selective prediction khi rủi ro cao.
4. Nhận ảnh chest X-ray và câu hỏi qua app; hiển thị câu trả lời, vùng grounding, điểm tin cậy hoặc rủi ro, ngưỡng và quyết định abstain. Ít nhất một ca phù hợp phải hiển thị vùng do pipeline grounding tạo ra, kèm tên phương pháp và giới hạn kiểm định; nếu không xác định được vùng, app phải ghi rõ trạng thái đó thay vì vẽ vùng giả.
5. Có thể fine-tune bằng PEFT/QLoRA trên H100/H200 và suy luận trên GPU RTX theo policy Docker, với mục tiêu profile RTX 4090 24 GB nếu backbone và cách lượng tử hóa cho phép.
6. Ghi nhận trade-off giữa answer quality, reliability và efficiency. Multi-teacher knowledge distillation là nhánh nghiên cứu tiếp nối sau video; trước đó chỉ thử một teacher và một student khi đường găng MVP đã hoàn thành.

### 2.2 Câu hỏi nghiên cứu ban đầu

- **RQ1 — Reliability:** Grounding-conditioned confidence có giảm AURC và cải thiện coverage tại một mức selective risk cố định so với confidence chỉ dựa trên answer probability không?
- **RQ2 — Faithfulness:** Khi can thiệp counterfactual vào vùng ảnh có ý nghĩa lâm sàng, risk score và abstention decision có phản ứng đúng hướng hơn so với khi can thiệp vùng không liên quan không?
- **RQ3 — Efficiency:** PEFT/QLoRA và, ở giai đoạn tiếp nối, knowledge distillation có thể giảm VRAM, GPU-hours hoặc latency mà không làm giảm đáng kể answer quality và selective reliability không?

Trong Capstone A, RQ1 và app MVP là ưu tiên giao bài. RQ2 và các so sánh nhiều seed có thể ở mức exploratory trước ngày 06/12. Không tuyên bố RQ3 về distillation nếu chưa có student, đối chứng và phép đo. Các giả thuyết, ngưỡng non-inferiority và primary endpoint phải được khóa trước khi đánh giá test set.

### 2.3 Trong phạm vi

- Modality chính: chest X-ray.
- [MIMIC-CXR-JPG v2.1.0](https://physionet.org/content/mimic-cxr-jpg/2.1.0/) làm nguồn ảnh; [MIMIC-Ext-MIMIC-CXR-VQA v1.0.0](https://physionet.org/content/mimic-ext-mimic-cxr-vqa/1.0.0/) làm nguồn câu hỏi và đáp án; [Chest ImaGenome v1.0.0](https://physionet.org/content/chest-imagenome/1.0.0/) làm nguồn vùng giải phẫu gắn với finding. [CheXmask v1.0.0](https://physionet.org/content/chexmask-cxr-segmentation-data/1.0.0/) bổ sung mask phổi trái, phổi phải và tim để kiểm tra vùng giải phẫu hoặc hiển thị có ghi rõ phương pháp; không thay nhãn VQA hay vùng bệnh lý. Nhóm đã có quyền truy cập ba bộ dữ liệu credentialed trên PhysioNet; Cáp xác minh quyền riêng, version và khóa nối ảnh trước khi xử lý. CheXmask open access không làm thay đổi hạn chế của ảnh MIMIC gốc.
- Primary benchmark là tập template có đáp án có thể suy ra từ ảnh, với quy tắc chọn và mã template được khóa trước khi xem test. Full benchmark là secondary benchmark, báo riêng theo content type. Giữ patient-level split và test gold độc lập; không dùng test để chọn model, prompt hoặc ngưỡng.
- Closed-ended và open-ended Radiology VQA.
- Baseline zero-shot và QLoRA/PEFT.
- Clinical grounding, uncertainty calibration, selective prediction và hallucination/error analysis.
- Đo latency, throughput, peak VRAM, trainable parameters và GPU-hours.
- [MedGemma 1.5 4B](https://huggingface.co/google/medgemma-1.5-4b-it) là baseline đầu tiên. [Qwen3-VL-4B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct) và [LLaVA-Med v1.5 Mistral 7B](https://github.com/microsoft/LLaVA-Med) là ứng viên so sánh sau gate về giấy phép, runtime, VRAM và giao diện ảnh.
- Demo dùng một bộ ảnh chest X-ray trên Kaggle **chưa chọn**. Cáp kiểm tra nguồn gốc, quyền trình chiếu và việc bộ đó có phải bản sao MIMIC hay không. Câu hỏi tự viết theo cấu trúc template VQA chỉ phục vụ walkthrough; không dùng làm benchmark hoặc ground truth lâm sàng.

### 2.4 Ngoài phạm vi mặc định

- Clinical deployment, chẩn đoán tự động hoặc tuyên bố thay thế bác sĩ.
- Full pretraining từ đầu.
- Thay backbone, dataset split hoặc metric sau khi xem test result.
- Đưa restricted MIMIC data, report, image hoặc identifier vào Git/Jira/public artifact.
- MRI/CT, pruning và full-model training trước ngày 06/12.
- Multi-teacher distillation trước video 06/12. Sau video, đây là nhánh nghiên cứu đã chấp thuận nhưng phải qua student-selection và compute gate riêng.
- Gọi điểm tin cậy trên ảnh Kaggle là đã hiệu chuẩn, hoặc gọi vùng hiển thị là grounding đúng, khi chưa có phép kiểm định trên phân phối ảnh demo.
- Dùng mask phổi hoặc tim của CheXmask làm bằng chứng trực tiếp cho vị trí tổn thương hoặc độ đúng của câu trả lời về bệnh lý.

## 3. Mô hình và thiết kế thực nghiệm

| ID | Hệ thống | Mục đích |
|---|---|---|
| B0 | MedGemma 1.5 4B frozen/zero-shot | Baseline đầu tiên; đo answer quality, chi phí inference và failure modes |
| B1 | MedGemma 1.5 4B + QLoRA VQA | Baseline trainable có thể tái lập; là đường găng của MVP nếu B0 chưa đủ |
| B2 | B1 + answer-only calibration/risk | Đối chứng reliability không dùng grounding |
| B3 | B1 + grounding-only risk | Tách giá trị của tín hiệu grounding |
| M1 | B1 + joint grounding-conditioned risk/abstention | Phương pháp đề xuất chính |
| M2 | M1 + counterfactual training hoặc consistency loss | Chỉ triển khai khi M1 ổn định; ablation mở rộng |

Các phép so sánh MedGemma, Qwen3-VL-4B-Instruct và LLaVA-Med v1.5 dùng cùng tập câu hỏi, patient split, chuẩn hóa đáp án, image resolution policy và metric code. Prompt format có thể khác theo model nhưng phải được khóa và ghi lại trước test. Đầu ra B0-Qwen và B0-LLaVA chỉ được đưa vào bảng so sánh sau khi vượt gate license, loading và VRAM. LLaVA-Med v1.5 là model 7B với stack riêng; không mặc định rằng nó chạy được trong Docker profile hiện tại.

Student chưa được chọn. Đức Anh lập decision record sau B0/B1, dựa trên chất lượng primary validation, latency, VRAM, quyền sử dụng, khả năng fine-tune và giao diện grounding. Student có thể là một trong ba ứng viên hoặc model nhỏ hơn nếu có bằng chứng và được duyệt. Trước video, `D1` là POC một teacher–student tùy năng lực còn lại. Sau video, `D2` là multi-teacher distillation để nghiên cứu: so sánh student không distill, một teacher và nhiều teacher; dùng teacher outputs chỉ từ train split, lưu trong môi trường được cấp quyền. Khác tokenizer thì ưu tiên distillation ở mức đáp án hoặc bằng chứng có schema chung; không giả định có thể áp dụng KL trực tiếp lên logits khác vocabulary.

Tất cả so sánh chính phải dùng cùng patient-level split, preprocessing, decoding policy và evaluation code. Tối thiểu ba random seeds cho kết quả dùng trong bài báo, trừ benchmark deterministic đã được giải thích. Kết quả một seed trước 06/12 phải ghi rõ là exploratory.

## 4. Metrics và protocol đánh giá

| Nhóm | Metrics bắt buộc | Quy tắc báo cáo |
|---|---|---|
| Benchmark protocol | Primary: tập template có đáp án dựa trên ảnh, đã đăng ký trước. Secondary: full MIMIC-Ext test với bảng riêng theo content type | Ghi mã template, tiêu chí loại, số câu hỏi và số bệnh nhân mỗi split; không thay subset sau khi xem test |
| Closed-ended VQA | Closed-Acc, balanced/macro-F1, per-question-type accuracy | Báo micro và macro; kèm bootstrap 95% CI cho kết quả paper-facing |
| Open-ended VQA | Exact/normalized match, BLEU, ROUGE-L, BERTScore; clinical factual score khi có công cụ phù hợp | BLEU/ROUGE không được dùng một mình để kết luận clinical correctness |
| Reliability | ECE, Brier score, NLL nếu khả dụng, risk–coverage curve, AURC, coverage tại fixed risk | Confidence definition và calibration split phải được ghi rõ |
| Hallucination | Unsupported-claim rate, contradiction rate, error taxonomy và clinician/domain-expert audit nếu có | Báo sample size, protocol gán nhãn và inter-rater agreement khi có nhiều annotator |
| Grounding | IoU/Dice cho mask, mAP cho box, pointing-game accuracy hoặc region hit rate tùy annotation | Chỉ so sánh metrics dùng cùng loại annotation và threshold |
| CheXmask anatomical QC | Tỷ lệ nối ảnh, tỷ lệ mask hợp lệ, Dice RCA (Mean) và kiểm tra căn chỉnh mask | Báo riêng cho phổi trái, phổi phải và tim; khóa ngưỡng lọc chất lượng trên validation trước test; không gọi đây là lesion-grounding accuracy |
| Counterfactual faithfulness | Risk delta trên vùng liên quan so với vùng không liên quan; answer-change rate; attribution/grounding degradation | Báo positive, negative và ambiguous interventions riêng |
| Efficiency | Trainable parameters, checkpoint size, GPU-hours, peak training/inference VRAM, throughput, p50/p95 latency ở batch size 1 | Khóa hardware, precision, image resolution, token limits và warm-up protocol |
| App MVP | Upload ảnh, nhập câu hỏi, câu trả lời, grounding view, confidence/risk, ngưỡng, abstention và thời gian trả lời | Smoke test trên dữ liệu công khai được phép dùng; ít nhất một ca phù hợp có vùng do pipeline tạo ra, không hard-code cho video; ghi phương pháp và giới hạn, còn ca không có vùng hợp lệ hiển thị "không xác định" |

Dataset gốc có template về giới tính. Primary benchmark loại template phụ thuộc metadata hoặc không có bằng chứng ảnh đáng tin cậy; quy tắc phải được khóa theo template ID trước khi mở test. Secondary benchmark vẫn báo đầy đủ, với điểm nhóm giới tính tách riêng và không dùng để tuyên bố clinical grounding. MIMIC-Ext v1.0.0 được dẫn xuất từ MIMIC-CXR-JPG v2.0.0, trong khi nhóm dự kiến dùng JPG v2.1.0; Cáp xác nhận mapping theo image/study ID và version trước khi join.

CheXmask cung cấp mask giải phẫu tạo tự động và điểm RCA, không cung cấp ground truth riêng cho đáp án VQA. Cáp kiểm tra image ID, view, kích thước và tọa độ mask trên phần MIMIC khớp với JPG v2.1.0. Mốc `Dice RCA (Mean) ≥ 0.7` là khuyến nghị từ [data dictionary](https://physionet.org/content/chexmask-cxr-segmentation-data/1.0.0/DATA_DICTIONARY.md); nhóm khóa quy tắc lọc trên validation trước khi dùng test. Ảnh MIMIC trùng nguồn không tạo thành external test set mới. Chỉ đánh giá organ-mask QC bằng CheXmask; finding grounding vẫn cần Chest ImaGenome hoặc nhãn phù hợp khác.

Test set chỉ được chạy sau khi model selection và threshold selection hoàn tất trên validation/calibration split. Ảnh Kaggle là dữ liệu demo ngoài benchmark; không lấy điểm tin cậy trên MIMIC làm bằng chứng calibration trên Kaggle. Per-example output dùng cho phân tích không được chứa protected identifiers.

## 5. Phân công trách nhiệm

| Thành viên | Vai trò chính | Trách nhiệm | Reviewer/backup |
|---|---|---|---|
| **Lê Đức Anh** | Team Lead; Model & Visual Grounding Lead | MedGemma B0/B1, Qwen/LLaVA feasibility gate, grounding interface, model integration vào app, student decision và distillation sau video | Gia Khánh |
| **Giáp Hoàng Gia Khánh** | Backup Team Lead; Research & Reliability Lead; Scrum facilitator | Template eligibility, RQ/hypothesis, calibration/selective metrics, phân tích thống kê, app risk display, report và video claim review | Đức Anh |
| **Cáp Việt Anh** | Data & Applied ML Lead | Quyền truy cập PhysioNet, mapping ba bộ credentialed và CheXmask phụ trợ, patient split, data card, grounding QC và chọn ảnh Kaggle có quyền trình chiếu | Gia Khánh |
| **Nguyễn Văn Tuấn Kiệt** | Junior Research Engineer | Checklist smoke test app, UI/evidence log theo đặc tả, chart regeneration, meeting log và dựng video | Cáp Việt Anh hoặc Gia Khánh |

Nguyên tắc giao việc cho Tuấn Kiệt: task phải nhỏ, có input/output rõ ràng, acceptance criteria kiểm tra được và reviewer được chỉ định trước. Trong hai sprint đầu, Tuấn Kiệt không tự thay đổi model logic, data split hoặc định nghĩa metric.

## 6. Cách vận hành Jira

- **SCRUM — Efficient & Reliable VLM in Medical:** một sprint mỗi tuần từ tuần 5. Epic là kết quả lớn; story nêu giá trị cho supervisor, lecturer hoặc nhóm nghiên cứu và có acceptance criteria; task/subtask là việc triển khai. Board này theo dõi deadline, app, review và contribution evidence.
- **MVD — Medical VLM Development:** giữ task kỹ thuật về literature, data, model, evaluation và experiment. Story SCRUM liên kết task MVD bằng quan hệ *is implemented by*, không nhân đôi công việc hoặc trạng thái.
- [SCRUM-6](https://vlminmedical.atlassian.net/browse/SCRUM-6) quản lý bài nộp và governance. [SCRUM-15](https://vlminmedical.atlassian.net/browse/SCRUM-15) quản lý research/data protocol; [SCRUM-16](https://vlminmedical.atlassian.net/browse/SCRUM-16) quản lý baseline; [SCRUM-17](https://vlminmedical.atlassian.net/browse/SCRUM-17) quản lý grounding/reliability; [SCRUM-18](https://vlminmedical.atlassian.net/browse/SCRUM-18) quản lý app MVP.
- Tuần 1–4 (17/09–14/10) là giai đoạn lập nhóm, chọn đề tài và theo dõi literature review theo lời nhóm. Chỉ ghi kết quả nào có meeting note hoặc evidence; không tạo sprint Done giả hoặc suy ra velocity từ hồi ức.
- Sprint cũ `Capstone A Sprint 1` được nhóm đóng ngày 15/10. Ba issue SCRUM-7, SCRUM-9 và SCRUM-12 giữ trạng thái Done/lịch sử của chúng; trạng thái đó không thay cho kiểm tra evidence khi dùng trong report.
- `Sprint 1 Report` của môn học là checkpoint tuần 9, `Sprint 2 Report` là checkpoint tuần 12; đây không phải tên/số thứ tự sprint Jira.
- MVD hiện không bật Story Point. Không đặt estimate giả khi chưa có capacity và thống nhất estimation; dùng owner, sprint, dependency và acceptance criteria để lập kế hoạch trước.

| Sprint Jira | Ngày theo Asia/Saigon | Goal và checkpoint |
|---|---|---|
| W05 | 15–21/10 | Chuẩn hóa backlog, user stories, literature gap và review scope. Sprint đang active. |
| W06 | 22–28/10 | Data/protocol draft; Team & Project Plan và STAR summaries hạn 25/10, 20:59. |
| W07 | 29/10–04/11 | Data/privacy gate, demo-data/app contract, MedGemma B0 feasibility. |
| W08 | 05–11/11 | B0/B1 validation, reliability metric fixtures, app synthetic smoke test. |
| W09 | 12–18/11 | Baseline demo, supervisor feedback và course Sprint 1 Report hạn 15/11, 20:59. |
| W10 | 19–25/11 | Finding-linked grounding, answer-only risk và app alpha. |
| W11 | 26/11–02/12 | App MVP beta, joint-method gate nếu đủ điều kiện, freeze kết quả ngày 02/12. |
| W12 | 03–06/12 | Demo rehearsal, course Sprint 2 Report, STAR summaries và video hạn 06/12, 20:59. |

Planning đầu tuần xác nhận capacity, mục tiêu, dependency và Definition of Ready. Review cuối tuần kiểm tra từng acceptance criterion và evidence link. Issue chưa đạt phải được chuyển sang sprint kế tiếp với owner, lý do, phần còn lại và tác động tới các mốc 25/10, 15/11, 06/12; không tự đánh dấu Done để khớp timeline. Nếu việc không nằm trên đường găng MVP, hai lead quyết định đưa sang research backlog thay vì lấn thời gian report/video. [SCRUM-31](https://vlminmedical.atlassian.net/browse/SCRUM-31) theo dõi việc Team Lead review cách vận hành mới.

### 6.1 Backlog đã tạo

| Jira | Owner | Nội dung | Deliverable liên quan |
|---|---|---|---|
| [SCRUM-7](https://vlminmedical.atlassian.net/browse/SCRUM-7) | Gia Khánh | Team & Project Plan package; Jira ghi Done trong sprint cũ, story SCRUM-20 kiểm tra evidence | 25/10 |
| [SCRUM-12](https://vlminmedical.atlassian.net/browse/SCRUM-12) | Cáp Việt Anh | MIMIC access, privacy, ethics controls; Jira ghi Done trong sprint cũ | 25/10 và xuyên suốt |
| [SCRUM-8](https://vlminmedical.atlassian.net/browse/SCRUM-8) | Đức Anh | Sprint 1 baseline review, demo, report | 15/11 |
| [SCRUM-9](https://vlminmedical.atlassian.net/browse/SCRUM-9) | Tuấn Kiệt | Meeting log và contribution evidence; Jira ghi Done trong sprint cũ | Evidence tiếp tục ở từng sprint |
| [SCRUM-13](https://vlminmedical.atlassian.net/browse/SCRUM-13) | Đức Anh | Integrate và review grounded selective VQA prototype | W11 checkpoint |
| [SCRUM-10](https://vlminmedical.atlassian.net/browse/SCRUM-10) | Gia Khánh | Sprint 2 prototype review và report | 06/12 |
| [SCRUM-11](https://vlminmedical.atlassian.net/browse/SCRUM-11) | Tuấn Kiệt | Video và MVP walkthrough package | 06/12 |
| [SCRUM-14](https://vlminmedical.atlassian.net/browse/SCRUM-14) | Đức Anh | App MVP: ảnh + câu hỏi → answer, grounding, risk, abstention | MVP beta 29/11; giao bài 06/12 |
| [MVD-2](https://vlminmedical.atlassian.net/browse/MVD-2) | Gia Khánh | Literature gap matrix và research questions | Research freeze |
| [MVD-3](https://vlminmedical.atlassian.net/browse/MVD-3) | Cáp Việt Anh | MIMIC VQA/grounding schema và CheXmask anatomical QC | Data gate |
| [MVD-4](https://vlminmedical.atlassian.net/browse/MVD-4) | Đức Anh | Backbone selection và zero-shot benchmark | Baseline gate |
| [MVD-6](https://vlminmedical.atlassian.net/browse/MVD-6) | Đức Anh | Reproducible QLoRA baseline | Sprint 1 demo |
| [MVD-5](https://vlminmedical.atlassian.net/browse/MVD-5) | Gia Khánh | Reliability/calibration evaluation | Sprint 1 demo |
| [MVD-7](https://vlminmedical.atlassian.net/browse/MVD-7) | Cáp Việt Anh | Counterfactual grounding pipeline | Sprint 2 prototype |
| [MVD-8](https://vlminmedical.atlassian.net/browse/MVD-8) | Đức Anh | Grounding-conditioned risk prototype | Sprint 2 prototype |
| [MVD-9](https://vlminmedical.atlassian.net/browse/MVD-9) | Tuấn Kiệt | Experiment registry, smoke tests, charts | Evidence package |
| [MVD-10](https://vlminmedical.atlassian.net/browse/MVD-10) | Đức Anh | Student selection và multi-teacher distillation | Nghiên cứu sau video 06/12 |

### 6.2 Story và phụ thuộc theo tuần

Mỗi story dưới đây có owner, acceptance criteria và liên kết đến task triển khai trong Jira. Một story chỉ được Done khi chính acceptance criteria của story có evidence; trạng thái Done của task liên quan không tự đóng story.

| Tuần | Story trên SCRUM | Task liên quan |
|---|---|---|
| W05 | [SCRUM-19](https://vlminmedical.atlassian.net/browse/SCRUM-19) gap matrix/RQ bản nháp; [SCRUM-31](https://vlminmedical.atlassian.net/browse/SCRUM-31) review quy trình Scrum | MVD-2 |
| W06 | [SCRUM-20](https://vlminmedical.atlassian.net/browse/SCRUM-20) project plan; [SCRUM-21](https://vlminmedical.atlassian.net/browse/SCRUM-21) data contract | SCRUM-7, SCRUM-12, MVD-3 |
| W07 | [SCRUM-22](https://vlminmedical.atlassian.net/browse/SCRUM-22) demo-data và app contract; [SCRUM-23](https://vlminmedical.atlassian.net/browse/SCRUM-23) B0 gate | SCRUM-14, MVD-4 |
| W08 | [SCRUM-24](https://vlminmedical.atlassian.net/browse/SCRUM-24) B1 gate; [SCRUM-25](https://vlminmedical.atlassian.net/browse/SCRUM-25) metric contract | MVD-6, MVD-5 |
| W09 | [SCRUM-26](https://vlminmedical.atlassian.net/browse/SCRUM-26) baseline demo và course Sprint 1 Report | SCRUM-8 |
| W10 | [SCRUM-27](https://vlminmedical.atlassian.net/browse/SCRUM-27) finding grounding; [SCRUM-28](https://vlminmedical.atlassian.net/browse/SCRUM-28) risk/abstention | MVD-7, MVD-8, SCRUM-13 |
| W11 | [SCRUM-29](https://vlminmedical.atlassian.net/browse/SCRUM-29) app MVP beta | SCRUM-13, SCRUM-14 |
| W12 | [SCRUM-30](https://vlminmedical.atlassian.net/browse/SCRUM-30) final report/video/demo | SCRUM-10, SCRUM-11, SCRUM-14 |

Jira ghi phụ thuộc `Blocks` theo đường găng: data gate → B0/B1; metric/grounding gate → risk/MVP beta → final package. Literature review và kiểm tra data có thể chạy song song. Khi một gate trượt, chỉ chuyển phần chưa đạt của story sang tuần sau và ghi blocker; không thay đổi metric hoặc deadline để tạo cảm giác đúng tiến độ.

## 7. Detailed Action Plan — Capstone A

| Giai đoạn | Thời gian | Owner | Task cụ thể | Output/Deliverable | Tiêu chí đánh giá |
|---|---|---|---|---|---|
| A0 — Scope và protocol | 11–17/10 | Gia Khánh, Đức Anh | Ghi CR-2026-10-11; khóa primary image-answerable template rule, secondary full benchmark, RQ và đường găng app | Scope record; template eligibility draft; acceptance checklist | Hai lead xác nhận; không dùng test để chọn template; mỗi deliverable có owner và Jira issue |
| A0 — Literature/RQ | 11–24/10 | Gia Khánh | Gap matrix cho VQA, clinical grounding, selective prediction, PEFT và multi-teacher KD; tách claim Capstone A với claim sau video | Gap matrix; hypothesis table; nguồn gốc luận điểm | Nguồn chính được trích; giả thuyết có đối chứng và phép đo; không hứa novelty trước kết quả |
| A1 — Data contract | 11–24/10 | Cáp | Xác minh quyền truy cập ba bộ credentialed; nối VQA, JPG và Chest ImaGenome; kiểm tra version mismatch, patient split, template ID và label uncertainty | Data card; schema; join report; synthetic fixtures | Không patient overlap; báo số câu hỏi và bệnh nhân theo split/content type; lỗi join được thống kê |
| A1 — CheXmask feasibility | 15–24/10 | Cáp; Gia Khánh review | Kiểm tra giấy phép CheXmask v1.0.0, image-ID join trên phần MIMIC, view, kích thước, mask alignment và RCA bằng mẫu nhỏ trên CPU | Báo cáo feasibility trong [MVD-3](https://vlminmedical.atlassian.net/browse/MVD-3); quy tắc lọc chất lượng dự kiến | Báo join coverage và trường hợp bị loại; không xem test để chọn ngưỡng; không làm chậm baseline hoặc MVP; nếu chưa qua thì hoãn full integration |
| A1 — Demo-data gate | 18–31/10 | Cáp; Gia Khánh review | Chọn một bộ Kaggle CXR có nguồn gốc và quyền trình chiếu rõ; kiểm tra không phải bản sao restricted MIMIC; soạn vài câu hỏi template-style | Demo-data decision; câu hỏi walkthrough; danh sách ảnh được phép | Không có ảnh/report/identifier MIMIC trong video; câu hỏi demo không được ghi là nhãn benchmark |
| A1 — App interface và scaffold | 18–31/10 | Đức Anh; Tuấn Kiệt hỗ trợ | Khóa contract `image + question → answer + grounding + confidence/risk + abstain + latency`; dựng app skeleton, config, experiment ID và synthetic smoke test | App skeleton; response schema; registry template | Synthetic upload và trả kết quả chạy end-to-end; trường nào chưa có model phải hiện rõ là chưa khả dụng |
| A1 — Team & Project Plan | 12–25/10 | Gia Khánh chủ trì; cả nhóm | Hoàn thiện team profile, teamwork, risk/ethics, solution alternatives, KoST, backlog và STAR summaries | PDF và 4 STAR summaries trước **25/10 20:59** | Rubric checklist hoàn tất; Jira/Git/meeting evidence có link; mỗi thành viên có contribution record |
| A2 — MedGemma B0 gate | 25/10–01/11 | Đức Anh | Xác nhận model terms/revision; chạy zero-shot trên primary validation; đo VRAM, latency và failure cases trên Docker profile | B0 config, decision record và validation table | Closed-Acc, macro-F1, normalized match, ECE/Brier inputs, p50/p95 và peak VRAM; không dùng gold test |
| A2 — B1 và data pipeline | 26/10–08/11 | Đức Anh, Cáp | Loader có split audit; fine-tune MedGemma bằng QLoRA trên H100/H200 nếu B0 gate qua; ghi run manifest và checkpoint hợp lệ | B1 run hoặc blocker có bằng chứng; configs; split audit | Reproduce trên clean environment; log trainable params, GPU-hours, peak VRAM; không có restricted artifact trong Git |
| A2 — Reliability metrics | 26/10–10/11 | Gia Khánh | Chuẩn hóa đáp án; định nghĩa confidence; ECE, Brier, risk–coverage, AURC, coverage@risk và answer-only abstention | Metric package; validation/calibration table | Hand-computed tests qua; ngưỡng chỉ chọn trên calibration split; raw score chưa hiệu chuẩn được gắn nhãn |
| A2 — Sprint 1 review | 09–15/11 | Đức Anh, Tuấn Kiệt; Gia Khánh review | Demo app skeleton với B0 hoặc B1, ghi supervisor feedback và retrospective | Sprint 1 PDF, baseline demo, 4 STAR summaries trước **15/11 20:59** | Upload và hỏi đáp hoạt động; báo accuracy, latency, VRAM với protocol; fallback đã diễn tập |
| A3 — Grounding module và QC | 09–22/11 | Cáp, Đức Anh | Dùng Chest ImaGenome cho vùng gắn với finding; nếu CheXmask qua feasibility thì dùng organ mask cho anatomical QC hoặc visualization có nhãn; kiểm tra ánh xạ câu hỏi sang vùng | Grounding interface; QC set; exclusion report | Báo organ-mask QC riêng với finding grounding; ảnh không có vùng hỗ trợ trả trạng thái "không xác định"; không gọi mask giải phẫu là bằng chứng bệnh lý |
| A3 — So sánh model có gate | 16–26/11 | Đức Anh; Gia Khánh review | Thử Qwen3-VL-4B-Instruct rồi LLaVA-Med v1.5 nếu license, runtime và GPU đạt; cùng validation samples, metrics và output schema | B0-Qwen/B0-LLaVA validation table hoặc blocker record | Ghi checkpoint, prompt, GPU, latency, VRAM và lý do loại; không làm chậm app beta |
| A3 — MVP beta | 16–29/11 | Đức Anh; Cáp, Gia Khánh, Tuấn Kiệt review | Nối model, grounding visualization, confidence/risk và abstention vào app; kiểm thử trên ảnh demo được duyệt | App beta của [SCRUM-14](https://vlminmedical.atlassian.net/browse/SCRUM-14) | Người dùng nhập ảnh và câu hỏi, thấy answer, score, ngưỡng, abstain và latency; ít nhất một ca có vùng do pipeline tạo ra và nêu phương pháp, ca khác có thể nêu lý do không có vùng; chạy trên RTX profile |
| A3 — Joint reliability prototype | 16–29/11 | Đức Anh; Gia Khánh review | Nếu B1, grounding và app beta đã đạt: thử B2/B3/M1, counterfactual QC và ablation nhỏ. Nếu chưa đạt, ưu tiên B2 cho app | M1 prototype hoặc decision record hoãn; preliminary ablation | Chỉ gọi M1 có hiệu quả khi có B1/B2/B3 cùng protocol; kết quả một seed ghi exploratory |
| A3 — Single-teacher POC tùy điều kiện | 25/11–02/12 | Đức Anh | Chỉ khi app beta, report inputs và video script đã ổn: chọn một teacher và một student cho smoke run train-only | `D1` config và smoke result, hoặc quyết định hoãn | Không dùng test để chọn student; không cản freeze kết quả 02/12; không gọi là multi-teacher |
| A4 — Kết quả và claim freeze | 29/11–02/12 | Gia Khánh | Tổng hợp primary/secondary riêng; bootstrap CI khi đủ mẫu; kiểm tra metric, seed, failure taxonomy và giới hạn so sánh | Result tables; claim–evidence matrix | Mỗi số có experiment ID, split, model revision và hardware; exploratory được ghi rõ; không có kết luận KD nếu chưa đo |
| A4 — Report và video | 30/11–06/12 | Gia Khánh chủ trì; Tuấn Kiệt dựng video; cả nhóm | Sprint 2 PDF, 4 STAR summaries, slide/script, video khoảng 10 phút và supervisor app walkthrough | PDF, PPTX/MP4, app MVP trước **06/12 20:59** | Bốn thành viên tham gia; demo dùng ảnh có quyền trình chiếu; tất cả claim có evidence; bản ghi không chứa MIMIC |

### Capstone A exit criteria

Mốc giao bài bắt buộc là: (1) Team & Project Plan; (2) Sprint 1 và Sprint 2 reports cùng contribution evidence; (3) app MVP nhận ảnh và câu hỏi rồi hiển thị answer, confidence/risk, ngưỡng và abstention, với ít nhất một ca hiển thị vùng do pipeline grounding tạo ra; ca không có vùng hợp lệ hiện trạng thái trung thực; (4) video và walkthrough được supervisor xem; (5) kết quả baseline có thể truy ngược tới config, split, checkpoint và hardware. B0 MedGemma là fallback demo khi B1 chưa sẵn sàng; report phải ghi rõ phần B1, M1 hoặc KD chưa đạt. Ba model, M1 hoàn chỉnh, một teacher–student và multi-teacher KD không phải điều kiện nộp video.

## 8. Nghiên cứu tiếp nối sau video 06/12

Phần này phục vụ bài báo hoặc luận văn nghiên cứu sau môn học. Không gán tuần của Capstone B hoặc ngày nộp giả định. Chỉ bắt đầu GPU runs mới sau khi nhóm định giá compute, khóa protocol và kiểm tra lại quyền model/dataset.

| Giai đoạn | Thời gian | Owner | Task cụ thể | Output/Deliverable | Tiêu chí đánh giá |
|---|---|---|---|---|---|
| R1 — Reproduce và freeze | Sau video, chưa chốt ngày | Đức Anh, Cáp, Gia Khánh | Reproduce B1/M1; khóa primary template IDs, split, prompt, decoding, confidence và metric code | Reproduction report; data card v1; frozen protocol | Patient leakage check qua; sai khác với Capstone A được giải thích; ít nhất một run lặp lại trên clean Docker image |
| R2 — Student và teacher gate | Sau R1 | Đức Anh; Gia Khánh review | Quyết định student từ ba ứng viên hoặc model nhỏ hơn; chọn teacher hợp lệ, giá GPU và target RTX | [MVD-10](https://vlminmedical.atlassian.net/browse/MVD-10) decision record | Model terms, checkpoint revisions, VRAM, latency, trainable params và expected GPU-hours được ghi; không chọn trên test |
| R3 — Multi-teacher distillation | Sau R2 | Đức Anh, Cáp | Tạo targets từ train split; thử một teacher và nhiều teacher, lọc bất đồng/độ tin cậy; lưu outputs restricted trong storage được cấp quyền | Student checkpoints, configs và experiment registry | Cùng student, split và budget so sánh; không trộn logits khác tokenizer nếu chưa có mapping hợp lệ |
| R4 — Ablation và robustness | Sau R3 | Gia Khánh; Đức Anh, Cáp | So sánh no-KD, single-teacher, multi-teacher; B2/B3/M1; counterfactual, subgroup và threshold sensitivity | Ablation matrix; error analysis; statistical report | Primary/secondary tách biệt; ba seeds cho kết quả chính trừ khi protocol giải thích; 95% CI; AURC/coverage/VRAM/latency được báo cùng answer quality |
| R5 — Paper package | Khi claim ổn định | Gia Khánh và Đức Anh; cả nhóm review | Chọn venue theo contribution, làm internal review kỹ thuật và lâm sàng, viết paper và artifact audit | Paper draft; reproducibility package; response-to-reviewer matrix | Bảng tái sinh được; claim có evidence; public artifact không chứa dữ liệu PhysioNet, teacher targets hay model weights chưa được phép phát hành |

Nếu distillation không cải thiện reliability hoặc efficiency, báo kết quả âm và giữ contribution ở grounded selective VQA. MRI/CT và pruning chưa thuộc kế hoạch này; muốn thêm phải có scope-change record mới.

## 9. Quality gates và quyết định mở rộng scope

| Gate | Điều kiện đạt | Nếu không đạt |
|---|---|---|
| G0 — Data/Governance | Quyền ba bộ credentialed hợp lệ; mapping và patient isolation qua; template IDs khóa trước test; CheXmask có license, image join và mask QC được ghi riêng | Dừng training/evaluation trên restricted data; nếu CheXmask chưa qua thì hoãn phần mask, giữ baseline và app |
| G1 — Baseline | MedGemma B0 chạy lại được, B1 có run manifest hoặc blocker rõ; metrics và efficiency được log | App dùng B0 có ghi giới hạn; chưa mở model comparison hoặc D1 |
| G2 — App MVP | App nhận ảnh và câu hỏi, hiện answer, score, threshold, abstain và latency; ít nhất một ca phù hợp hiện vùng do pipeline tạo ra, ca khác có trạng thái không xác định khi cần; ảnh demo có quyền trình chiếu | Ưu tiên sửa app và video; hoãn Qwen/LLaVA, M1 hoặc D1 theo thứ tự đó |
| G3 — Reliability | Metric fixtures qua; confidence và ngưỡng chọn trên calibration; grounding có QC | Không gọi score "calibrated" hoặc vùng "faithful"; kết quả chỉ là exploratory |
| G4 — Joint method | M1 so với B1/B2/B3 dưới cùng protocol và có chi phí tăng thêm | Chưa claim M1 hiệu quả; làm tiếp sau video nếu cần |
| G5 — Distillation/publication | Student và teacher gate qua; single/multi-teacher ablation, CI, robustness và artifact audit đủ | Thu hẹp claim hoặc báo kết quả âm; không nộp claim distillation thiếu đối chứng |

## 10. Rủi ro và phương án giảm thiểu

| Rủi ro | Dấu hiệu sớm | Mitigation | Owner |
|---|---|---|---|
| Join ba bộ dữ liệu sai version | MIMIC-Ext image/study ID không map hết sang JPG v2.1.0 | Báo join coverage và exclusion theo version; khóa mapping trước training | Cáp |
| CheXmask lệch ảnh hoặc mask kém chất lượng | Image ID không khớp, mask sai kích thước hoặc RCA thấp | Kiểm tra join và căn chỉnh trên mẫu nhỏ; tiền định ngưỡng chất lượng; hoãn mask integration nếu không đạt | Cáp |
| Demo Kaggle thiếu quyền hoặc là bản sao MIMIC | Không có nguồn gốc, license hoặc quyền trình chiếu rõ | Chọn lại bộ công khai có provenance; không quay ảnh chưa được duyệt | Cáp |
| Thuê GPU không ổn định/chi phí tăng | Queue dài, OOM, run bị preempt | Smoke run nhỏ, checkpoint/resume, 4-bit PEFT, budget cap theo experiment | Đức Anh |
| Data leakage | Patient xuất hiện nhiều split; tuning trên test | Automated split audit, immutable manifest, test access gate | Cáp + Gia Khánh |
| Reliability claim yếu | Accuracy tăng nhưng AURC/ECE không cải thiện | Giữ reliability là primary endpoint; answer-only và grounding-only controls | Gia Khánh |
| Grounding không faithful | Heatmap đẹp nhưng counterfactual response yếu | Intervention tests, negative controls, explicit failure taxonomy | Đức Anh + Cáp |
| Model phụ không chạy trên Docker/GPU đã khóa | LLaVA-Med cần stack khác; Qwen hoặc LLaVA OOM | Feasibility gate sớm; ghi blocker; giữ MedGemma và app làm đường găng | Đức Anh |
| Score trên Kaggle lệch phân phối MIMIC | Câu hỏi demo không có nhãn hoặc calibration | Gắn nhãn demo-only và out-of-domain; không báo clinical accuracy/calibration trên ảnh đó | Gia Khánh |
| Scope drift do coding agent | Agent đổi backbone/metric hoặc thêm feature ngoài issue | Root `AGENTS-INSTRUCTIONS.md`, task contract, scope-change record và review gate | Gia Khánh |
| Chênh lệch kinh nghiệm trong nhóm | Task bị tắc hoặc chất lượng không đồng đều | Bounded tasks, pairing, reviewer bắt buộc, checklist-based DoD | Đức Anh |
| Deadline report lấn app và video | Result hoặc MVP chưa freeze ngày 02/12 | Freeze thí nghiệm 02/12; hoãn D1 và model phụ; tách report owner với app owner | Gia Khánh |

## 11. Definition of Done chung

Một work item chỉ được chuyển sang Done khi:

- Acceptance criteria trong Jira đã đạt và có evidence link.
- Code/config có reviewer; test hoặc smoke test liên quan đã chạy.
- Experiment ghi đủ Jira key, commit, config, dataset version, seed, hardware, thời gian và artifact path.
- Metric definition và comparison baseline không bị thay đổi âm thầm.
- Không có restricted data, identifier, credential hoặc oversized model artifact trong Git.
- Kết quả âm, limitation và deviation khỏi kế hoạch được ghi lại.
- Nếu dùng cho report/paper, bảng hoặc hình có thể tái sinh từ machine-readable result.
- Với app, checklist upload → answer → grounding/status → score → abstain → latency qua trên GPU inference đã duyệt; video chỉ dùng ảnh có quyền trình chiếu.
- Với benchmark, primary template IDs đã khóa trước test và full-benchmark secondary được báo riêng.

## 12. Nhịp làm việc và contribution evidence

- Planning đầu sprint: xác nhận sprint goal, capacity, dependencies và acceptance criteria.
- Stand-up bất đồng bộ tối thiểu ba lần/tuần: Done / Next / Blocker / Evidence.
- Technical review hằng tuần: data integrity, metric validity, compute spend và scope drift.
- Supervisor review: chuẩn bị app walkthrough, ảnh demo được duyệt, fallback recording, câu hỏi cần feedback và biên bản quyết định.
- Cuối sprint: review, retrospect, cập nhật risk register và ethics checklist.
- Mỗi thành viên duy trì STAR log liên tục, gắn Jira issue, commit/PR, document section, experiment ID và impact; không chờ tới ngày nộp mới dựng lại evidence.

## 13. Change control

Mọi thay đổi một trong các mục sau cần được ghi thành quyết định và được Team Lead cùng Research Lead duyệt trước khi thực hiện: research question, modality, dataset/split, backbone chính, primary metric, proposed method, compute budget lớn hoặc submission target.

Change record phải nêu: lý do, evidence mới, ảnh hưởng tới timeline/compute, work item bị thay thế, metrics cần chạy lại và quyết định accept/reject/defer. Sau khi duyệt, cập nhật Jira và tài liệu này trước khi coding agent thực hiện.

CR-2026-10-11. Gia Khánh xác nhận Đức Anh đã đồng ý thay đổi; record ghi rõ approval của Đức Anh được chuyển lời bởi Gia Khánh, không giả lập chữ ký hoặc comment riêng của Đức Anh. Thay đổi đưa MVP app và ba model candidate vào Capstone A, đặt image-answerable subset là primary, full benchmark là secondary, bỏ lịch Capstone B và chuyển multi-teacher KD sang nghiên cứu sau video. Khi nhóm chọn student, dataset Kaggle cụ thể, compute budget lớn hoặc thay metric chính, tạo decision record mới trước khi chạy.

CR-2026-10-15 trong cùng issue chấp thuận CheXmask v1.0.0 làm nguồn mask giải phẫu phụ trợ. Gia Khánh xác nhận Đức Anh đã đồng ý, nhưng record không giả lập xác nhận trực tiếp từ Đức Anh. Primary VQA benchmark, nguồn finding grounding và deadline MVP không đổi. Nếu dùng CheXmask làm đầu vào huấn luyện hoặc risk feature cho B3/M1, nhóm phải ghi protocol và chạy lại đối chứng liên quan.
