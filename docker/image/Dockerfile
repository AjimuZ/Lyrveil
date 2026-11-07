# Builds the image runtime image of compose.yaml, ComfyUI at the pinned commit, from the repository root:
#   docker compose --profile ai build lyrveil-image
# Regenerates docker/image/requirements.lock after a change of the commit, since uv is not installed on Windows:
#   docker build -f docker/image/Dockerfile --target lockfile --output type=local,dest=docker/image .
# The build fails unless the fetched tree is the pinned commit, every installed package is a wheel pinned with its hash
# in requirements.lock, the lock satisfies the commit's own requirements.txt, and PyTorch is the CUDA 13.0 build. The
# image holds no model and no launch file, which arrive read-only at /opt/ComfyUI/models/checkpoints and /run/lyrveil
# when the container starts (docs/operations.md, Runtime images). Changing the pin needs docs/runtime-contracts.md
# verified again at the new tag (docs/technology.md, Version policy).
# Run "/usr/local/bin/lyrveil-image-entrypoint --print-command" in a container to see the command a launch file gives.

ARG PYTHON_IMAGE=python:3.12.16-slim-trixie

FROM ${PYTHON_IMAGE} AS source
RUN apt-get update \
    && apt-get install --yes --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
ARG COMFYUI_REPOSITORY=https://github.com/Comfy-Org/ComfyUI.git
ARG COMFYUI_COMMIT=426f5eea52a7ece1dd599fff4cb6cd1f352ddd24
WORKDIR /opt/ComfyUI
# Git checks the content against the commit hash, so the tree is exactly the pinned one. .git is removed, so the image
# holds the sources only.
RUN git init --quiet \
    && git remote add origin "${COMFYUI_REPOSITORY}" \
    && git fetch --quiet --depth 1 origin "${COMFYUI_COMMIT}" \
    && git checkout --quiet --detach FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = "${COMFYUI_COMMIT}" \
    && rm -rf .git

FROM ${PYTHON_IMAGE} AS uv
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.12 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1

# Resolves the commit's requirements.txt for this Python. --torch-backend takes the PyTorch packages from the CUDA 13.0
# index of download.pytorch.org and everything else from PyPI. Wheels only, so no install ever runs a build script.
FROM uv AS lock
WORKDIR /lock
COPY --from=source /opt/ComfyUI/requirements.txt ./requirements.txt
RUN uv pip compile requirements.txt \
        --torch-backend cu130 \
        --only-binary :all: \
        --generate-hashes \
        --custom-compile-command "docker build -f docker/image/Dockerfile --target lockfile --output type=local,dest=docker/image ." \
        --output-file requirements.lock

FROM scratch AS lockfile
COPY --from=lock /lock/requirements.lock /requirements.lock

# "pip sync" installs exactly the lock and refuses a file without a hash. The offline install of requirements.txt then
# fails unless the lock already satisfies every requirement of the pinned commit, so a stale lock stops the build.
FROM uv AS deps
COPY docker/image/requirements.lock /tmp/requirements.lock
COPY --from=source /opt/ComfyUI/requirements.txt /tmp/requirements.txt
RUN uv venv --quiet /opt/venv \
    && uv pip sync --python /opt/venv/bin/python --require-hashes --only-binary :all: --torch-backend cu130 \
        --compile-bytecode /tmp/requirements.lock \
    && uv pip check --python /opt/venv/bin/python \
    && uv pip install --python /opt/venv/bin/python --no-index -r /tmp/requirements.txt

# Counts the CLIP tokens of the tag vocabulary with this tree's tokenizer and environment (T-IMG-16). After a change of
# the vocabulary or of the pin, rewrite its clip_tokens with:
#   docker build -f docker/image/Dockerfile --target clip-tokens --output type=local,dest=backend/src/lyrveil/images/vocabulary .
# The target clip-tokens-check fails when a committed count differs. Neither stage is part of the runtime image.
FROM deps AS clip-tokens-run
COPY --from=source /opt/ComfyUI /opt/ComfyUI
COPY docker/image/clip_tokens.py backend/src/lyrveil/images/vocabulary/tags.v1.json /tmp/clip/
RUN /opt/venv/bin/python /tmp/clip/clip_tokens.py write /tmp/clip/tags.v1.json /out/tags.v1.json

FROM scratch AS clip-tokens
COPY --from=clip-tokens-run /out/tags.v1.json /tags.v1.json

FROM clip-tokens-run AS clip-tokens-check
RUN /opt/venv/bin/python /tmp/clip/clip_tokens.py check /tmp/clip/tags.v1.json

FROM ${PYTHON_IMAGE} AS runtime
# HOME and TMPDIR point into the tmpfs /tmp/comfy of compose.yaml, the only writable path; the entrypoint creates both.
# No bytecode is written at run time: everything is compiled here.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    HOME=/tmp/comfy/home \
    TMPDIR=/tmp/comfy/tmp
COPY --from=deps /opt/venv /opt/venv
COPY --from=source /opt/ComfyUI /opt/ComfyUI
COPY --chmod=0755 docker/image/entrypoint.sh /usr/local/bin/lyrveil-image-entrypoint
# PyTorch's CUDA version is read from the installed build, so it needs no GPU.
RUN python -m compileall -q /opt/ComfyUI \
    && cuda="$(python -c 'import torch; print(torch.version.cuda or "none")')" \
    && case "${cuda}" in 13.0*) ;; *) echo "PyTorch is built for CUDA ${cuda}, expected 13.0" >&2; exit 1 ;; esac
WORKDIR /opt/ComfyUI
USER 10001:10001
EXPOSE 8188
ENTRYPOINT ["/usr/local/bin/lyrveil-image-entrypoint"]
