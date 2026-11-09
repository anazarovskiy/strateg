"""Связи между записями и документами: анализ влияния изменений и поиск по базе знаний."""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict

from . import textio
from .core import PROJECTS, StratError, get_project, list_projects, short, split_ids
from .lint import parse_blocks, path_str, project_docs, refs_in, strip_comments
from .sources import snapshot_text


def build_index(pr) -> dict:
    refs = defaultdict(list)
    for rel in project_docs(pr):
        text = (pr.dir / rel).read_text(encoding="utf-8", errors="replace")
        for b in parse_blocks(text):
            for rid in refs_in(b["text"]):
                refs[rid].append({"file": rel, "line": b["line"], "section": path_str(b["path"]),
                                  "text": short(b["text"], 140)})
    claims = pr.claims()
    children = defaultdict(set)
    by_source = defaultdict(set)
    for c in claims:
        for b in c.get("basis", []):
            children[b].add(c["id"])
        for s in c.get("sources", []):
            by_source[s].add(c["id"])
    dec_by_claim = defaultdict(set)
    for d in pr.decisions():
        for b in d.get("basis", []):
            dec_by_claim[b].add(d["id"])
    return {"refs": refs, "children": children, "by_source": by_source, "dec_by_claim": dec_by_claim,
            "claims": {c["id"]: c for c in claims}}


def impact(pr, ids, idx=None) -> dict:
    idx = idx or build_index(pr)
    start = set()
    for i in ids:
        if i.startswith("S-"):
            start |= idx["by_source"].get(i, set())
        else:
            start.add(i)
    affected, frontier = set(start), list(start)
    derived = set()
    while frontier:
        cur = frontier.pop()
        for ch in idx["children"].get(cur, set()):
            if ch not in affected:
                affected.add(ch)
                derived.add(ch)
                frontier.append(ch)
    refs = []
    seen = set()
    for cid in sorted(affected) + [i for i in ids if i.startswith("S-")]:
        for r in idx["refs"].get(cid, []):
            key = (r["file"], r["line"])
            if key in seen:
                continue
            seen.add(key)
            refs.append({**r, "via": cid})
    decisions = sorted({d for cid in affected for d in idx["dec_by_claim"].get(cid, set())})
    commits = [k for k in pr.commitments() if k.get("decision") in decisions and k.get("status") == "open"]
    return {"start": sorted(start), "derived": sorted(derived), "refs": sorted(refs, key=lambda r: (r["file"], r["line"])),
            "decisions": decisions, "commitments": [k["id"] for k in commits]}


def cmd_impact(args):
    pr = get_project(args.project)
    ids = split_ids(args.ids)
    if not ids:
        raise StratError("Укажите записи или источники: ./strat impact C-004 S-002")
    res = impact(pr, ids)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0
    cl = res["start"]
    print("Изменение затрагивает:")
    print(f"  записи: {', '.join(cl) or '—'}" + (f"; выводы на их основе: {', '.join(res['derived'])}"
                                                    if res["derived"] else ""))
    if res["refs"]:
        print("  места в документах (пересмотреть):")
        cur = None
        for r in res["refs"]:
            if r["file"] != cur:
                cur = r["file"]
                print(f"    {cur}")
            print(f"      стр. {r['line']} · {r['section']} · через {r['via']}: {r['text']}")
    else:
        print("  в документах ссылок нет")
    if res["decisions"]:
        print(f"  решения, основания которых затронуты: {', '.join(res['decisions'])}")
    if res["commitments"]:
        print(f"  открытые обязательства по этим решениям: {', '.join(res['commitments'])}")
    return 0


# ---------- поиск ----------

def _corpus(pr, kinds):
    docs = []
    if "claim" in kinds:
        for c in pr.claims():
            st = "" if c.get("status") == "active" else f" [{c.get('status')}]"
            docs.append({"id": c["id"], "kind": "claim", "project": pr.slug,
                         "title": f"{c['id']} ({c.get('type')}){st}",
                         "text": " ".join([c.get("text", ""), c.get("quote", ""), " ".join(c.get("tags", []))]),
                         "preview": c.get("text", "")})
    if "source" in kinds:
        for s in pr.sources():
            body = snapshot_text(pr, s)
            chunks = [body[i:i + 1500] for i in range(0, min(len(body), 60000), 1500)] or [""]
            for n, ch in enumerate(chunks):
                docs.append({"id": s["id"], "kind": "source", "project": pr.slug,
                             "title": f"{s['id']} {short(s.get('title'), 70)}" + (f" (часть {n + 1})" if n else ""),
                             "text": (s.get("title", "") + " ") * 2 + ch, "preview": ch[:300]})
    if "doc" in kinds:
        for rel in project_docs(pr):
            text = strip_comments((pr.dir / rel).read_text(encoding="utf-8", errors="replace"))
            sec, buf, line0 = "(начало)", [], 1
            for i, line in enumerate(text.splitlines() + ["# __end__"], 1):
                if line.startswith("#"):
                    if "".join(buf).strip():
                        docs.append({"id": f"{rel}:{line0}", "kind": "doc", "project": pr.slug,
                                     "title": f"{rel} › {sec}", "text": sec + " " + " ".join(buf),
                                     "preview": " ".join(buf)[:300]})
                    sec, buf, line0 = line.lstrip("#").strip(), [], i
                else:
                    buf.append(line)
    return docs


def bm25(docs, query, k1=1.5, b=0.75):
    toks = [textio.tokens(d["text"]) for d in docs]
    n = len(docs)
    if not n:
        return []
    avg = sum(len(t) for t in toks) / n or 1
    df = Counter()
    for t in toks:
        df.update(set(t))
    q = textio.tokens(query)
    scores = []
    for d, t in zip(docs, toks):
        tf = Counter(t)
        s = 0.0
        for w in q:
            if w not in tf:
                continue
            idf = math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5))
            s += idf * tf[w] * (k1 + 1) / (tf[w] + k1 * (1 - b + b * len(t) / avg))
        if s > 0:
            scores.append((s, d))
    scores.sort(key=lambda x: -x[0])
    return scores


def cmd_search(args):
    kinds = {"claim", "source", "doc"} if not args.type else set(args.type.split(","))
    projects = list_projects() if args.all else [get_project(args.project)]
    docs = []
    for pr in projects:
        docs += _corpus(pr, kinds)
    query = " ".join(args.query) if isinstance(args.query, list) else args.query
    res = bm25(docs, query)[: args.n]
    if args.json:
        print(json.dumps([{"score": round(s, 3), **{k: d[k] for k in ("id", "kind", "project", "title", "preview")}}
                          for s, d in res], ensure_ascii=False, indent=1))
        return 0
    if not res:
        print("Ничего не найдено. Попробуйте другие формулировки (синонимы, термины из источников).")
        return 0
    for s, d in res:
        proj = f"[{d['project']}] " if args.all else ""
        print(f"{s:5.2f} {proj}{d['title']}\n      {short(d['preview'], 200)}")
        if args.links and d["kind"] == "claim":
            pr = next(p for p in projects if p.slug == d["project"])
            imp = impact(pr, [d["id"]])
            c = pr.claim_map().get(d["id"], {})
            print(f"      источники: {', '.join(c.get('sources', [])) or '—'}; основания: "
                  f"{', '.join(c.get('basis', [])) or '—'}; используется: "
                  f"{', '.join(sorted({r['file'] for r in imp['refs']})) or 'нигде'}"
                  + (f"; решения: {', '.join(imp['decisions'])}" if imp["decisions"] else ""))
    return 0
