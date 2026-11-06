# Builds the image of the two HTTP fakes for compose.test.yaml, from the repository root:
#   docker build -f tests/fakes/Dockerfile -t lyrveil/fakes:test .
# It is a test image and no service of compose.yaml uses it. It holds only the standard-library fakes: fake_docker and
# fake_nvml import the orchestrator and run in its test stage. The same image serves both runtimes, and the command picks
# the fake: fakes.fake_kobold (port 5001, the default) or fakes.fake_comfy (port 8188).

FROM python:3.12.16-slim-trixie
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app
WORKDIR /app
COPY tests/fakes/__init__.py tests/fakes/scripted_server.py tests/fakes/fake_kobold.py tests/fakes/fake_comfy.py ./fakes/
USER 10001:10001
EXPOSE 5001 8188
CMD ["python", "-m", "fakes.fake_kobold"]
