from __future__ import annotations

import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
RELEASE = ROOT / "release"
PACKAGE_ZIP = RELEASE / "急先锋_初赛提交包.zip"
VIDEO_SUFFIXES = {".mp4", ".mov", ".webm"}
MIN_VIDEO_BYTES = 5 * 1024 * 1024
MIN_DURATION = 150.0
MAX_DURATION = 330.0


def _run(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)


def check_video() -> tuple[bool, str]:
    candidates = [
        path for path in list(DOCS.rglob("*")) + list(RELEASE.rglob("*"))
        if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES
    ]
    large = [path for path in candidates if path.stat().st_size > MIN_VIDEO_BYTES]
    if not large:
        if candidates:
            return False, f"视频体积不足 5MB: {[str(path.relative_to(ROOT)) for path in candidates]}"
        return False, "缺少 3–5 分钟演示视频（docs/ 下未找到 mp4/mov/webm）"
    video = max(large, key=lambda path: path.stat().st_size)
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return True, f"{video.relative_to(ROOT)}，体积 {video.stat().st_size} bytes；未校验时长（ffprobe 不存在）"
    result = _run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(video)])
    if result.returncode != 0:
        return False, f"ffprobe 无法读取时长: {result.stderr.strip()[:200]}"
    try:
        duration = float(result.stdout.strip())
    except ValueError:
        return False, f"ffprobe 返回无效时长: {result.stdout.strip()}"
    if not MIN_DURATION <= duration <= MAX_DURATION:
        return False, f"{video.relative_to(ROOT)} 时长 {duration:.1f}s，要求 150–330s"
    return True, f"{video.relative_to(ROOT)}，时长 {duration:.1f}s，体积 {video.stat().st_size} bytes"


def check_fresh_file(path: Path, label: str) -> tuple[bool, str]:
    if not path.exists():
        return False, f"缺少 {label}: {path.relative_to(ROOT)}"
    age_days = (time.time() - path.stat().st_mtime) / 86400
    status = "PASS" if age_days <= 7 else "FAIL"
    return age_days <= 7, f"{path.relative_to(ROOT)}，mtime={time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(path.stat().st_mtime))}，{age_days:.1f} 天前"


def check_package() -> tuple[bool, str]:
    if not PACKAGE_ZIP.exists():
        return False, f"缺少提交包: {PACKAGE_ZIP.relative_to(ROOT)}"
    forbidden: list[str] = []
    with zipfile.ZipFile(PACKAGE_ZIP) as archive:
        names = archive.namelist()
    for name in names:
        parts = PurePosixPath(name).parts
        if any(part.startswith(".venv") or part.startswith(".pytest_tmp") for part in parts):
            forbidden.append(name)
        if any(part in {"logs", "$null", "dict"} for part in parts):
            forbidden.append(name)
    if forbidden:
        return False, f"提交包含杂物: {forbidden[:20]}"
    return True, f"{PACKAGE_ZIP.relative_to(ROOT)}，{len([name for name in names if not name.endswith('/')])} 个文件，无 .venv/logs/.pytest_tmp/$null/dict"


def check_git() -> tuple[bool, str]:
    status = _run(["git", "status", "--porcelain"])
    remote = _run(["git", "remote", "get-url", "origin"])
    if status.returncode != 0:
        return False, status.stderr.strip() or "git status 失败"
    if status.stdout.strip():
        return False, f"工作区不干净: {status.stdout.strip().splitlines()[:10]}"
    if remote.returncode != 0 or not remote.stdout.strip():
        return False, "git remote origin 未配置"
    return True, f"工作区干净；origin={remote.stdout.strip()}"


def main() -> int:
    checks = [
        ("演示视频", check_video()),
        ("方案书 PDF", check_fresh_file(DOCS / "急先锋_项目方案书.pdf", "方案书 PDF")),
        ("讲解 PPT", check_fresh_file(DOCS / "急先锋_项目讲解PPT.pptx", "讲解 PPT")),
        ("提交包", check_package()),
        ("代码仓库", check_git()),
    ]
    passed = 0
    failed = 0
    for label, (ok, evidence) in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {label}: {evidence}")
        if ok:
            passed += 1
        else:
            failed += 1
    print(f"\n{passed} 项通过 / {failed} 项失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())