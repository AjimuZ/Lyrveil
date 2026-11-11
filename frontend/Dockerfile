# Builds the frontend image from the repository root, the build context of every image:
#   docker build -f frontend/Dockerfile -t lyrveil/frontend:local .
# Every installed package comes from package-lock.json: "npm ci" refuses a lock that does not match package.json.
# The build stage runs the same strict build as "scripts\lyrveil.cmd test lint", so an image never holds a build
# that printed a warning or loads anything from the network.

FROM node:24.22.2-alpine@sha256:a34ab6d7f6f4813f4d0c5469d74685604b962fbdbf5793a7bc7b4998ecb867c8 AS build
ENV NG_CLI_ANALYTICS=false \
    NG_FORCE_TTY=false
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY frontend/angular.json frontend/tsconfig.json frontend/tsconfig.app.json frontend/tsconfig.spec.json ./
COPY frontend/tools/build-strict.mjs ./tools/build-strict.mjs
COPY frontend/src ./src
RUN node tools/build-strict.mjs

# The unprivileged image runs nginx as a non-root user on port 8080, with its pid and temporary files in /tmp,
# which Compose mounts as tmpfs on a read-only root file system. Like every service, it runs as 10001:10001 rather than
# the image's own user 101 (docs/security.md, Containers and networks); nginx only reads the world-readable configuration and files.
FROM nginxinc/nginx-unprivileged:1.30.6-alpine@sha256:f5116ed4606f3eb015dd4a8e28cb9b351476dc0ad4bc6e9f93b19cec130cdf03 AS runtime
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist/lyrveil/browser /usr/share/nginx/html
USER 10001:10001
EXPOSE 8080
