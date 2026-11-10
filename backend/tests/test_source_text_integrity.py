import re
from pathlib import Path


STRING_LITERAL_RE = re.compile(r"""(['"])(.*?)\1""")
SQL_MARKERS = ("SELECT ", "UPDATE ", "INSERT ", "DELETE ")


def find_corrupted_strings(paths):
    problems = []
    for path in paths:
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if ".execute(" in line or any(marker in line.upper() for marker in SQL_MARKERS):
                continue
            for match in STRING_LITERAL_RE.finditer(line):
                body = match.group(2)
                if "???" in body:
                    problems.append(f"{path}:{line_no}:{line.strip()}")
    return problems


def test_source_contains_no_corrupted_chinese_placeholder():
    root = Path(__file__).resolve().parents[2]
    paths = list((root / "backend" / "app").glob("*.py"))
    paths += list((root / "backend" / "benchmark").glob("*.py"))
    paths += list((root / "frontend" / "src").rglob("*.vue"))
    paths += list((root / "frontend" / "src").rglob("*.js"))
    problems = find_corrupted_strings(paths)
    assert problems == [], "\n".join(problems)


def test_integrity_scanner_detects_regression(tmp_path: Path):
    sample = tmp_path / "broken.py"
    sample.write_text('message = "????????"\n', encoding="utf-8")
    problems = find_corrupted_strings([sample])
    assert problems
    assert "broken.py:1" in problems[0]