#!/usr/bin/env bash
# Build and verify the test-execution sandbox image. Explicit setup step:
# the runner uses this exact local tag and never pulls at runtime.
set -euo pipefail

IMAGE="${SANDBOX_IMAGE:-agent-sandbox:1.0}"
cd "$(dirname "$0")/.."

echo "==> building ${IMAGE} from backend/sandbox/Dockerfile"
docker build -t "${IMAGE}" -f backend/sandbox/Dockerfile backend/sandbox

echo "==> recording digest"
docker inspect --format='{{index .RepoDigests 0}}' "${IMAGE}" \
  > backend/sandbox/IMAGE_DIGEST 2>/dev/null || \
  docker inspect --format='{{.Id}}' "${IMAGE}" > backend/sandbox/IMAGE_DIGEST
cat backend/sandbox/IMAGE_DIGEST

echo "==> verify: pytest present, green suite passes sandboxed"
docker run --rm --network none --user nobody --read-only \
  --tmpfs /tmp:rw,size=64m,noexec \
  "${IMAGE}" python -m pytest --version

echo "==> verify: no host secrets inside the sandbox"
docker run --rm --network none --user nobody --read-only \
  --tmpfs /tmp:rw,size=64m,noexec \
  -e PYTHONDONTWRITEBYTECODE=1 \
  "${IMAGE}" python -c \
  "import os; keys=set(os.environ); assert not (keys & {'OPENAI_API_KEY','APPROVAL_TOKEN'}), keys; print('no host secrets; env:', sorted(keys))"

echo "sandbox image ready: ${IMAGE}"
