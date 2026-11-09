"""Проверка документов проекта: ссылки на реестр, разделение фактов и гипотез, обязательные разделы.

Правила намеренно простые и детерминированные, чтобы их можно было объяснить руководителю и жюри:
- E1xx — целостность доказательной базы (проверяются всегда, в том числе хуком при завершении хода);
- E2xx — полнота документов (жёстко проверяются при закрытии этапа: ./strat stage <этап> done).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .core import get_project, parse_date, today

REF_RE = re.compile(r"\[((?:[CSDKH]-\d{3,})(?:\s*[,;]\s*(?:[CSDKH]-\d{3,}))*)\]")
ID_RE = re.compile(r"[CSDKH]-\d{3,}")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
ITEM_RE = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+")
EVIDENCE = {"fact", "observation", "estimate"}

REQUIRED = {
    "brief.md": [["вопрос"], ["решени"], ["получател", "для кого", "кому"], ["огранич"], ["данн", "материал"]],
    "research/findings.md": [["известно"], ["не знаем", "пробел", "неизвестн"]],
    "strategy/product.md": [["аудитори"], ["задач", "jtbd"], ["альтернатив"], ["направлени"], ["отказ"],
                            ["экономик", "затрат", "доход"]],
    "strategy/comms.md": [["аудитори"], ["барьер"], ["сообщени"], ["подтвержд", "доказател"], ["канал"],
                          ["обещани"]],
    "strategy/validation.md": [["ожидаем"], ["сравни", "контрол", "база"], ["срок"],
                               ["нежелательн", "побочн", "ограничител"]],
    "summary.md": [],
}
STAGE_DOCS = {
    "frame": ["brief.md"],
    "research": ["research/findings.md"],
    "options": ["options.md"],
    "strategy": ["strategy/product.md", "strategy/comms.md", "strategy/validation.md"],
    "report": ["brief.md", "research/findings.md", "options.md", "strategy/product.md", "strategy/comms.md",
               "strategy/validation.md", "summary.md"],
}
SKIP_DIRS = ("sources/", "inputs/", "log/", "interviews/real/", "interviews/synthetic/")
SKIP_FILES = {"README.md", "report.md"}


def _low(s: str) -> str:
    return (s or "").lower().replace("ё", "е")


def strip_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)


def parse_blocks(text: str) -> list:
    """Разбивает markdown на блоки: заголовки, пункты списков, абзацы, строки таблиц.

    У каждого блока: line (номер первой строки), kind, text, path — список (уровень, заголовок).
    """
    lines = strip_comments(text).splitlines()
    blocks, path = [], []
    cur = None
    in_code = False

    def flush():
        nonlocal cur
        if cur and cur["text"].strip():
            blocks.append(cur)
        cur = None

    for i, raw in enumerate(lines, 1):
        line = raw.rstrip()
        if line.strip().startswith("```"):
            in_code = not in_code
            flush()
            continue
        if in_code:
            continue
        m = HEADING_RE.match(line)
        if m:
            flush()
            level, title = len(m.group(1)), m.group(2).strip()
            path = [p for p in path if p[0] < level] + [(level, title)]
            blocks.append({"line": i, "kind": "heading", "text": title, "path": list(path), "level": level})
            continue
        if not line.strip():
            flush()
            continue
        if line.lstrip().startswith("|"):
            flush()
            if "-" in line and set(line.strip()) <= set("|:- "):
                continue
            blocks.append({"line": i, "kind": "table", "text": line.strip(), "path": list(path)})
            continue
        if ITEM_RE.match(line):
            flush()
            cur = {"line": i, "kind": "item", "text": line.strip(), "path": list(path)}
            continue
        if cur is None:
            cur = {"line": i, "kind": "para", "text": line.strip(), "path": list(path)}
        else:
            cur["text"] += " " + line.strip()
    flush()
    return blocks


def refs_in(text: str) -> list:
    out = []
    for m in REF_RE.finditer(text):
        out.extend(ID_RE.findall(m.group(1)))
    return out


SECTION_KINDS = [
    ("synthetic", re.compile(r"синтет|модельн")),
    ("objections", re.compile(r"возражени|контраргумент")),
    ("hypotheses", re.compile(r"гипотез|должно быть правдой|допущени|предположени")),
    ("forecasts", re.compile(r"прогноз")),
    ("gaps", re.compile(r"не знаем|пробел|неизвестн|противореч")),
    ("facts", re.compile(r"^факты|\bфакты\b|что известно|^известно")),
]


def section_kind(path) -> str:
    for _, title in reversed(path):
        t = _low(title)
        for kind, rx in SECTION_KINDS:
            if rx.search(t):
                return kind
    return ""


def path_str(path) -> str:
    return " › ".join(t for _, t in path) or "(начало)"


def _issue(sev, code, file, line, msg):
    return {"severity": sev, "code": code, "file": file, "line": line, "message": msg}


def lint_file(rel: str, text: str, cmap: dict, smap: dict, strict: bool) -> list:
    issues = []
    blocks = parse_blocks(text)
    t0 = today()
    for b in blocks:
        ids = refs_in(b["text"])
        kind = section_kind(b["path"])
        for rid in ids:
            if rid[0] == "C":
                c = cmap.get(rid)
                if not c:
                    issues.append(_issue("error", "E101", rel, b["line"], f"ссылка на несуществующую запись {rid}"))
                    continue
                st = c.get("status")
                if st in ("retracted", "superseded"):
                    hint = f", замена: {c.get('superseded_by')}" if c.get("superseded_by") else ""
                    issues.append(_issue("error", "E102", rel, b["line"],
                                         f"{rid} {'отозвано' if st == 'retracted' else 'заменено'}{hint} — "
                                         f"обновите формулировку или основание"))
                elif st == "disputed":
                    issues.append(_issue("warning", "W103", rel, b["line"],
                                         f"{rid} оспорено ({c.get('status_note', '')}) — оговорите это в тексте"))
                if c.get("type") == "synthetic" and kind not in ("hypotheses", "synthetic"):
                    issues.append(_issue("error", "E104", rel, b["line"],
                                         f"синтетический ответ {rid} в разделе «{path_str(b['path'])}»: модельные "
                                         f"респонденты не доказывают спрос — переносите в гипотезы"))
                exp = parse_date(c.get("expires"))
                if exp and exp < t0 and st == "active":
                    issues.append(_issue("warning", "W109", rel, b["line"], f"{rid} устарело (срок {c['expires']})"))
            elif rid[0] == "S" and rid not in smap:
                issues.append(_issue("error", "E101", rel, b["line"], f"ссылка на несуществующий источник {rid}"))
        if kind == "facts" and b["kind"] in ("item", "table"):
            if b["kind"] == "table" and not ids and _is_header_row(b, blocks):
                continue
            low = _low(b["text"])
            if not ids:
                if "нет данных" in low or "не найден" in low:
                    continue
                issues.append(_issue("error", "E105", rel, b["line"],
                                     f"в разделе «{path_str(b['path'])}» утверждение без ссылки на реестр: "
                                     f"«{b['text'][:90]}»"))
                continue
            cids = [x for x in ids if x[0] == "C" and x in cmap]
            if cids and not any(cmap[x].get("type") in EVIDENCE for x in cids):
                types = ", ".join(sorted({cmap[x].get("type") for x in cids}))
                issues.append(_issue("error", "E106", rel, b["line"],
                                     f"в разделе фактов пункт опирается только на {types} — это не факт; "
                                     f"перенесите в гипотезы/прогнозы или добавьте подтверждение"))
            for x in cids:
                if cmap[x].get("verified") is False:
                    issues.append(_issue("warning", "W107", rel, b["line"], f"у {x} цитата не найдена в источнике"))
            rels = []
            for x in cids:
                for s in cmap[x].get("sources", []):
                    if s in smap:
                        rels.append(smap[s].get("reliability", "?"))
            if rels and all(r in ("D",) for r in rels):
                issues.append(_issue("warning", "W108", rel, b["line"],
                                     "факт опирается только на источники уровня D (блоги, форумы)"))
    return issues


def _is_header_row(b, blocks) -> bool:
    idx = blocks.index(b)
    return idx == 0 or blocks[idx - 1]["kind"] != "table"


def check_required(rel: str, text: str, strict: bool) -> list:
    issues = []
    req = REQUIRED.get(rel)
    if req is None:
        return issues
    blocks = parse_blocks(text)
    heads = [b for b in blocks if b["kind"] == "heading"]
    for alts in req:
        found = [h for h in heads if any(a in _low(h["text"]) for a in alts)]
        if not found:
            issues.append(_issue("error", "E201", rel, 1, f"нет раздела «{alts[0]}…»"))
            continue
        h = found[0]
        content = [b for b in blocks if b["kind"] != "heading" and b["path"][: len(h["path"])] == h["path"]]
        if not content and not any(i["line"] == h["line"] and i["code"] == "E211" for i in issues):
            issues.append(_issue("error" if strict else "warning", "E211", rel, h["line"],
                                 f"раздел «{h['text']}» пустой"))
    return issues


def check_options(text: str, cmap: dict, strict: bool, raw: str) -> list:
    rel = "options.md"
    issues = []
    blocks = parse_blocks(text)
    opts = [b for b in blocks if b["kind"] == "heading" and b["level"] == 2 and _low(b["text"]).startswith("вариант")]
    if len(opts) < 3:
        issues.append(_issue("error", "E202", rel, 1, f"вариантов {len(opts)}, нужно не меньше трёх"))
    sq_words = ("статус-кво", "статус кво", "оставить как есть", "ничего не менять", "без изменений")
    close_words = ("закрыть", "свернуть", "остановить", "закрытие", "выйти из")
    if opts and not any(any(w in _low(o["text"]) for w in sq_words) for o in opts):
        issues.append(_issue("error", "E203", rel, opts[0]["line"],
                             "нет варианта «оставить как есть» (статус-кво) — назовите его в заголовке"))
    if opts and not any(any(w in _low(o["text"]) for w in close_words) for o in opts) and \
            not re.search(r"<!--\s*закрытие не рассматриваем:", _low(raw)):
        issues.append(_issue("warning", "W204", rel, opts[0]["line"],
                             "нет варианта «закрыть направление»; если он неуместен, объясните в комментарии "
                             "вида <!-- закрытие не рассматриваем: причина -->"))
    for o in opts:
        sub = [b for b in blocks if len(b["path"]) >= len(o["path"]) and b["path"][: len(o["path"])] == o["path"]]
        sub_heads = [b for b in sub if b["kind"] == "heading" and b is not o]
        kinds = {section_kind(h["path"]) for h in sub_heads}
        obj_items = [b for b in sub if b["kind"] != "heading" and section_kind(b["path"]) == "objections"]
        if "objections" not in kinds or not obj_items:
            issues.append(_issue("error", "E205", rel, o["line"],
                                 f"у «{o['text']}» нет сильных возражений (подраздел «Сильные возражения»)"))
        if "facts" not in kinds:
            issues.append(_issue("error" if strict else "warning", "E206", rel, o["line"],
                                 f"у «{o['text']}» нет подраздела «Факты»"))
        if "hypotheses" not in kinds:
            issues.append(_issue("error" if strict else "warning", "E206", rel, o["line"],
                                 f"у «{o['text']}» нет подраздела «Гипотезы» или «Что должно быть правдой»"))
    if opts and not any("рекоменд" in _low(b["text"]) or "сравнени" in _low(b["text"])
                        for b in blocks if b["kind"] == "heading"):
        issues.append(_issue("warning", "W207", rel, 1, "нет раздела со сравнением вариантов или рекомендацией"))
    return issues


def check_promises(text: str, strict: bool) -> list:
    rel = "strategy/comms.md"
    issues = []
    blocks = parse_blocks(text)
    rows = [b for b in blocks if b["kind"] == "table" and any("обещани" in _low(t) for _, t in b["path"])]
    if not rows:
        issues.append(_issue("error" if strict else "warning", "E208", rel, 1,
                             "нет таблицы «Аудит обещаний»: каждое обещание → возможность продукта → основание"))
        return issues
    for r in rows[1:]:
        cells = [c.strip() for c in r["text"].strip("|").split("|")]
        if len(cells) < 3:
            continue
        if not cells[1] or not cells[2]:
            issues.append(_issue("error", "E209", rel, r["line"],
                                 f"обещание «{cells[0][:60]}» без привязки к продукту или основанию"))
        status = _low(cells[-1])
        if re.search(r"(^|\s)нет(\s|$|,)", status) or "не поддерж" in status:
            issues.append(_issue("error", "E210", rel, r["line"],
                                 f"обещание «{cells[0][:60]}» не подкреплено продуктом — уберите или измените"))
    return issues


def project_docs(pr) -> list:
    out = []
    for p in sorted(pr.dir.rglob("*.md")):
        rel = pr.rel(p)
        if rel in SKIP_FILES or any(rel.startswith(d) for d in SKIP_DIRS):
            continue
        out.append(rel)
    return out


def lint_project(pr, stage=None, files=None, strict=None) -> list:
    cmap = pr.claim_map()
    smap = pr.source_map()
    if stage:
        targets = [f for f in STAGE_DOCS.get(stage, []) if (pr.dir / f).exists() or f in REQUIRED]
        strict = True if strict is None else strict
    else:
        targets = files or project_docs(pr)
        strict = False if strict is None else strict
    issues = []
    for rel in targets:
        p = pr.dir / rel
        if not p.exists():
            if stage:
                issues.append(_issue("error", "E200", rel, 0, "документ не создан"))
            continue
        raw = p.read_text(encoding="utf-8", errors="replace")
        text = strip_comments(raw)
        issues += lint_file(rel, text, cmap, smap, strict)
        issues += check_required(rel, text, strict)
        if rel == "options.md":
            issues += check_options(text, cmap, strict, raw)
        if rel == "strategy/comms.md":
            issues += check_promises(text, strict)
        if rel == "summary.md" and strict and not refs_in(text):
            issues.append(_issue("warning", "W212", rel, 1, "в резюме нет ни одной ссылки на реестр"))
    return issues


def cmd_lint(args):
    pr = get_project(args.project)
    files = None
    if args.files:
        files = [pr.rel(Path(f).resolve()) if Path(f).is_absolute() else f for f in args.files]
    issues = lint_project(pr, stage=args.stage, files=files, strict=args.strict or None)
    if args.only_integrity:
        issues = [i for i in issues if i["code"].startswith(("E1", "W1"))]
    if args.json:
        print(json.dumps(issues, ensure_ascii=False, indent=1))
    else:
        if not issues:
            print("Проверка пройдена: ошибок и предупреждений нет.")
        cur = None
        for i in sorted(issues, key=lambda x: (x["file"], x["line"])):
            if i["file"] != cur:
                cur = i["file"]
                print(f"\n{cur}")
            mark = "✗" if i["severity"] == "error" else "!"
            print(f"  {mark} стр. {i['line']}: [{i['code']}] {i['message']}")
        e = sum(1 for i in issues if i["severity"] == "error")
        w = len(issues) - e
        if issues:
            print(f"\nИтого: ошибок {e}, предупреждений {w}.")
    errors = any(i["severity"] == "error" for i in issues)
    return 1 if errors else 0
