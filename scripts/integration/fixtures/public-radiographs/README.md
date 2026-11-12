# Ảnh X-quang công khai dùng trong demo

Bộ `thorax-public-radiographs-v1` bổ sung 3 study vào Worklist của Thorax. Tên, năm sinh, giới tính, ngày study và mã CXR trong web là dữ liệu dựng riêng cho demo. File cấu hình ghi nguồn, tác giả và SHA-256 của từng ảnh gốc. [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/).

| Mã demo | Số ảnh | Nguồn |
|---|---:|---|
| CXR-101 | 2 | Stillwaterising · [PA](https://commons.wikimedia.org/wiki/File:Chest_Xray_PA_3-8-2010.png) · [LAT](https://commons.wikimedia.org/wiki/File:Chest_Xray_Lateral_3-8-2010.png) |
| CXR-102 | 2 | Mikael Häggström · [PA](https://commons.wikimedia.org/wiki/File:Chest_radiograph_in_influensa_and_H_influenzae,_posteroanterior.jpg) · [LAT](https://commons.wikimedia.org/wiki/File:Chest_radiograph_in_influensa_and_H_influenzae,_lateral.jpg) |
| CXR-103 | 1 | Mikael Häggström · [AP](https://commons.wikimedia.org/wiki/File:X-ray_of_COPD_exacerbation_-_anteroposterior_view.jpg) |

Ứng dụng xác minh checksum ảnh gốc và tạo PNG theo quy trình chuẩn hóa hiện có. Các ảnh này được đưa vào giao diện demo. Chế độ Mock AI trả danh sách nhận xét và vùng gợi ý rỗng cho bộ ảnh này, nên có thể tự vẽ vùng khoanh và thêm nhận xét để thử luồng duyệt. Kết quả mock rỗng không thể dùng để kết luận về ảnh.
