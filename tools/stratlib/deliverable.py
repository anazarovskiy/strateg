"""Итоговый документ в структуре заказчика.

Если бриф задаёт, из каких частей сдать работу («один документ из пяти частей: …»), агент записывает эти
части командой ./strat deliverable set. Появляется deliverable.md с заголовками частей; проверка требует,
чтобы каждая часть была заполнена, а отчёт ставит этот документ первым, рабочие материалы этапов — после него.
"""
from __future__ import annotations

from .core import StratError, get_project, journal, split_ids

LEAD_TITLE = "Коротко"


def _skeleton(title: str, parts: list, lead: str) -> str:
    out = [f"# {title}", ""]
    if lead:
        out += [f"## {LEAD_TITLE}", "", f"<!-- Требование заказчика: {lead}. Полстраницы, со ссылками на записи. -->",
                ""]
    for i, p in enumerate(parts, 1):
        out += [f"## {i}. {p}", "",
                "<!-- Пишите для руководителя. Расчёт ставьте рядом с решением, которое он обосновывает; "
                "числа — со ссылками на записи реестра (факты брифа, допущения A:, оценки R:). "
                "Подробности оставьте в рабочих документах этапов. -->", ""]
    return "\n".join(out)


def parts_of(pr) -> list:
    d = pr.state().get("deliverable") or {}
    return d.get("parts", [])


def headings_expected(pr) -> list:
    d = pr.state().get("deliverable") or {}
    out = [LEAD_TITLE] if d.get("lead") else []
    return out + [f"{i}. {p}" for i, p in enumerate(d.get("parts", []), 1)]


def cmd_set(args):
    pr = get_project(args.project)
    parts = [p.strip() for p in args.parts.split("|") if p.strip()]
    if len(parts) < 2:
        raise StratError("Укажите части через «|»: --parts \"Клиент и его проблема|Предложение|…\".")
    smap = pr.source_map()
    src = split_ids(args.source) if args.source else []
    for s in src:
        if s not in smap:
            raise StratError(f"Источник {s} не найден.")
    st = pr.state()
    st["deliverable"] = {"parts": parts, "lead": (args.lead or "").strip(), "sources": src}
    pr.save_state(st)
    path = pr.dir / "deliverable.md"
    if path.exists():
        print("deliverable.md уже есть: структура в состоянии обновлена, документ не трогаю. "
              "Сверьте заголовки с ./strat deliverable show.")
    else:
        path.write_text(_skeleton(st.get("title", pr.slug), parts, st["deliverable"]["lead"]), encoding="utf-8")
        print(f"Создан deliverable.md: {len(parts)} частей" + (" и вводный раздел «Коротко»." if args.lead else "."))
    journal(pr, "deliverable_set", {"parts": parts, "lead": bool(args.lead), "sources": src})
    return 0


def cmd_show(args):
    pr = get_project(args.project)
    exp = headings_expected(pr)
    if not exp:
        print("Структура заказчика не задана. Если бриф требует определённых частей: ./strat deliverable set.")
        return 0
    print("Части итогового документа (deliverable.md):")
    for h in exp:
        print("  ## " + h)
    return 0


def check(pr, text: str, strict: bool) -> list:
    """Каждая часть есть и не пустая. Возвращает список проблем в формате lint."""
    from .lint import _issue
    issues = []
    sev = "error" if strict else "warning"
    import re
    blocks = {}
    cur = None
    for line in text.splitlines():
        m = re.match(r"^##\s+(.*)$", line)
        if m:
            cur = m.group(1).strip()
            blocks[cur] = []
        elif cur is not None:
            blocks[cur].append(line)
    for h in headings_expected(pr):
        hit = next((k for k in blocks if k.lower().startswith(h.lower()[:40])), None)
        if hit is None:
            issues.append(_issue(sev, "E213", "deliverable.md", 0, f"нет части «{h}», которую требует заказчик"))
            continue
        body = "\n".join(blocks[hit]).strip()
        if len(re.sub(r"[#\s]", "", body)) < 80:
            issues.append(_issue(sev, "E213", "deliverable.md", 0, f"часть «{h}» почти пустая"))
    return issues
