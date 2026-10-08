from pathlib import Path
from uuid import UUID
from ..config import settings


def run_dir(run_id: str) -> Path:
    path = Path(settings.artifact_dir) / run_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_text(run_id: str, name: str, content: str) -> str:
    path = run_dir(run_id) / name
    path.write_text(content, encoding="utf-8")
    return str(path)


def save_bytes(run_id: str, name: str, content: bytes) -> str:
    path = run_dir(run_id) / name
    path.write_bytes(content)
    return str(path)
