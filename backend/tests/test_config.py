from pathlib import Path

from app.config import PROJECT_DIR, resolve_project_path


def test_relative_project_path_is_anchored_to_project_root():
    assert resolve_project_path("backend/farmmatch.db") == (PROJECT_DIR / "backend" / "farmmatch.db").resolve()


def test_absolute_project_path_is_preserved(tmp_path: Path):
    target = tmp_path / "farmmatch.db"
    assert resolve_project_path(target) == target.resolve()