"""把 docs/方案书.md 渲染成 A4 PDF（Edge headless 打印）。

用法：
    .venv/Scripts/python.exe tools/build_plan_pdf.py
    .venv/Scripts/python.exe tools/build_plan_pdf.py --no-copy   # 只生成到 docs/

产物：
    docs/方案书.html                      中间产物（在线阅读版）
    docs/赛题一+OKC+急先锋_方案书.pdf      最终 PDF
    桌面 01_方案书与项目白皮书/           同步一份（默认）
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
SOURCE = DOCS / "方案书.md"
HTML = DOCS / "方案书.html"
PDF = DOCS / "赛题一+OKC+急先锋_方案书.pdf"
DESKTOP_DIR = Path.home() / "Desktop" / "赛题一+OKC+急先锋" / "01_方案书与项目白皮书"

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

CSS = """
@page { size: A4; margin: 17mm 15mm 16mm 15mm; }
* { box-sizing: border-box; }
body {
  font-family: "Microsoft YaHei", "PingFang SC", "Source Han Sans SC", sans-serif;
  font-size: 10.4pt; line-height: 1.72; color: #1b1b1b; margin: 0;
  text-align: justify;
}
h1 { font-size: 19pt; line-height: 1.4; text-align: center; margin: 8mm 0 4mm; }
h2 {
  font-size: 13pt; margin: 16px 0 8px; padding-left: 9px;
  border-left: 4px solid #0f766e; page-break-after: avoid;
}
h3 { font-size: 11.2pt; margin: 12px 0 6px; page-break-after: avoid; }
h4 { font-size: 10.6pt; margin: 10px 0 4px; }
p { margin: 5px 0 7px; }
ul, ol { margin: 5px 0 8px; padding-left: 22px; }
li { margin: 2px 0; }
strong { color: #0b3d38; }
code {
  font-family: Consolas, "Courier New", monospace; font-size: 9pt;
  background: #f2f5f4; padding: 1px 4px; border-radius: 3px; color: #0b5a52;
}
pre {
  background: #f6f8f7; border: 1px solid #d8e0de; border-radius: 4px;
  padding: 8px 10px; font-size: 8.6pt; line-height: 1.5;
  white-space: pre-wrap; word-break: break-all; page-break-inside: avoid;
}
pre code { background: none; padding: 0; color: #16332f; }
table {
  width: 100%; border-collapse: collapse; margin: 8px 0 10px;
  font-size: 9pt; page-break-inside: avoid;
}
th, td { border: 1px solid #b7c4c1; padding: 4px 6px; vertical-align: top; }
th { background: #eef4f2; font-weight: 600; text-align: left; }
tr:nth-child(even) td { background: #fafcfb; }
blockquote {
  margin: 8px 0; padding: 6px 12px; border-left: 3px solid #cbd5d2;
  background: #f8faf9; color: #454545;
}
img { max-width: 100%; display: block; margin: 6px auto; page-break-inside: avoid; }
hr { border: none; border-top: 1px solid #dde5e3; margin: 12px 0; }
a { color: #0f766e; text-decoration: none; }
"""


def _find_edge() -> str:
    for path in EDGE_CANDIDATES:
        if Path(path).exists():
            return path
    raise SystemExit("找不到 Microsoft Edge，无法打印 PDF")


def _render_html() -> str:
    text = SOURCE.read_text(encoding="utf-8")
    text = re.sub(r"^\s*<a id=\"[^\"]*\"></a>\s*$", "", text, flags=re.M)
    body = markdown.markdown(
        text,
        extensions=["tables", "fenced_code", "sane_lists", "nl2br"],
        output_format="html5",
    )
    # 图片换成绝对 file URI，避免 headless 相对路径解析失败
    def to_uri(match: re.Match[str]) -> str:
        src = match.group(1)
        if src.startswith(("http://", "https://", "file://", "data:")):
            return match.group(0)
        target = (DOCS / src).resolve()
        return match.group(0).replace(src, target.as_uri())

    body = re.sub(r'<img[^>]*src="([^"]+)"', lambda m: to_uri(m), body)
    html = (
        "<!DOCTYPE html>\n<html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        f"<title>急先锋技术方案书</title><style>{CSS}</style></head><body>\n"
        f"{body}\n</body></html>\n"
    )
    HTML.write_text(html, encoding="utf-8")
    return html


def _print_pdf(edge: str) -> None:
    if PDF.exists():
        PDF.unlink()
    cmd = [
        edge,
        "--headless=new",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
        "--virtual-time-budget=15000",
        "--run-all-compositor-stages-before-draw",
        # 新旧两版开关同时给：Chrome 111+ 用 --no-pdf-header-footer，
        # 旧版用 --print-to-pdf-no-header，缺一个就会在页脚打出本机 file:// 路径
        "--no-pdf-header-footer",
        "--print-to-pdf-no-header",
        f"--print-to-pdf={PDF}",
        HTML.as_uri(),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if not PDF.exists():
        raise SystemExit(f"PDF 生成失败\nstdout={result.stdout[-600:]}\nstderr={result.stderr[-600:]}")


def _verify() -> None:
    from pypdf import PdfReader

    reader = PdfReader(str(PDF))
    text = "\n".join((page.extract_text() or "") for page in reader.pages)
    cn = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fa5")
    checks = {
        "RUN-20261114-210325-F769": "最新运行 ID",
        "8,656": "六阶段合计",
        "9,177": "总耗时",
        "5.68": "偏差比例",
        "14,315": "旧数据残留",
    }
    print(f"PDF: {PDF.name}  页数={len(reader.pages)}  中文字数={cn}  大小={PDF.stat().st_size // 1024}KB")
    for token, label in checks.items():
        hit = text.count(token)
        flag = "OK " if (hit > 0) != (token == "14,315") else "!! "
        print(f"  {flag}{label} {token}: {hit}")
    if cn < 5000:
        raise SystemExit(f"字数 {cn} 低于赛制 5000 字要求")
    if "14,315" in text:
        raise SystemExit("PDF 里仍有旧阶段数据 14,315 ms，源文件未更新")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-copy", action="store_true", help="只生成到 docs/，不同步桌面")
    args = parser.parse_args()

    if not SOURCE.exists():
        raise SystemExit(f"源文件不存在: {SOURCE}")
    edge = _find_edge()
    _render_html()
    _print_pdf(edge)
    _verify()

    if not args.no_copy and DESKTOP_DIR.exists():
        target = DESKTOP_DIR / PDF.name
        shutil.copy2(PDF, target)
        print(f"已同步: {target} ({os.path.getsize(target) // 1024}KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
