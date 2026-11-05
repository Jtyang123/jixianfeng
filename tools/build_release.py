from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "release"
PACKAGE = RELEASE / "急先锋_初赛提交包"
ZIP_PATH = RELEASE / "急先锋_初赛提交包.zip"

EXCLUDED_ROOTS = {"node_modules", "__pycache__", ".pytest_cache", ".git", "data", "release"}
EXCLUDED_SUFFIXES = {".db", ".pyc", ".pid"}


def build() -> None:
    if PACKAGE.exists():
        shutil.rmtree(PACKAGE)
    RELEASE.mkdir(exist_ok=True)
    PACKAGE.mkdir(parents=True)
    for source in ROOT.iterdir():
        if source.name in EXCLUDED_ROOTS or source.name.startswith(".edge-"):
            continue
        if source.is_dir():
            shutil.copytree(
                source,
                PACKAGE / source.name,
                ignore=shutil.ignore_patterns(
                    "node_modules", "__pycache__", ".pytest_cache", "*.db", "*.pyc", ".edge-*"
                ),
            )
        elif source.suffix not in EXCLUDED_SUFFIXES and source.name != ".env":
            shutil.copy2(source, PACKAGE / source.name)

    sample = Path(r"D:\FarmMatch_库存数据.xlsx")
    if sample.exists():
        shutil.copy2(sample, PACKAGE / "docs" / "FarmMatch_库存数据.xlsx")
    for screenshot in (PACKAGE / "docs" / "screenshots").glob("solution_page_*.png"):
        screenshot.unlink()

    (PACKAGE / "提交说明.md").write_text(
        "# 急先锋初赛提交包\n\n"
        "## 运行\n\nWindows 下双击或执行：\n\n```powershell\n.\\start.cmd\n```\n\n"
        "浏览器打开 `http://127.0.0.1:8000`。\n\n"
        "如果本机没有 PostgreSQL，系统会自动回退 SQLite；公网部署使用 `Dockerfile` 与 `docker-compose.yml`，详见 `deploy/DEPLOYMENT.md`。\n\n"
        "## 材料\n\n"
        "- `docs/急先锋_项目方案书.pdf`\n- `docs/急先锋_项目讲解PPT.pptx`\n"
        "- `docs/DEMO_SCRIPT.md`\n- `docs/答辩问答.md`\n- `docs/评分标准对齐清单.md`\n"
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
    print(f"Package: {PACKAGE}")
    print(f"Zip: {ZIP_PATH} ({ZIP_PATH.stat().st_size} bytes)")
    print(f"Files: {sum(1 for path in PACKAGE.rglob('*') if path.is_file())}")


if __name__ == "__main__":
    build()
