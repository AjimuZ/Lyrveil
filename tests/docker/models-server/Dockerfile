# Builds the HTTPS test server of tests/docker/Models.Tests.ps1 (T-DCK-11), from the repository root:
#   docker build -f tests/docker/models-server/Dockerfile -t lyrveil/models-test-server:models-test .
# It is a test image: no service of compose.yaml uses it, and it serves generated files only. openssl creates its test
# certificates at start; the Debian package comes with ca-certificates, and is installed only if it is missing.

FROM python:3.12.16-slim-trixie@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
RUN command -v openssl >/dev/null || (apt-get update && apt-get install -y --no-install-recommends openssl && rm -rf /var/lib/apt/lists/*)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY tests/docker/models-server/server.py ./server.py
EXPOSE 8443
CMD ["python", "/app/server.py"]
