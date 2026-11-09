"""Учёт стоимости и времени: токены из журналов сессий Claude Code и тарифы из config/prices.json."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from .core import (CONFIG, append_jsonl, get_project, human_duration, load_json, now_iso, parse_dt, read_jsonl,
                   save_json)

PRICE_FIELDS = ("input", "cache_write_5m", "cache_write_1h", "cache_read", "output")


def prices() -> dict:
    return load_json(CONFIG / "prices.json", {}) or {}


def price_for(model: str, table=None):
    table = table or prices()
    models = table.get("models", {})
    m = (model or "").lower().split("[")[0]
    m = table.get("aliases", {}).get(m, m)
    best = None
    for key in models:
        if m.startswith(key) and (best is None or len(key) > len(best)):
            best = key
    return (best, models[best]) if best else (None, None)


def message_cost(model: str, u: dict, table=None):
    key, p = price_for(model, table)
    if not p:
        return None
    cc = u.get("cache_creation") or {}
    w5 = cc.get("ephemeral_5m_input_tokens") if cc else None
    w1 = cc.get("ephemeral_1h_input_tokens") if cc else None
    if w5 is None and w1 is None:
        w5, w1 = u.get("cache_creation_input_tokens", 0) or 0, 0
    tokens = {
        "input": u.get("input_tokens", 0) or 0,
        "cache_write_5m": w5 or 0,
        "cache_write_1h": w1 or 0,
        "cache_read": u.get("cache_read_input_tokens", 0) or 0,
        "output": u.get("output_tokens", 0) or 0,
    }
    lc = p.get("long_context")
    rate = p
    if lc and tokens["input"] + tokens["cache_read"] + tokens["cache_write_5m"] + tokens["cache_write_1h"] > lc.get(
            "threshold", 10 ** 12):
        rate = lc
    usd = sum(tokens[f] * rate.get(f, 0) for f in PRICE_FIELDS) / 1_000_000
    ws = (u.get("server_tool_use") or {}).get("web_search_requests", 0) or 0
    usd += ws * (table or prices()).get("web_search_per_1000", 0) / 1000
    return usd


def transcript_files(transcript_path: str, session_id: str = None) -> list:
    p = Path(transcript_path)
    files = [p] if p.exists() else []
    sid = session_id or p.stem
    sub = p.parent / sid / "subagents"
    if sub.exists():
        files += sorted(sub.glob("*.jsonl"))
    return files


def usage_from_files(files) -> dict:
    """Суммирует токены по моделям. Повторные записи одного ответа (стриминг) считаются один раз."""
    seen = {}
    for f in files:
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                for n, line in enumerate(fh):
                    if '"usage"' not in line:
                        continue
                    try:
                        o = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    msg = o.get("message") if isinstance(o.get("message"), dict) else None
                    if not msg or not isinstance(msg.get("usage"), dict):
                        continue
                    model = msg.get("model") or "unknown"
                    if model.startswith("<"):
                        continue
                    mid = msg.get("id") or o.get("requestId") or o.get("uuid") or f"{f.name}:{n}"
                    seen[mid] = (model, msg["usage"], "subagent" in str(f))
        except OSError:
            continue
    table = prices()
    out = {"models": {}, "calc_usd": 0.0, "unpriced": [], "requests": 0}
    for model, u, is_sub in seen.values():
        m = out["models"].setdefault(model, {"requests": 0, "input": 0, "output": 0, "cache_read": 0,
                                             "cache_write": 0, "web_search": 0, "usd": 0.0, "subagent": is_sub})
        m["requests"] += 1
        m["input"] += u.get("input_tokens", 0) or 0
        m["output"] += u.get("output_tokens", 0) or 0
        m["cache_read"] += u.get("cache_read_input_tokens", 0) or 0
        m["cache_write"] += u.get("cache_creation_input_tokens", 0) or 0
        m["web_search"] += (u.get("server_tool_use") or {}).get("web_search_requests", 0) or 0
        c = message_cost(model, u, table)
        if c is None:
            if model not in out["unpriced"]:
                out["unpriced"].append(model)
        else:
            m["usd"] += c
            out["calc_usd"] += c
        out["requests"] += 1
    return out


def record_stop(pr, payload: dict):
    """Вызывается хуком Stop: добавляет приращение стоимости сессии с привязкой к текущему этапу."""
    tp = payload.get("transcript_path")
    sid = payload.get("session_id") or (Path(tp).stem if tp else "unknown")
    if not tp:
        return None
    usage = usage_from_files(transcript_files(tp, sid))
    state_path = pr.dir / "log" / "cost_state.json"
    cs = load_json(state_path, {}) or {}
    prev = cs.get(sid, {"calc_usd": 0.0, "models": {}})
    delta_usd = usage["calc_usd"] - prev.get("calc_usd", 0.0)
    dmodels = {}
    for model, m in usage["models"].items():
        pm = prev.get("models", {}).get(model, {})
        d = {k: m[k] - pm.get(k, 0) for k in ("requests", "input", "output", "cache_read", "cache_write", "web_search")}
        if any(d.values()):
            dmodels[model] = d
    sl = load_json(pr.dir / "log" / "statusline.json", {}) or {}
    cc = (sl.get(sid) or {}).get("cost_usd")
    st = pr.state()
    if abs(delta_usd) < 1e-9 and not dmodels and cc == prev.get("cc_usd"):
        return None
    row = {"ts": now_iso(), "session": sid, "stage": st.get("stage"), "calc_usd_delta": round(delta_usd, 6),
           "calc_usd_session": round(usage["calc_usd"], 6), "models": dmodels, "cc_usd": cc,
           "unpriced": usage["unpriced"]}
    append_jsonl(pr.costs_path, row)
    cs[sid] = {"calc_usd": usage["calc_usd"], "models": usage["models"], "cc_usd": cc, "ts": now_iso()}
    save_json(state_path, cs)
    return row


def cmd_cost(args):
    pr = get_project(args.project)
    rows = read_jsonl(pr.costs_path)
    st = pr.state()
    by_stage = defaultdict(float)
    by_model = defaultdict(lambda: defaultdict(int))
    sessions = {}
    for r in rows:
        by_stage[r.get("stage") or "—"] += r.get("calc_usd_delta", 0) or 0
        for m, d in (r.get("models") or {}).items():
            for k, v in d.items():
                by_model[m][k] += v
        sessions[r.get("session")] = r
    total = sum(by_stage.values())
    cc_total = sum((s.get("cc_usd") or 0) for s in sessions.values())
    if args.json:
        print(json.dumps({"total_calc_usd": total, "by_stage": by_stage, "by_model": by_model,
                          "claude_code_usd": cc_total, "sessions": len(sessions)}, ensure_ascii=False, indent=1))
        return 0
    from .state import STAGE_NAMES
    print(f"Стоимость проекта (оценка по тарифам API из config/prices.json): ${total:.2f}")
    if cc_total:
        print(f"По данным строки статуса Claude Code: ${cc_total:.2f} (сессий: {len(sessions)})")
    print("При подписке Claude Pro/Max деньги за токены не списываются; цифра показывает эквивалент по тарифам API.")
    if by_stage:
        print("\nПо этапам:")
        for s, v in by_stage.items():
            info = st.get("stages", {}).get(s, {})
            dur = ""
            a, b = parse_dt(info.get("started")), parse_dt(info.get("finished"))
            if a and b:
                dur = f", длительность этапа {human_duration((b - a).total_seconds())}"
            print(f"  {STAGE_NAMES.get(s, s):14} ${v:.3f}{dur}")
    if by_model:
        print("\nПо моделям (токены):")
        for m, d in by_model.items():
            print(f"  {m:28} запросов {d['requests']}, вход {d['input']:,}, выход {d['output']:,}, "
                  f"кэш чтение {d['cache_read']:,}, кэш запись {d['cache_write']:,}, поисков {d['web_search']}"
                  .replace(",", " "))
    unpriced = sorted({m for r in rows for m in r.get("unpriced", [])})
    if unpriced:
        print(f"\nНет цены для моделей: {', '.join(unpriced)} — добавьте их в config/prices.json.")
    if not rows:
        print("Записей о стоимости пока нет: они появляются после каждого ответа агента (хук Stop).")
    return 0
