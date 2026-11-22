"""Проверка репозитория перед отправкой: личные пути, контакты, ключи, ответы к тестовым заданиям."""
from __future__ import annotations

import re
import subprocess

from .core import ROOT
from . import textio

TEXT_EXT = {".md", ".txt", ".json", ".jsonl", ".py", ".sh", ".csv", ".html", ".mdc", ".yml", ".yaml", ".toml", ""}
SKIP = (".git/", "__pycache__/", "tests/", "tools/stratlib/release.py")
HOME_RE = re.compile(r"(/Users/[^/\s\"']+|/home/(?!claude\b)[^/\s\"']+|[A-Z]:\\\\Users\\\\[^\\\s\"']+)")
# адреса и телефоны, которые законно лежат в материалах (примеры, справочные номера), не считаем
ALLOWED = re.compile(r"example\.(?:org|com)|noreply@|8[\s-]?800")


def _files():
    try:
        out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=ROOT, capture_output=True,
                             text=True, timeout=20)
        if out.returncode == 0:
            return [ROOT / f for f in out.stdout.splitlines()]
    except (OSError, subprocess.SubprocessError):
        pass
    return [p for p in ROOT.rglob("*") if p.is_file()]


def scan() -> list:
    from .hooks import _SECRETS
    found = []
    for p in _files():
        rel = p.relative_to(ROOT).as_posix()
        if rel.startswith(SKIP) or p.suffix.lower() not in TEXT_EXT or not p.exists():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for ln, line in enumerate(text.splitlines(), 1):
            m = HOME_RE.search(line)
            if m:
                found.append((rel, ln, "личный путь", m.group(1)))
            for rx, name in _SECRETS:
                if rx.search(line):
                    found.append((rel, ln, "похоже на ключ", name))
        if rel.startswith("projects/") and "/sources/" not in rel:
            plain = re.sub(r"https?://\S+", " ", text)  # цифры в адресах страниц — не номера карт
            plain = re.sub(r"\d+\.\d+(?:e-?\d+)?", " ", plain)  # дробные числа в журналах стоимости тоже
            for h in textio.pii_scan(plain):
                line = plain.splitlines()[h["line"] - 1]
                if not ALLOWED.search(line):
                    found.append((rel, h["line"], "персональные данные", f"{h['type']} {h['masked']}"))
    if not (ROOT / "evals" / "answers").exists():
        found.append(("evals/answers", 0, "нет ответов к тестам", "верните папку, иначе замер нельзя повторить"))
    return found


def cmd_release_check(args):
    found = scan()
    if not found:
        print("Можно отправлять: личных путей, контактов и ключей не найдено, ответы к тестам на месте.")
        return 0
    print("Перед отправкой стоит поправить:")
    for rel, ln, kind, what in found[:80]:
        print(f"  {rel}:{ln}  {kind}: {what}")
    if len(found) > 80:
        print(f"  … и ещё {len(found) - 80}")
    print("\nЛичные пути в журналах можно заменить так: ./strat release-check --fix-paths")
    if getattr(args, "fix_paths", False):
        n = 0
        for p in _files():
            rel = p.relative_to(ROOT).as_posix()
            if rel.startswith(SKIP) or p.suffix.lower() not in TEXT_EXT or not p.exists():
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            new = re.sub(re.escape(str(ROOT)) + r"/?", "", text)
            new = HOME_RE.sub("~", new)
            if new != text:
                p.write_text(new, encoding="utf-8")
                n += 1
        print(f"Пути заменены в {n} файлах. Запустите проверку ещё раз.")
    return 1
