# Test-execution sandbox image. Pinned minimal base; the only extra
# dependency is pytest (fixture suites are stdlib-only). Built and
# verified explicitly via scripts/setup-sandbox.sh — never pulled or
# selected at runtime. Tests run as `nobody` against a read-only
# workspace mount; see DockerSandboxRunner for the confinement flags.
FROM python:3.12-slim-bookworm

RUN pip install --no-cache-dir pytest==8.3.4 \
    && rm -rf /root/.cache

ENV PYTHONDONTWRITEBYTECODE=1 \
    LANG=C.UTF-8

WORKDIR /work
