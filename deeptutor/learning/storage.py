from __future__ import annotations

import time
from pathlib import Path

from deeptutor.learning.models import LearningProgress
from deeptutor.services.path_service import get_path_service


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


class LearningStore:
    def __init__(self, root: Path | None = None) -> None:
        self._root = root or (get_path_service().get_workspace_dir() / "learning")
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, book_id: str) -> Path:
        if "/" in book_id or "\\" in book_id or ".." in book_id or ":" in book_id:
            raise ValueError(f"Invalid book_id: {book_id!r}")
        return self._root / f"{book_id}.json"

    def save(self, progress: LearningProgress) -> None:
        progress.updated_at = time.time()
        data = progress.model_dump(mode="json")
        import json

        text = json.dumps(data, ensure_ascii=False, indent=2)
        _atomic_write_text(self._path(progress.book_id), text)

    def load(self, book_id: str) -> LearningProgress | None:
        path = self._path(book_id)
        if not path.exists():
            return None
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
        return LearningProgress.model_validate(data)

    def delete(self, book_id: str) -> None:
        path = self._path(book_id)
        if path.exists():
            path.unlink()

    def exists(self, book_id: str) -> bool:
        return self._path(book_id).exists()


__all__ = ["LearningStore"]
