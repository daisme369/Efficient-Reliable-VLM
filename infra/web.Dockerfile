FROM node:24.22.2-bookworm-slim@sha256:01d566e8d94d8c619591d78652babfccd3c6992a3dffa9ed5ab39e11cf6c70e9
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm install --global pnpm@11.25.0 && chown node:node /app
COPY --chown=node:node src/thorax-web/package.json src/thorax-web/pnpm-lock.yaml ./
USER node
RUN pnpm install --frozen-lockfile --ignore-scripts
COPY --chown=node:node src/thorax-web/ ./
ENV THORAX_API_ORIGIN=http://api:8100
RUN pnpm build
CMD ["node", "node_modules/next/dist/bin/next", "start", "--hostname", "0.0.0.0", "--port", "3100"]
