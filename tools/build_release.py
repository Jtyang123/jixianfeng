from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "release"
DOCS = ROOT / "docs"
PACKAGE = RELEASE / "急先锋_初赛提交包"
ZIP_PATH = RELEASE / "急先锋_初赛提交包.zip"

EXCLUDED_DIR_NAMES = {
    ".venv", "venv", "env", "node_modules", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "htmlcov", ".git", "release", "data", "logs",
    ".slidep", ".workbuddy", ".vscode", ".idea", "ppt_build",
}
EXCLUDED_FILE_NAMES = {".env", "$null", "dict", ".DS_Store"}
EXCLUDED_FILE_SUFFIXES = (".db", ".db-journal", ".db-wal", ".db-shm", ".pyc", ".pid", ".log")

# 隐藏文件默认不进包（.ppt_check.pdf 这类临时产物曾混入过），只保留运行必需的几个
KEPT_DOTFILES = {".env.example", ".gitignore", ".gitattributes", ".dockerignore"}

# 评审不应看到的内部管理文档：自查报告、待办清单、自评表、赛题原文摘录。
INTERNAL_DOCS = {
    "TRACK1_COMPLIANCE_REVIEW.md",
    "初赛提交清单.md",
    "评分标准对齐清单.md",
    "competition_deck_text.txt",
}
INTERNAL_DOC_SUFFIXES = ("-verify.png", "key-crop.png", "endpoint-crop.png")

# 与提交目录 01_项目白皮书 重复的成品，演示包只保留 md 源文件
DUPLICATED_ARTIFACTS = {
    "赛题一+OKC+急先锋_项目白皮书.pdf",
    "赛题一+OKC+急先锋_项目白皮书.docx",
    # 方案书 HTML 与 05_其他材料 的附录重复，代码包只留 md 源
    "方案书.html",
    # 声明模板由 04_相关证明材料 单独提交，代码包里不需要
    "OKC_项目原创性声明_待签字.docx",
    "OKC_项目原创性声明_待签字.pdf",
}


def _excluded_dir(name: str) -> bool:
    # 隐藏目录一律排除（.github 是 CI 证据，必须保留）
    if name.startswith(".") and name != ".github":
        return True
    # tmp_/build_ 这类临时工作目录不得进包（曾把 5MB 的临时演示稿带进来）
    if name.startswith(("tmp", "build_", "_")):
        return True
    return name in EXCLUDED_DIR_NAMES


def _excluded_file(name: str) -> bool:
    if name in EXCLUDED_FILE_NAMES or name.endswith(EXCLUDED_FILE_SUFFIXES):
        return True
    # 隐藏文件（点开头）除白名单外一律排除，避免校验用临时文件混进评审包
    return name.startswith(".") and name not in KEPT_DOTFILES


def _excluded_internal_doc(relative: Path) -> bool:
    """内部管理文档不进演示包，避免评审看到自查与待办。"""
    if relative.parts[:1] != ("docs",):
        return False
    if relative.name in INTERNAL_DOCS or relative.name in DUPLICATED_ARTIFACTS:
        return True
    return relative.name.endswith(INTERNAL_DOC_SUFFIXES)


def _copy_ignore(directory: str, names: list[str]) -> set[str]:
    parent = Path(directory)
    ignored: set[str] = set()
    for name in names:
        path = parent / name
        if path.is_dir() and _excluded_dir(name):
            ignored.add(name)
        elif path.is_file() and (_excluded_file(name) or _excluded_internal_doc(path.relative_to(ROOT))):
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
        if path.is_file() and _excluded_internal_doc(relative):
            kind = "重复成品" if relative.name in DUPLICATED_ARTIFACTS else "内部文档"
            violations.append(f"{kind}: {relative}")

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

    print("Key materials:")
    for item in files:
        relative = item.relative_to(package)
        if relative.parts[:1] == ("docs",) and (
            item.suffix in {".pdf", ".pptx"} or item.name == "项目白皮书.md"
        ):
            print(f"  OK  {relative} ({_human_size(item.stat().st_size)})")
    if not any(f.suffix == ".pdf" for f in files if f.relative_to(package).parts[:1] == ("docs",)):
        print("  提示：本包不含白皮书 PDF，白皮书按赛事要求单独提交")

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

    # PPT 预览面板会长期占用 docs/ 下的成品，导致覆盖失败；若存在更新的重新构建版本，以它为准
    deck_target = PACKAGE / "docs" / "急先锋_项目讲解PPT.pptx"
    deck_rebuilt = ROOT / "ppt_build" / "急先锋_项目讲解PPT.pptx"
    deck_original = DOCS / "急先锋_项目讲解PPT.pptx"
    if deck_rebuilt.exists() and deck_target.exists():
        if deck_original.exists() and deck_rebuilt.stat().st_mtime > deck_original.stat().st_mtime:
            shutil.copy2(deck_rebuilt, deck_target)
            print("Deck: 使用 ppt_build 中的较新版本")

    (PACKAGE / "提交说明.md").write_text(
        "# 急先锋初赛提交包\n\n"
        "## 第一步：启动并登录（评委请看这一节）\n\n"
        "Windows 下双击或执行：\n\n```powershell\n.\\start.cmd\n```\n\n"
        "浏览器打开 `http://127.0.0.1:8010`。\n\n"
        "- **用户名固定为 `admin`**。\n"
        "- **密码**：首次启动时 `start.cmd` 窗口会打印一段标题为「急先锋 · 首次启动，已创建管理员账号」的信息，"
        "里面直接给出了密码；也可以打开包根目录的 `data/bootstrap-admin-password.txt`，文件内容就是密码。\n"
        "- 密码由系统在首次启动时随机生成、只保存在本机，仓库与压缩包内都不含明文密码，"
        "因此文档里不存在固定口令，请按上面两种方式获取。\n"
        "- 首次登录后建议点右上角钥匙按钮修改密码。\n\n"
        "如果启动窗口已经滚过去看不到密码：直接打开 `data/bootstrap-admin-password.txt` 读取。"
        "如果改过密码又忘记：删除 `data/` 下的数据库文件后重启，系统会重新生成管理员账号并再次打印密码。\n\n"
        "## 运行环境\n\n"
        "如果本机没有 PostgreSQL，系统会自动回退 SQLite；公网部署使用 `Dockerfile` 与 `docker-compose.yml`，"
        "详见 `deploy/DEPLOYMENT.md`。首次运行前需要执行一次 `setup.cmd` 安装依赖并构建前端。\n\n"
        "## 材料\n\n"
        "- `docs/急先锋_项目讲解PPT.pptx`（16 页，官方模版）\n"
        "- `docs/项目白皮书.md`\n"
        "- `docs/安装与运行说明.md`\n"
        "- `docs/DEMO_SCRIPT.md`\n- `docs/DEMO_VIDEO_SCRIPT.md`\n- `docs/答辩问答.md`\n"
        "- `docs/AGENTARTS_VERIFICATION.md`\n- `docs/screenshots/`\n\n"
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