FROM node:24.22.2-bookworm-slim@sha256:01d566e8d94d8c619591d78652babfccd3c6992a3dffa9ed5ab39e11cf6c70e9 AS fixture
WORKDIR /build
COPY scripts/integration/export_demo.mjs scripts/integration/
COPY src/thorax-web/lib/server/fixtures.ts src/thorax-web/lib/server/
RUN node scripts/integration/export_demo.mjs > /demo.json

FROM python:3.12.16-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/thorax/apps/api
WORKDIR /thorax
COPY apps/api/requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir -r /tmp/requirements.lock \
 && groupadd --gid 10001 thorax \
 && useradd --uid 10001 --gid thorax --no-create-home thorax \
 && install -d -o thorax -g thorax /data/images /data/fixtures
COPY apps/api/ apps/api/
COPY scripts/integration/ scripts/integration/
COPY infra/local.integration.toml infra/
COPY src/thorax-web/lib/server/assets/*.png /demo/assets/
COPY --from=fixture /demo.json /demo/demo.json
USER thorax
