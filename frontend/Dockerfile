# Frontend image: the built static app served by nginx.
#
# Build from the REPO ROOT, same as backend/Dockerfile — keeps one build
# context and one .dockerignore for both images:
#   docker build -f frontend/Dockerfile -t drhp-frontend .
#
# The backend lives in a separate container (see backend/Dockerfile) — this
# image needs to know where to reach it. BACKEND_URL is substituted into the
# nginx config at container START (not build time), via nginx's own
# envsubst-on-templates entrypoint mechanism, so the same built image works
# against any backend address without a rebuild:
#   docker run -e BACKEND_URL=http://backend:8000 -p 8080:80 drhp-frontend

FROM node:20-slim AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ .
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY frontend/nginx.conf.template /etc/nginx/templates/default.conf.template
ENV BACKEND_URL=http://backend:8000

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD wget -q -O- http://127.0.0.1:80/ >/dev/null || exit 1
