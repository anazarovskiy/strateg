"""Решения и обязательства: основания, ответственные, показатели, условия и сроки пересмотра."""
from __future__ import annotations

import datetime as dt
import json

from .core import (TEMPLATES, FileLock, StratError, append_jsonl, get_project, journal, next_id, now_iso,
                   parse_date, read_jsonl, short, split_ids, split_list, today, write_jsonl, write_text)


def cmd_add(args):
    pr = get_project(args.project)
    cmap = pr.claim_map()
    basis = split_ids(args.basis)
    if not basis:
        raise StratError("У решения должны быть основания: --basis C-...,C-...")
    for b in basis:
        if b not in cmap:
            raise StratError(f"Основание {b} не найдено.")
        if cmap[b].get("status") in ("retracted", "superseded"):
            raise StratError(f"Основание {b} отозвано или заменено.")
    if not parse_date(args.review_by):
        raise StratError("Укажите дату пересмотра: --review-by 2027-02-01")
    with FileLock(pr.decisions_path):
        rows = read_jsonl(pr.decisions_path)
        did = next_id(rows, "D")
        rec = {
            "id": did, "title": args.title, "chosen": args.chosen or "", "rejected": split_list(args.rejected),
            "basis": basis, "owner": args.owner or "", "metrics": split_list(args.metric),
            "review_by": str(parse_date(args.review_by)), "review_if": split_list(args.review_if),
            "status": "active", "created": now_iso(), "note": args.note or "",
        }
        append_jsonl(pr.decisions_path, rec)
    tpl = (TEMPLATES / "decision.md")
    body = tpl.read_text(encoding="utf-8") if tpl.exists() else "# {{id}}. {{title}}\n"
    fill = {
        "{{id}}": did, "{{title}}": rec["title"], "{{chosen}}": rec["chosen"] or "—",
        "{{date}}": rec["created"][:10], "{{owner}}": rec["owner"] or "—",
        "{{basis}}": "\n".join(f"- {cmap[b]['text']} [{b}]" for b in basis),
        "{{rejected}}": "\n".join(f"- {r}" for r in rec["rejected"]) or "- —",
        "{{metrics}}": "\n".join(f"- {m}" for m in rec["metrics"]) or "- —",
        "{{review_by}}": rec["review_by"], "{{review_if}}": "\n".join(f"- {r}" for r in rec["review_if"]) or "- —",
    }
    for k, v in fill.items():
        body = body.replace(k, v)
    write_text(pr.dir / "decisions" / f"{did}.md", body)
    journal(pr, "decision", {"id": did, "title": rec["title"], "basis": basis})
    print(f"{did} записано: {rec['title']}. Документ: decisions/{did}.md. Пересмотр не позже {rec['review_by']}.")
    return 0


def cmd_update(args):
    pr = get_project(args.project)
    with FileLock(pr.decisions_path):
        rows = read_jsonl(pr.decisions_path)
        hit = None
        for d in rows:
            if d["id"] == args.id.upper():
                hit = d
                d.setdefault("history", []).append({"ts": now_iso(), "status": d.get("status"),
                                                    "review_by": d.get("review_by"), "note": args.note or ""})
                if args.status:
                    d["status"] = args.status
                if args.review_by:
                    d["review_by"] = str(parse_date(args.review_by))
                if args.add_basis:
                    d["basis"] = sorted(set(d.get("basis", []) + split_ids(args.add_basis)))
        if not hit:
            raise StratError(f"Решение {args.id} не найдено.")
        write_jsonl(pr.decisions_path, rows)
    journal(pr, "decision_update", {"id": hit["id"], "status": hit.get("status"), "note": args.note or ""})
    print(f"{hit['id']} обновлено: статус {hit['status']}, пересмотр {hit.get('review_by')}.")
    return 0


def cmd_list(args):
    pr = get_project(args.project)
    rows = pr.decisions()
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0
    if not rows:
        print("Решений пока нет.")
        return 0
    for d in rows:
        print(f"{d['id']} [{d.get('status')}] {d['title']} — выбрано: {short(d.get('chosen'), 60)}; "
              f"ответственный: {d.get('owner') or '—'}; пересмотр: {d.get('review_by')}; основания: "
              f"{', '.join(d.get('basis', []))}")
    return 0


def commit_add(args):
    pr = get_project(args.project)
    if args.decision and args.decision.upper() not in {d["id"] for d in pr.decisions()}:
        raise StratError(f"Решение {args.decision} не найдено.")
    due = parse_date(args.due)
    if not due:
        raise StratError("Укажите срок: --due 2026-12-15")
    with FileLock(pr.commitments_path):
        rows = read_jsonl(pr.commitments_path)
        kid = next_id(rows, "K")
        rec = {"id": kid, "decision": (args.decision or "").upper(), "who": args.who, "what": args.what,
               "due": str(due), "status": "open", "created": now_iso(), "note": args.note or ""}
        append_jsonl(pr.commitments_path, rec)
    journal(pr, "commitment", {"id": kid, "who": args.who, "what": short(args.what, 120), "due": str(due)})
    print(f"{kid}: {args.who} — {args.what} (до {due}).")
    return 0


def commit_set(args, status):
    pr = get_project(args.project)
    with FileLock(pr.commitments_path):
        rows = read_jsonl(pr.commitments_path)
        hit = None
        for k in rows:
            if k["id"] == args.id.upper():
                hit = k
                k["status"] = status
                k["closed_at"] = now_iso()
                if args.note:
                    k["note"] = args.note
        if not hit:
            raise StratError(f"Обязательство {args.id} не найдено.")
        write_jsonl(pr.commitments_path, rows)
    journal(pr, "commitment_" + status, {"id": hit["id"], "note": args.note or ""})
    print(f"{hit['id']}: {status}.")
    return 0


def commit_list(args):
    pr = get_project(args.project)
    rows = pr.commitments()
    if not rows:
        print("Обязательств нет.")
        return 0
    t = today()
    for k in rows:
        due = parse_date(k.get("due"))
        late = " ПРОСРОЧЕНО" if k.get("status") == "open" and due and due < t else ""
        print(f"{k['id']} [{k.get('status')}] {k.get('who')}: {k.get('what')} — до {k.get('due')}"
              f"{late} ({k.get('decision') or 'без решения'})")
    return 0


def check(pr) -> dict:
    """Что требует внимания: пересмотры, изменившиеся основания, сроки обязательств, открытые вопросы."""
    t = today()
    soon = t + dt.timedelta(days=14)
    cmap = pr.claim_map()
    now_, changed, upcoming = [], [], []
    for d in pr.decisions():
        if d.get("status") != "active":
            continue
        rb = parse_date(d.get("review_by"))
        if rb and rb <= t:
            now_.append(f"{d['id']} «{short(d['title'], 60)}»: наступил срок пересмотра ({rb})")
        elif rb and rb <= soon:
            upcoming.append(f"{d['id']}: пересмотр {rb}")
        for b in d.get("basis", []):
            c = cmap.get(b)
            if not c:
                changed.append(f"{d['id']}: основание {b} не найдено в реестре")
                continue
            if c.get("status") in ("disputed", "retracted", "superseded"):
                st = {"disputed": "оспорено", "retracted": "отозвано", "superseded": "заменено"}[c["status"]]
                changed.append(f"{d['id']}: основание {b} {st} — {short(c.get('status_note'), 80)}")
            exp = parse_date(c.get("expires"))
            if exp and exp < t:
                changed.append(f"{d['id']}: основание {b} устарело (срок {c['expires']})")
    for k in pr.commitments():
        if k.get("status") != "open":
            continue
        due = parse_date(k.get("due"))
        if due and due < t:
            now_.append(f"{k['id']} просрочено ({due}): {k.get('who')} — {short(k.get('what'), 70)}")
        elif due and due <= t + dt.timedelta(days=7):
            upcoming.append(f"{k['id']} срок {due}: {k.get('who')} — {short(k.get('what'), 70)}")
    for h in pr.state().get("human", []):
        if not h.get("answer"):
            now_.append(f"{h['id']} ждёт ответа: {short(h['question'], 90)}")
    return {"now": now_, "changed": changed, "soon": upcoming}


def cmd_check(args):
    pr = get_project(args.project)
    res = check(pr)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0
    if not any(res.values()):
        print("Срочных вопросов нет: сроки пересмотра не наступили, основания решений действуют.")
        return 0
    for key, title in (("now", "Решить сейчас"), ("changed", "Основания изменились — пересмотреть"),
                       ("soon", "Скоро")):
        if res[key]:
            print(title + ":")
            for x in res[key]:
                print(f"  {x}")
    return 0
