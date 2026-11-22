"""Финансовая модель проекта: допущения и результаты расчёта как записи реестра.

Агент пишет расчёт в analysis/model.py (шаблон: ./strat model init). Скрипт сохраняет analysis/model.json, а
команда ./strat model sync превращает его в записи реестра:
  допущение  → запись типа assumption (значение, диапазон, почему так, как проверить);
  результат  → запись типа estimate с основаниями: допущения и факты из материалов, на которых он держится.
Записи сохраняют свои номера между пересчётами: в документах ссылки не ломаются, а изменившиеся числа ловит
проверка W110, и ./strat impact показывает, какие разделы пересчитать.
"""
from __future__ import annotations

import json
import re
import shutil

from .core import (ROOT, FileLock, StratError, append_jsonl, get_project, journal, next_id, now_iso, read_jsonl,
                   write_jsonl)
from .sources import register, snapshot_text

TEMPLATE = ROOT / "templates" / "model.py"


def fmt(v) -> str:
    """Число по-русски: 12 000, 0,045, 1,5 млн не сокращаем — единицы пишет сам расчёт."""
    if isinstance(v, bool) or v is None:
        return str(v)
    if isinstance(v, int) or (isinstance(v, float) and v.is_integer() and abs(v) >= 1000):
        s = f"{int(round(v)):,}".replace(",", " ")
        return s
    if isinstance(v, float):
        s = f"{v:.4f}".rstrip("0").rstrip(".")
        if "." in s:
            whole, frac = s.split(".")
            whole = f"{int(whole):,}".replace(",", " ") if whole not in ("-0",) else whole
            return whole + "," + frac
        return f"{int(s):,}".replace(",", " ")
    return str(v)


def _unit(u: str) -> str:
    return f" {u}" if u else ""


def _line_assumption(a: dict) -> str:
    rng = ""
    if a.get("low") is not None and a.get("high") is not None:
        rng = f" (диапазон {fmt(a['low'])}–{fmt(a['high'])})"
    parts = [_end(f"A:{a['key']}. {a['text']}: {fmt(a['value'])}{_unit(a.get('unit', ''))}{rng}")]
    if a.get("why"):
        parts.append(f"Почему так: {a['why'].rstrip('.')}.")
    if a.get("check"):
        parts.append(f"Как проверить: {a['check'].rstrip('.')}.")
    return " ".join(parts)


def _end(s: str) -> str:
    return s if s.endswith(".") else s + "."


def _line_result(r: dict) -> str:
    scen = f" [{r['scenario']}]" if r.get("scenario") else ""
    return _end(f"R:{r['key']}{scen}. {r['text']}: {fmt(r['value'])}{_unit(r.get('unit', ''))}")


def _line_fact(f: dict) -> str:
    return f"F:{f['key']}. {f.get('text', f['key'])}: {fmt(f['value'])}{_unit(f.get('unit', ''))} ({f['claim']})"


def render(model: dict) -> str:
    out = ["# Финансовая модель", "",
           "Расчёт агента: analysis/model.py → analysis/model.json → ./strat model sync. "
           "Все числа ниже — допущения (A) и результаты расчёта (R); факты (F) взяты из материалов.", ""]
    if model.get("facts"):
        out += ["## Факты из материалов", ""] + [f"- {_line_fact(f)}" for f in model["facts"]] + [""]
    out += ["## Допущения", ""] + [f"- {_line_assumption(a)}" for a in model.get("assumptions", [])] + [""]
    out += ["## Результаты", ""] + [f"- {_line_result(r)}" for r in model.get("results", [])] + [""]
    for t in model.get("tables", []):
        out += [f"## {t.get('title', 'Таблица')}", "", t.get("markdown", "").strip(), ""]
    return "\n".join(out).rstrip() + "\n"


def _validate(model: dict, cmap: dict):
    keys = set()
    for sect in ("facts", "assumptions", "results"):
        for item in model.get(sect, []):
            k = item.get("key")
            if not k or not re.fullmatch(r"[a-z0-9_]+", k):
                raise StratError(f"{sect}: ключ «{k}» — только латиница, цифры и _.")
            if k in keys:
                raise StratError(f"Ключ {k} встречается дважды.")
            keys.add(k)
            if "value" not in item:
                raise StratError(f"{k}: нет значения (value).")
    for f in model.get("facts", []):
        cid = (f.get("claim") or "").upper()
        if cid not in cmap:
            raise StratError(f"Факт {f['key']}: запись {cid or '(нет)'} не найдена. Сначала занесите число из "
                             "материалов командой ./strat claim add с цитатой, затем укажите её в FACTS.")
    for r in model.get("results", []):
        for b in r.get("basis", []):
            if b not in keys:
                raise StratError(f"Результат {r['key']} опирается на неизвестный ключ {b}.")
        if not r.get("basis"):
            raise StratError(f"Результат {r['key']}: укажите basis — допущения и факты, из которых он посчитан.")


def _model_source(pr, text: str) -> str:
    """Источник «модель» с дословным снимком; новая версия — только если текст изменился."""
    rows = read_jsonl(pr.sources_path)
    mine = [s for s in rows if s.get("kind") == "model"]
    if mine:
        last = mine[-1]
        if snapshot_text(pr, last).strip() == text.strip():
            return last["id"]
    version = len(mine) + 1
    rec = register(pr, {"title": f"Финансовая модель агента, версия {version}", "kind": "model",
                        "path": "analysis/model.md", "verbatim": True, "reliability": "расчёт",
                        "publisher": "расчёт агента"}, text)
    return rec["id"]


def cmd_init(args):
    pr = get_project(args.project)
    dst = pr.dir / "analysis" / "model.py"
    if dst.exists() and not args.force:
        print(f"{pr.rel(dst)} уже есть. Перезаписать: --force.")
        return 3
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(TEMPLATE, dst)
    journal(pr, "model_init", {"path": pr.rel(dst)})
    print(f"Шаблон расчёта: {pr.rel(dst)}. Заполните FACTS, ASSUMPTIONS и compute(), запустите "
          f"python3 {pr.rel(dst)}, затем ./strat model sync.")
    return 0


def cmd_sync(args):
    pr = get_project(args.project)
    path = pr.dir / (args.file or "analysis/model.json")
    if not path.exists():
        raise StratError(f"Нет {pr.rel(path)}. Запустите расчёт: python3 analysis/model.py.")
    try:
        model = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise StratError(f"{pr.rel(path)}: не JSON ({e}).")
    cmap = pr.claim_map()
    _validate(model, cmap)
    text = render(model)
    (pr.dir / "analysis" / "model.md").write_text(text, encoding="utf-8")
    sid = _model_source(pr, text)

    ids_path = pr.dir / "analysis" / "model.ids.json"
    ids = json.loads(ids_path.read_text(encoding="utf-8")) if ids_path.exists() else {}
    key2claim = {f["key"]: f["claim"].upper() for f in model.get("facts", [])}
    created, changed, retracted = [], [], []

    from .claims import _verify_against  # noqa: WPS433 — общий механизм сверки цитат

    with FileLock(pr.claims_path):
        rows = read_jsonl(pr.claims_path)
        by_id = {c["id"]: c for c in rows}
        smap = pr.source_map()

        def upsert(tag, ctype, text_, quote, basis, method, extra):
            cid = ids.get(tag)
            c = by_id.get(cid) if cid else None
            if c is None:
                cid = next_id(rows, "C")
                c = {"id": cid, "text": text_, "type": ctype, "sources": [sid], "quote": quote, "loc": "",
                     "basis": basis, "method": method, "confidence": extra.get("confidence", "medium"),
                     "as_of": "", "expires": "", "tags": ["model", tag], "status": "active",
                     "created_by": "model", "created_at": now_iso()}
                res = _verify_against(pr, c, smap)
                c["verified"] = res["status"] in ("exact", "close")
                rows.append(c)
                by_id[cid] = c
                ids[tag] = cid
                created.append(cid)
                return cid
            old = (c.get("text"), c.get("quote"), c.get("basis"))
            if old != (text_, quote, basis) or c.get("status") != "active":
                c.setdefault("history", []).append({"ts": now_iso(), "action": "model_sync",
                                                    "old": {"text": c.get("text"), "quote": c.get("quote")}})
                c.update({"text": text_, "quote": quote, "sources": [sid], "basis": basis, "status": "active"})
                res = _verify_against(pr, c, smap)
                c["verified"] = res["status"] in ("exact", "close")
                if cid not in created and cid not in changed:
                    changed.append(cid)
            return cid

        for a in model.get("assumptions", []):
            line = _line_assumption(a)
            rng = f", диапазон {fmt(a['low'])}–{fmt(a['high'])}" if a.get("low") is not None else ""
            key2claim[a["key"]] = upsert(f"A:{a['key']}", "assumption",
                                         f"{a['text']}: {fmt(a['value'])}{_unit(a.get('unit', ''))}{rng} (допущение)",
                                         line, [], "допущение модели", a)
        # два прохода: результат может опираться на другой результат, который идёт ниже по списку
        for _pass in (1, 2):
            for r in model.get("results", []):
                line = _line_result(r)
                basis = sorted({key2claim[b] for b in r.get("basis", []) if b in key2claim})
                scen = f" ({r['scenario']})" if r.get("scenario") else ""
                key2claim[r["key"]] = upsert(f"R:{r['key']}", "estimate",
                                             f"{r['text']}{scen}: {fmt(r['value'])}{_unit(r.get('unit', ''))}",
                                             line, basis, r.get("method") or "расчёт analysis/model.py", r)

        live = {f"A:{a['key']}" for a in model.get("assumptions", [])} | \
               {f"R:{r['key']}" for r in model.get("results", [])}
        for tag, cid in list(ids.items()):
            c = by_id.get(cid)
            if tag not in live and c and c.get("status") == "active":
                c["status"] = "retracted"
                c["status_note"] = "убрано из модели"
                retracted.append(cid)
        write_jsonl(pr.claims_path, rows)
    ids_path.write_text(json.dumps(ids, ensure_ascii=False, indent=1), encoding="utf-8")
    journal(pr, "model_sync", {"source": sid, "created": created, "changed": changed, "retracted": retracted})

    print(f"Модель занесена: источник {sid}, analysis/model.md.")
    print(f"  новых записей: {len(created)}, изменилось: {len(changed)}, убрано: {len(retracted)}")
    if changed or retracted:
        ids_s = ",".join(changed + retracted)
        print(f"  Числа изменились — посмотрите, какие разделы пересчитать: ./strat impact {ids_s}")
    if args.verbose:
        for tag, cid in sorted(ids.items(), key=lambda x: x[1]):
            print(f"  {cid}  {tag}")
    return 0


def cmd_show(args):
    pr = get_project(args.project)
    ids_path = pr.dir / "analysis" / "model.ids.json"
    if not ids_path.exists():
        print("Модель ещё не занесена: ./strat model sync.")
        return 0
    ids = json.loads(ids_path.read_text(encoding="utf-8"))
    cmap = pr.claim_map()
    for tag, cid in sorted(ids.items()):
        c = cmap.get(cid, {})
        mark = "" if c.get("status") == "active" else f" [{c.get('status')}]"
        print(f"{cid}  {tag:28} {c.get('text', '')}{mark}")
    return 0
