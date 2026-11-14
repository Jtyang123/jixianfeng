from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "release"
PACKAGE = RELEASE / "急先锋_初赛提交包"
ZIP_PATH = RELEASE / "急先锋_初赛提交包.zip"

EXCLUDED_DIR_NAMES = {
    ".venv", "venv", "env", "node_modules", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "htmlcov", ".git", "release", "data", "logs",
    ".slidep", ".workbuddy", ".vscode", ".idea",
}
EXCLUDED_FILE_NAMES = {".env", "$null", "dict", ".DS_Store"}
EXCLUDED_FILE_SUFFIXES = (".db", ".db-journal", ".db-wal", ".db-shm", ".pyc", ".pid", ".log")


def _excluded_dir(name: str) -> bool:
    return (
        name.startswith(".pytest_tmp")
        or name.startswith(".edge-")
        or name in EXCLUDED_DIR_NAMES
    )


def _excluded_file(name: str) -> bool:
    return name in EXCLUDED_FILE_NAMES or name.endswith(EXCLUDED_FILE_SUFFIXES)


def _copy_ignore(directory: str, names: list[str]) -> set[str]:
    parent = Path(directory)
    ignored: set[str] = set()
    for name in names:
        path = parent / name
        if path.is_dir() and _excluded_dir(name):
            ignored.add(name)
        elif path.is_file() and _excluded_file(name):
            ignored.add(name)
    return ignored


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{size}B"


def _verify(package: Path) -> None:
    violations: list[str] = []
    files = [path for path in package.rglob("*") if path.is_file()]
    for path in package.rglob("*"):
        relative = path.relative_to(package)
        parts = relative.parts
        if any(_excluded_dir(part) for part in parts[:-1] if not path.is_file()) and path.is_dir():
            violations.append(str(relative))
        if any(part.startswith(".venv") or "_pytest_tmp" in part for part in parts):
            violations.append(str(relative))
        if path.is_file() and _excluded_file(path.name):
            violations.append(str(relative))

    top_dirs: list[tuple[str, int]] = []
    for child in package.iterdir():
        if child.is_dir():
            size = sum(path.stat().st_size for path in child.rglob("*") if path.is_file())
            top_dirs.append((child.name, size))
    top_dirs.sort(key=lambda item: item[1], reverse=True)

    print(f"Files: {len(files)}")
    print("Top directories:")
    for name, size in top_dirs[:10]:
        print(f"  {name}: {_human_size(size)}")
    if len(files) > 400:
        print(f"WARNING: package has {len(files)} files; review before submission")
    if violations:
        raise SystemExit(f"提交包含杂物: {violations[:20]}")


def build() -> None:
    if PACKAGE.exists():
        shutil.rmtree(PACKAGE)
    RELEASE.mkdir(exist_ok=True)
    PACKAGE.mkdir(parents=True)
    for source in ROOT.iterdir():
        if source.is_dir() and _excluded_dir(source.name):
            continue
        if source.is_file() and _excluded_file(source.name):
            continue
        if source.is_dir():
            shutil.copytree(source, PACKAGE / source.name, ignore=_copy_ignore)
        else:
            shutil.copy2(source, PACKAGE / source.name)

    sample = Path(r"D:\FarmMatch_库存数据.xlsx")
    if sample.exists():
        shutil.copy2(sample, PACKAGE / "docs" / "FarmMatch_库存数据.xlsx")

    (PACKAGE / "提交说明.md").write_text(
        "# 急先锋初赛提交包\n\n"
        "## 运行\n\nWindows 下双击或执行：\n\n```powershell\n.\\start.cmd\n```\n\n"
        "浏览器打开 `http://127.0.0.1:8010`。\n\n"
        "如果本机没有 PostgreSQL，系统会自动回退 SQLite；公网部署使用 `Dockerfile` 与 `docker-compose.yml`，详见 `deploy/DEPLOYMENT.md`。\n\n"
        "## 材料\n\n"
        "- `docs/急先锋_项目方案书.pdf`\n- `docs/急先锋_项目讲解PPT.pptx`\n"
        "- `docs/DEMO_SCRIPT.md`\n- `docs/DEMO_VIDEO_SCRIPT.md`\n- `docs/答辩问答.md`\n- `docs/评分标准对齐清单.md`\n"
        "- `docs/初赛提交清单.md`\n- `docs/screenshots/`\n\n"
        "## 重要说明\n\n华为云 AgentArts 的真实调用需要配置 `AGENTARTS_BASE_URL`、`AGENTARTS_API_KEY` 和模型服务。"
        "未配置时，系统使用本地确定性回退以保证演示可运行。\n",
        encoding="utf-8",
    )

    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in PACKAGE.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(PACKAGE))
    _verify(PACKAGE)
    print(f"Package: {PACKAGE}")
    print(f"Zip: {ZIP_PATH} ({ZIP_PATH.stat().st_size} bytes)")


if __name__ == "__main__":
    build()