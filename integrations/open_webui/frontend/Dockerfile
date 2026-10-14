# syntax=docker/dockerfile:1.7

# Open WebUI source and runtime are pinned to the same v0.11.3 release.
ARG OPEN_WEBUI_SOURCE_COMMIT=2a960a59fe1dbbd35282f0556b3666d81102e781
ARG MAGAZINE_VERSION=0.1.0-beta.5

FROM node:22-alpine3.20@sha256:2289fb1fba0f4633b08ec47b94a89c7e20b829fc5679f9b7b298eaa2f1ed8b7e AS frontend
ARG OPEN_WEBUI_SOURCE_COMMIT
ARG MAGAZINE_VERSION
RUN apk add --no-cache git
WORKDIR /src
# Open WebUI's production bundle exceeds Node's default ~4 GiB heap while
# rendering chunks on some builders (the upstream Dockerfile documents this).
ENV NODE_OPTIONS="--max-old-space-size=8192"
RUN git init \
    && git remote add origin https://github.com/open-webui/open-webui.git \
    && git fetch --depth 1 origin "${OPEN_WEBUI_SOURCE_COMMIT}" \
    && git checkout --detach FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = "${OPEN_WEBUI_SOURCE_COMMIT}" \
    && test "$(node -p "require('./package.json').version")" = "0.11.3"
COPY patches/0001-magazine-sidebar-launcher.patch /tmp/magazine-sidebar.patch
RUN git apply --check /tmp/magazine-sidebar.patch \
    && git apply /tmp/magazine-sidebar.patch
COPY patches/0002-frontend-parser-security.patch /tmp/frontend-parser-security.patch
RUN git apply --check /tmp/frontend-parser-security.patch \
    && git apply /tmp/frontend-parser-security.patch
RUN npm ci --force
COPY tests/parser_compatibility.test.cjs /src/parser_compatibility.test.cjs
RUN node --test parser_compatibility.test.cjs
ENV APP_BUILD_HASH="open-webui-v0.11.3-magazine-${MAGAZINE_VERSION}"
RUN npm run build

FROM ghcr.io/open-webui/open-webui:v0.11.3@sha256:41daa0cf2561a5d4c8d1ff31ee2a98d93ab4d3ac2605cac69366ff6a3374a933
ARG OPEN_WEBUI_SOURCE_COMMIT
ARG MAGAZINE_VERSION
COPY --from=frontend /src/build /app/build
COPY licenses/ /licenses/open-webui/
LABEL org.opencontainers.image.title="Magazine-enabled Open WebUI" \
      org.opencontainers.image.version="${MAGAZINE_VERSION}" \
      org.opencontainers.image.source="https://github.com/Nundrian/Magazine" \
      org.opencontainers.image.base.name="ghcr.io/open-webui/open-webui:v0.11.3" \
      org.opencontainers.image.description="Open WebUI 0.11.3 with a Magazine new-tab sidebar launcher; the Magazine Function is installed separately" \
      magazine.open-webui.source-commit="${OPEN_WEBUI_SOURCE_COMMIT}"
