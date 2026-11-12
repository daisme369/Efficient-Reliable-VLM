# Thorax demo với PostgreSQL 16

Bản Compose chạy web, API, worker, inference mock và PostgreSQL. Chỉ web mở cổng trên `127.0.0.1`. Dịch vụ `init` chạy migration bằng tài khoản chủ rồi nạp ảnh và ca mẫu. API và worker dùng tài khoản PostgreSQL hạn chế. Inference dùng token riêng và bộ phản hồi mock.

## Khởi động

Cài Docker với Compose và Linux containers. Từ thư mục gốc, sao chép `infra/.env.compose.example` thành `infra/.env.compose`. Điền mỗi mật khẩu và token bằng một chuỗi ngẫu nhiên độc lập, chỉ gồm ký tự URL an toàn. Dùng `THORAX_WEB_PORT=3111` nếu bản preview Windows đang dùng cổng 3100.

```sh
docker compose --env-file infra/.env.compose -f infra/compose.yaml config --quiet
docker compose --env-file infra/.env.compose -f infra/compose.yaml up -d --build
docker compose --env-file infra/.env.compose -f infra/compose.yaml ps -a
```

Mở `http://127.0.0.1:<THORAX_WEB_PORT>` và đăng nhập bằng `doctor@thorax.demo` với `THORAX_DOCTOR_PASSWORD`, hoặc `admin@thorax.demo` với `THORAX_ADMIN_PASSWORD`. Hai mật khẩu này dùng khi tạo tài khoản lần đầu. Tài khoản đã lưu vẫn dùng mật khẩu cũ khi sửa file cấu hình.

`init` phải kết thúc với mã 0. Compose chờ PostgreSQL và inference sẵn sàng trước khi khởi động API. Kiểm tra nội bộ của API xác minh migration Alembic, một ảnh mẫu và contract inference. Xem log bằng `docker compose --env-file infra/.env.compose -f infra/compose.yaml logs --tail 80 api worker inference`. Dùng `stop` để tắt và `up -d` để chạy lại. PostgreSQL, ảnh và manifest nằm trong ba volume có tên. Để thử bộ ảnh khác, chọn dự án Compose mới bằng `-p` và cổng khác. [Docker Compose startup order](https://docs.docker.com/compose/how-tos/startup-order/).

## Sao lưu và diễn tập phục hồi

Các lệnh dưới đây dùng shell Bash từ thư mục gốc. Chọn một thời điểm không còn yêu cầu AI đang chạy. Giữ bản sao lưu và file cấu hình riêng của máy ở nơi có quyền truy cập phù hợp. Sao lưu cùng lúc database, ảnh và manifest.

```bash
dc() { docker compose --env-file infra/.env.compose -f infra/compose.yaml "$@"; }
backup="thorax-backup-$(date +%Y%m%d-%H%M%S)"
mkdir "$backup"
dc stop web api worker
dc exec -T db pg_dump -U thorax_owner -d thorax -Fc --no-owner --no-privileges -f /tmp/thorax.dump
dc cp db:/tmp/thorax.dump "$backup/thorax.dump"
dc cp api:/data/images "$backup/images"
dc cp inference:/data/fixtures "$backup/fixtures"
dc up -d
```

Dùng một tên dự án **mới** cho lần diễn tập và một cổng khác. Thư mục sao lưu phải có `thorax.dump`, `images/` và `fixtures/`. Dùng bản mã nguồn và thông tin đăng nhập của bản sao lưu.

```bash
restore() { THORAX_WEB_PORT=3112 docker compose --env-file infra/.env.compose -f infra/compose.yaml -p thorax-restore-check "$@"; }
restore up -d --wait db
restore cp "$backup/thorax.dump" db:/tmp/thorax.dump
restore exec -T db pg_restore -U thorax_owner -d thorax --single-transaction --exit-on-error --no-owner --no-privileges /tmp/thorax.dump
restore run --rm --build --no-deps -v "$PWD/$backup:/backup:ro" init sh -ec 'cp -R /backup/images/. /data/images/; cp -R /backup/fixtures/. /data/fixtures/'
restore up -d --build
restore exec -T api python scripts/integration/deployment.py --api-root apps/api api-ready --api-url http://127.0.0.1:8100 --all-images
```

Sau khi API sẵn sàng, mở `http://127.0.0.1:3112` và đối chiếu báo cáo đã duyệt, người ký, thời điểm ký và audit với bản gốc. Kiểm tra ảnh đối chiếu hash và kích thước của từng ảnh với bản ghi PostgreSQL. `pg_restore` trả mã lỗi nếu khôi phục ràng buộc thất bại. [PostgreSQL 16 pg_dump](https://www.postgresql.org/docs/16/app-pgdump.html).

Máy làm việc hiện tại chưa chạy được Docker/PostgreSQL. Các lệnh triển khai và phục hồi cần nghiệm thu trên máy có Docker trước khi dùng cho buổi demo. Chế độ VLM/CV thật được cấu hình riêng trong [hướng dẫn tích hợp](../docs/LOCAL_INTEGRATION.md).
