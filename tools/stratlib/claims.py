"""Реестр утверждений: что известно, откуда, какого типа и в каком состоянии."""
from __future__ import annotations

import json
import os

from . import textio
from .core import (FileLock, StratError, append_jsonl, get_project, journal, next_id, now_iso, parse_date,
                   read_jsonl, short, split_ids, split_list, today, write_jsonl)
from .sources import snapshot_text

TYPES = {
    "fact": "факт",
    "observation": "наблюдение",
    "estimate": "оценка",
    "forecast": "прогноз",
    "hypothesis": "гипотеза",
    "assumption": "допущение",
    "inference": "вывод",
    "synthetic": "синтетика",
}
ALIASES = {v: k for k, v in TYPES.items()}
ALIASES.update({"факт": "fact", "наблюдение": "observation", "оценка": "estimate", "расчет": "estimate",
                "расчёт": "estimate", "прогноз": "forecast", "гипотеза": "hypothesis", "допущение": "assumption",
                "вывод": "inference", "синтетика": "synthetic", "synthetic-response": "synthetic"})
EVIDENCE = {"fact", "observation", "estimate"}
STATUSES = {"active": "действует", "disputed": "оспорено", "retracted": "отозвано", "superseded": "заменено"}
NON_TEXT_KINDS = {"image", "video", "audio"}


def norm_type(t: str) -> str:
    t = (t or "").strip().lower()
    t = ALIASES.get(t, t)
    if t not in TYPES:
        raise StratError(f"Неизвестный тип «{t}». Допустимые: " + ", ".join(f"{k} ({v})" for k, v in TYPES.items()))
    return t


def _verify_against(pr, claim: dict, smap: dict) -> dict:
    """Сверяет цитату с текстами источников. Возвращает лучший результат."""
    quote = claim.get("quote") or ""
    best = {"status": "no_quote" if not quote else "no_text", "score": 0.0, "snippet": "", "source": None}
    if not quote:
        return best
    order = {"exact": 3, "close": 2, "not_found": 1, "no_text": 0}
    for sid in claim.get("sources", []):
        src = smap.get(sid)
        if not src:
            continue
        txt = snapshot_text(pr, src) if src.get("verbatim", True) else ""
        res = textio.verify_quote(quote, txt) if txt else {"status": "no_text", "score": 0.0, "snippet": ""}
        res["source"] = sid
        if order[res["status"]] > order.get(best["status"], -1):
            best = res
    return best


def _verified_flag(res: dict):
    return {"exact": True, "close": True, "not_found": False}.get(res["status"])


def cmd_add(args):
    pr = get_project(args.project)
    ctype = norm_type(args.type)
    text = (args.text or "").strip()
    if len(text) < 5:
        raise StratError("Сформулируйте утверждение полностью (--text).")
    smap = pr.source_map()
    sources = split_ids(args.source)
    for s in sources:
        if s not in smap:
            raise StratError(f"Источник {s} не найден. Сначала зарегистрируйте его: ./strat source add|fetch|note.")
    cmap = pr.claim_map()
    basis = split_ids(args.basis)
    for b in basis:
        if b not in cmap:
            raise StratError(f"Основание {b} не найдено в реестре.")
        if cmap[b].get("status") in ("retracted", "superseded"):
            raise StratError(f"Основание {b} отозвано или заменено — на него нельзя опираться.")
    quote = (args.quote or "").strip()
    kinds = {smap[s].get("kind") for s in sources}
    warnings = []

    if ctype in ("fact", "observation"):
        if not sources:
            raise StratError(f"Тип «{TYPES[ctype]}» требует источник (--source S-...).")
        if not quote and not kinds & NON_TEXT_KINDS and not args.allow_unverified:
            raise StratError("Для факта нужна дословная цитата из источника (--quote). Если сведения из таблицы, "
                             "процитируйте значение и укажите ячейку в --loc.")
        if kinds & NON_TEXT_KINDS and not args.loc:
            raise StratError("Для наблюдения по изображению, видео или аудио укажите место: --loc \"экран 2, кнопка "
                             "«Подключить»\" или --loc \"00:03:15\".")
    if ctype == "estimate":
        if not sources and not basis:
            raise StratError("Оценка опирается на источник или расчёт: укажите --source и/или --basis.")
        if not args.method:
            warnings.append("у оценки не указан метод (--method): как получено число")
    if ctype == "inference" and not basis:
        raise StratError("Вывод должен опираться на другие утверждения: --basis C-...,C-...")
    if ctype == "synthetic":
        if not sources or not all(smap[s].get("kind") == "synthetic" for s in sources):
            raise StratError("Синтетический ответ ссылается на расшифровку модельной беседы "
                             "(источник с --kind synthetic).")
    if ctype == "forecast" and not sources and not basis:
        warnings.append("у прогноза нет источника или основания — это ваш прогноз, укажите это в тексте")
    if any(smap[s].get("kind") == "synthetic" for s in sources) and ctype != "synthetic":
        raise StratError("Из синтетической беседы можно заносить только тип synthetic: она не доказывает факты.")

    claim = {
        "text": text,
        "type": ctype,
        "sources": sources,
        "quote": quote,
        "loc": args.loc or "",
        "basis": basis,
        "method": args.method or "",
        "confidence": (args.confidence or "medium").lower(),
        "as_of": args.as_of or "",
        "expires": args.expires or "",
        "tags": split_list(args.tags),
        "status": "active",
        "created_by": os.environ.get("STRAT_ACTOR", args.by or "agent"),
        "created_at": now_iso(),
    }
    res = _verify_against(pr, claim, smap)
    claim["verified"] = _verified_flag(res) if quote else None
    claim["verify_score"] = res.get("score")
    if quote and res["status"] == "not_found" and ctype in ("fact", "observation", "estimate"):
        if not args.allow_unverified:
            hint = f" Ближайший фрагмент в {res.get('source')}: «{short(res.get('snippet'), 220)}»." \
                if res.get("snippet") else ""
            raise StratError("Цитата не найдена в снимке источника." + hint +
                             " Скопируйте цитату дословно или, если источник без дословного текста, добавьте "
                             "--allow-unverified --note \"почему\".", code=2)
        warnings.append("цитата не найдена в снимке — запись помечена как непроверенная")
    if quote and res["status"] == "no_text":
        warnings.append("у источника нет дословного снимка — цитату проверить нельзя")
        claim["verified"] = None
    if args.note:
        claim["note"] = args.note

    with FileLock(pr.claims_path):
        rows = read_jsonl(pr.claims_path)
        key = textio.norm(text)
        for r in rows:
            if r.get("status") == "active" and textio.norm(r.get("text", "")) == key and \
                    sorted(r.get("sources", [])) == sorted(sources):
                print(f"Такое утверждение уже есть: {r['id']}. Новую запись не создаю.")
                return 0
        cid = next_id(rows, "C")
        claim = {"id": cid, **claim}
        append_jsonl(pr.claims_path, claim)
    journal(pr, "claim", {"id": cid, "type": ctype, "sources": sources, "basis": basis,
                          "verified": claim["verified"], "text": short(text, 160)})
    status = {True: "цитата найдена", False: "цитата НЕ найдена", None: "без проверки цитаты"}[claim["verified"]]
    print(f"{cid} добавлено: {TYPES[ctype]}; {', '.join(sources) or 'без источника'}; {status}.")
    for w in warnings:
        print(f"  предупреждение: {w}")
    return 0


def _filter(rows, args):
    out = []
    types = {norm_type(t) for t in split_list(getattr(args, "type", None))} if getattr(args, "type", None) else None
    for c in rows:
        if types and c.get("type") not in types:
            continue
        if getattr(args, "status", None) and c.get("status") != args.status:
            continue
        if getattr(args, "source", None) and args.source.upper() not in c.get("sources", []):
            continue
        if getattr(args, "tag", None) and args.tag not in c.get("tags", []):
            continue
        if getattr(args, "q", None):
            q = textio.norm(args.q)
            if q not in textio.norm(c.get("text", "") + " " + c.get("quote", "") + " " + " ".join(c.get("tags", []))):
                continue
        if getattr(args, "unverified", False) and c.get("verified") is not False:
            continue
        out.append(c)
    return out


def cmd_list(args):
    pr = get_project(args.project)
    rows = _filter(pr.claims(), args)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0
    if not rows:
        print("Записей не найдено.")
        return 0
    mark = {True: "✓", False: "✗", None: "·"}
    for c in rows:
        st = "" if c.get("status") == "active" else f" [{STATUSES.get(c.get('status'), c.get('status'))}]"
        src = ",".join(c.get("sources", [])) or ("осн.: " + ",".join(c.get("basis", [])) if c.get("basis") else "—")
        print(f"{c['id']} {mark.get(c.get('verified'), '·')} {TYPES.get(c['type'], c['type']):11} {src:14} "
              f"{short(c.get('text'), 110)}{st}")
    print(f"\nВсего: {len(rows)}. ✓ цитата найдена, ✗ не найдена, · без проверки.")
    return 0


def cmd_show(args):
    pr = get_project(args.project)
    c = pr.claim_map().get(args.id.upper())
    if not c:
        raise StratError(f"{args.id} не найдено.")
    print(json.dumps(c, ensure_ascii=False, indent=2))
    from .impact import build_index, impact
    idx = build_index(pr)
    imp = impact(pr, [c["id"]], idx)
    if imp["refs"]:
        print("\nГде используется:")
        for r in imp["refs"]:
            print(f"  {r['file']}:{r['line']}  {r['section']}")
    return 0


def cmd_verify(args):
    pr = get_project(args.project)
    smap = pr.source_map()
    ids = set(split_ids(args.ids)) if args.ids else None
    stats = {"exact": 0, "close": 0, "not_found": 0, "no_text": 0, "no_quote": 0}
    bad = []
    with FileLock(pr.claims_path):
        rows = read_jsonl(pr.claims_path)
        for c in rows:
            if ids and c["id"] not in ids:
                continue
            if c.get("status") in ("retracted", "superseded"):
                continue
            res = _verify_against(pr, c, smap)
            stats[res["status"]] = stats.get(res["status"], 0) + 1
            c["verified"] = _verified_flag(res) if c.get("quote") else None
            c["verify_score"] = res.get("score")
            if res["status"] == "not_found":
                bad.append((c, res))
        write_jsonl(pr.claims_path, rows)
    total = sum(stats.values())
    print(f"Проверено записей: {total}. Дословно: {stats['exact']}, близко: {stats['close']}, "
          f"не найдено: {stats['not_found']}, нет снимка: {stats['no_text']}, без цитаты: {stats['no_quote']}.")
    for c, res in bad[:30]:
        print(f"  ✗ {c['id']} «{short(c.get('quote'), 90)}» — ближайшее: «{short(res.get('snippet'), 120)}»")
    journal(pr, "verify", {"stats": stats})
    return 1 if bad and args.strict else 0


def _update(pr, cid, fn, action):
    with FileLock(pr.claims_path):
        rows = read_jsonl(pr.claims_path)
        hit = None
        for c in rows:
            if c["id"] == cid:
                hit = c
                fn(c)
        if not hit:
            raise StratError(f"{cid} не найдено.")
        write_jsonl(pr.claims_path, rows)
    journal(pr, "claim_" + action, {"id": cid, "status": hit.get("status"), "note": hit.get("status_note", "")})
    return hit


def cmd_status(args):
    """dispute | retract | supersede | restore"""
    pr = get_project(args.project)
    cid = args.id.upper()
    action = args.action
    by = split_ids(getattr(args, "by", None))
    cmap = pr.claim_map()
    for b in by:
        if b not in cmap and b not in pr.source_map():
            raise StratError(f"{b} не найдено в реестрах.")
    if action == "supersede" and not by:
        raise StratError("Укажите, чем заменяется запись: --by C-...")
    note = args.note or ""

    def fn(c):
        hist = c.setdefault("history", [])
        hist.append({"ts": now_iso(), "action": action, "from": c.get("status"), "note": note, "by": by})
        c["status"] = {"dispute": "disputed", "retract": "retracted", "supersede": "superseded",
                       "restore": "active"}[action]
        c["status_note"] = note
        if action == "supersede":
            c["superseded_by"] = by[0]
        if action == "dispute":
            c["disputed_by"] = by

    c = _update(pr, cid, fn, action)
    print(f"{cid}: {STATUSES[c['status']]}. Проверьте, что это затрагивает: ./strat impact {cid}")
    return 0


def cmd_update(args):
    """Исправление записи (текст, цитата, место, метки) с сохранением истории."""
    pr = get_project(args.project)
    cid = args.id.upper()
    smap = pr.source_map()
    changes = {k: v for k, v in {
        "text": args.text, "quote": args.quote, "loc": args.loc, "confidence": args.confidence,
        "as_of": args.as_of, "expires": args.expires, "method": args.method,
    }.items() if v is not None}
    if args.tags is not None:
        changes["tags"] = split_list(args.tags)
    if args.add_source:
        new = split_ids(args.add_source)
        for s in new:
            if s not in smap:
                raise StratError(f"Источник {s} не найден.")
        changes["_add_sources"] = new
    if not changes:
        raise StratError("Нечего менять: укажите --text, --quote, --loc, --tags, --add-source ...")

    result = {}

    def fn(c):
        old = {k: c.get(k) for k in changes if not k.startswith("_")}
        for k, v in changes.items():
            if k == "_add_sources":
                c["sources"] = sorted(set(c.get("sources", []) + v))
            else:
                c[k] = v
        c.setdefault("history", []).append({"ts": now_iso(), "action": "update", "old": old,
                                            "note": args.note or ""})
        res = _verify_against(pr, c, smap)
        c["verified"] = _verified_flag(res) if c.get("quote") else None
        c["verify_score"] = res.get("score")
        result.update(res)

    c = _update(pr, cid, fn, "update")
    status = {True: "цитата найдена", False: "цитата НЕ найдена", None: "без проверки цитаты"}[c.get("verified")]
    print(f"{cid} исправлено ({status}). Если менялся смысл, проверьте: ./strat impact {cid}")
    return 0


def stale_claims(rows):
    out = []
    t = today()
    for c in rows:
        exp = parse_date(c.get("expires"))
        if exp and exp < t and c.get("status") == "active":
            out.append(c)
    return out
