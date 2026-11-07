# Builds the text runtime image of compose.yaml, KoboldCpp from the pinned release binary, from the repository root:
#   docker compose --profile ai build lyrveil-llm
# The build downloads koboldcpp-linux-x64 and fails unless its size and SHA-256 match the pin of
# docs/technology.md. The image holds the unpacked launcher only: no single-file binary, no model and no launch file,
# which arrive read-only at /models/llm and /run/lyrveil when the container starts (docs/operations.md, Runtime images).
# Changing the pin needs docs/runtime-contracts.md verified again at the new tag (docs/technology.md, Version policy).
# Run "/usr/local/bin/lyrveil-llm-entrypoint --print-command" in a container to see the command a launch file gives.

# The digest of debian:trixie-slim built on 2026-10-27, so every build starts from the same base.
ARG DEBIAN_IMAGE=debian:trixie-slim@sha256:f6b08b09a05725f9431502d3b5714f5c4576e82c1a84fa1debe0c24a7e0c3ec4

FROM ${DEBIAN_IMAGE} AS download
RUN apt-get update \
    && apt-get install --yes --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /download
ARG KOBOLDCPP_VERSION=v1.124.2
RUN curl --fail --location --silent --show-error --proto '=https' --tlsv1.2 --retry 3 \
        --output koboldcpp-linux-x64 \
        "https://github.com/LostRuins/koboldcpp/releases/download/${KOBOLDCPP_VERSION}/koboldcpp-linux-x64"
# The expected size and hash are declared after the download, so a build with other values reuses the cached download
# and still runs this check; tests/docker/TextRuntime.Tests.ps1 relies on that. The size is checked before the hash so
# a truncated download names its real cause. Either mismatch stops the build.
ARG KOBOLDCPP_SIZE=628981944
ARG KOBOLDCPP_SHA256=c66ae0106673c7d9c41bafcc92c95bc5a6256709e9d853e5e2255bf536c1b069
RUN size="$(stat -c %s koboldcpp-linux-x64)" \
    && if [ "${size}" != "${KOBOLDCPP_SIZE}" ]; then \
        echo "koboldcpp-linux-x64 is ${size} bytes, expected ${KOBOLDCPP_SIZE}" >&2; exit 1; \
    fi \
    && echo "${KOBOLDCPP_SHA256}  koboldcpp-linux-x64" | sha256sum --check --strict -
# --unpack extracts the single-file binary once, so no start needs a writable, executable extraction directory. The
# extraction keeps the private modes of its temporary folder, so everything is made readable for the user 10001. The
# launcher must exist and run: its --help needs no GPU and no model, and proves the unpacked bundle is complete.
RUN chmod 0755 koboldcpp-linux-x64 \
    && ./koboldcpp-linux-x64 --unpack /opt/koboldcpp \
    && chmod -R a+rX,go-w /opt/koboldcpp \
    && test -x /opt/koboldcpp/koboldcpp-launcher \
    && /opt/koboldcpp/koboldcpp-launcher --help > /dev/null

FROM ${DEBIAN_IMAGE} AS runtime
# curl serves the health check of compose.yaml. HOME points into the tmpfs /tmp, the only writable path, for any cache
# a library writes under the home folder.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
ENV HOME=/tmp
COPY --from=download /opt/koboldcpp /opt/koboldcpp
COPY --chmod=0755 docker/llm/entrypoint.sh /usr/local/bin/lyrveil-llm-entrypoint
WORKDIR /tmp
USER 10001:10001
EXPOSE 5001
ENTRYPOINT ["/usr/local/bin/lyrveil-llm-entrypoint"]
