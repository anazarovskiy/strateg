"""Проекты и состояние процесса: этапы, шаги, вопросы к руководителю, сводка для продолжения работы."""
from __future__ import annotations

import json
import os
import platform
import shutil
import sys
from pathlib import Path

from .core import (PROJECTS, ROOT, TEMPLATES, StratError, active_slug, fmt_dt, get_project, human_duration,
                   journal, list_projects, load_config, now_iso, parse_dt, read_jsonl, save_json, set_active,
                   short, slugify, split_list)
from .core import Project

STAGES = [
    ("frame", "постановка"),
    ("collect", "материалы"),
    ("research", "исследование"),
    ("options", "варианты"),
    ("strategy", "стратегия"),
    ("report", "отчёт"),
    ("decide", "решение"),
    ("monitor", "сопровождение"),
]
STAGE_NAMES = dict(STAGES)
WORKFLOWS = {
    "full": [s for s, _ in STAGES],
    "quick": ["frame", "research", "report"],
    "comms": ["frame", "collect", "research", "options", "strategy", "report"],
    "diagnostic": ["frame", "collect", "research", "options", "strategy", "report", "decide"],
    "revisit": ["monitor"],
    "brief": [s for s, _ in STAGES],
}
WORKFLOW_NAMES = {"full": "полный цикл", "quick": "быстрая справка", "comms": "коммуникационная задача",
                  "diagnostic": "диагностика метрики", "revisit": "пересмотр",
                  "brief": "стратегия по брифу заказчика"}
STAGE_TEMPLATES = {
    "frame": ["brief.md"],
    "research": ["research/plan.md", "research/findings.md"],
    "options": ["options.md"],
    "strategy": ["strategy/product.md", "strategy/comms.md", "strategy/validation.md"],
    "report": ["summary.md"],
    "monitor": ["revisions.md"],
}
# процесс «по брифу»: вместо коммуникационной стратегии — финансовая модель и план по кварталам,
# итог — документ в структуре заказчика (deliverable.md)
WORKFLOW_TEMPLATES = {
    "brief": {"strategy": ["strategy/product.md", "strategy/validation.md", "strategy/roadmap.md"],
              "report": ["summary.md"]},
}


def stage_templates(workflow: str, stage: str) -> list:
    return WORKFLOW_TEMPLATES.get(workflow, {}).get(stage, STAGE_TEMPLATES.get(stage, []))


STAGE_HINTS = {
    "frame": "Постановка: уточнить вопрос, решение, получателя, ограничения и данные; заполнить brief.md (навык frame).",
    "collect": "Материалы: зарегистрировать файлы руководителя и стартовые открытые источники (навык collect).",
    "research": "Исследование: план от вопросов, меняющих решение, затем ответы с источниками (навык research).",
    "options": "Варианты: не меньше трёх, со статус-кво, сильные возражения от critic (навык options).",
    "strategy": "Стратегия: продуктовая, коммуникационная и план проверки (навыки product, comms, validation).",
    "report": "Отчёт: summary.md и ./strat report (навык report).",
    "decide": "Решение: записать выбор руководителя, ответственных, показатели и условия пересмотра (навык decide).",
    "monitor": "Сопровождение: следить за сроками и новыми данными (навык revisit, ./strat decision check).",
}
DIRS = ["inputs", "sources/snapshots", "sources/files", "evidence", "research", "interviews/real",
        "interviews/synthetic", "interviews/personas", "analysis", "strategy", "decisions", "log"]


def _copy_templates(pr: Project, stage: str) -> list:
    made = []
    for rel in stage_templates(pr.state().get("workflow"), stage):
        dst = pr.dir / rel
        if dst.exists():
            continue
        src = TEMPLATES / Path(rel).name
        if not src.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        text = src.read_text(encoding="utf-8").replace("{{title}}", pr.state().get("title", pr.slug))
        dst.write_text(text, encoding="utf-8")
        made.append(rel)
    return made


def cmd_init(args):
    title = " ".join(args.title).strip() if isinstance(args.title, list) else (args.title or "").strip()
    if not title:
        raise StratError("Укажите название задачи: ./strat init \"Почему падает удержание в приложении X\"")
    slug = args.slug or slugify(title)
    pr = Project(slug)
    if pr.exists():
        set_active(slug)
        print(f"Проект «{slug}» уже существует — сделал его активным. Сводка: ./strat status")
        return 0
    for d in DIRS:
        (pr.dir / d).mkdir(parents=True, exist_ok=True)
    wf = args.workflow or "full"
    if wf not in WORKFLOWS:
        raise StratError("Тип процесса: " + ", ".join(WORKFLOWS))
    st = {
        "project": slug,
        "title": title,
        "created": now_iso(),
        "workflow": wf,
        "stage": None,
        "stages": {s: {"status": "pending"} for s, _ in STAGES},
        "steps": {},
        "human": [],
        "next": "Постановка задачи: уточнить вопрос, решение, получателя, ограничения и данные (навык frame).",
        "budget_usd": float(args.budget) if args.budget else load_config("policy").get("budget_usd_default", 15.0),
        "owner": args.owner or "",
    }
    pr.save_state(st)
    (pr.dir / "README.md").write_text(
        f"# {title}\n\nРабочая папка проекта агента «Стратег». Сводка состояния: `./strat status -p {slug}`.\n"
        "Главные документы: brief.md → research/findings.md → options.md → strategy/ → report.html.\n",
        encoding="utf-8")
    set_active(slug)
    journal(pr, "init", {"title": title, "workflow": wf})
    print(f"Проект создан: projects/{slug} (процесс: {WORKFLOW_NAMES[wf]}). Он активен.")
    print("Дальше: ./strat stage frame start — и заполнить brief.md.")
    return 0


def cmd_use(args):
    pr = Project(args.slug)
    if not pr.exists():
        raise StratError(f"Проект {args.slug} не найден. Список: ./strat list")
    set_active(args.slug)
    print(f"Активный проект: {args.slug}")
    return 0


def cmd_list(args):
    act = active_slug()
    items = list_projects()
    if not items:
        print("Проектов нет. Создайте: ./strat init \"Название задачи\"")
        return 0
    for pr in items:
        st = pr.state()
        stage = STAGE_NAMES.get(st.get("stage"), "не начат")
        mark = "*" if pr.slug == act else " "
        print(f"{mark} {pr.slug:32} {stage:14} обновлён {fmt_dt(st.get('updated'))}  {short(st.get('title'), 60)}")
    return 0


def _mark_stage(st, stage, status):
    s = st["stages"].setdefault(stage, {})
    if status == "in_progress":
        s.setdefault("started", now_iso())
        s.pop("finished", None)
    if status == "done":
        s.setdefault("started", now_iso())
        s["finished"] = now_iso()
    s["status"] = status


def cmd_stage(args):
    pr = get_project(args.project)
    st = pr.state()
    stage = args.name
    if stage not in STAGE_NAMES:
        raise StratError("Этапы: " + ", ".join(f"{k} ({v})" for k, v in STAGES))
    if args.action == "start":
        order = [s for s, _ in STAGES]
        prev = [s for s in order[: order.index(stage)] if s in WORKFLOWS.get(st.get("workflow"), order)]
        undone = [s for s in prev if st["stages"].get(s, {}).get("status") != "done"]
        _mark_stage(st, stage, "in_progress")
        st["stage"] = stage
        st["next"] = STAGE_HINTS.get(stage, st.get("next"))
        made = _copy_templates(pr, stage)
        pr.save_state(st)
        journal(pr, "stage", {"stage": stage, "action": "start"})
        print(f"Этап «{STAGE_NAMES[stage]}» начат." + (f" Созданы шаблоны: {', '.join(made)}." if made else ""))
        if undone:
            print("  Не завершены предыдущие этапы: " + ", ".join(STAGE_NAMES[s] for s in undone) +
                  ". Если это осознанно (например, быстрый процесс), продолжайте.")
        return 0
    if args.action == "done":
        from .lint import lint_project
        issues = lint_project(pr, stage=stage)
        errors = [i for i in issues if i["severity"] == "error"]
        if errors and not args.force:
            print(f"Этап «{STAGE_NAMES[stage]}» нельзя закрыть: проверка нашла ошибки ({len(errors)}).")
            for i in errors[:25]:
                print(f"  ✗ {i['file']}:{i['line']} {i['message']}")
            print("Исправьте и повторите. Закрыть принудительно: --force (будет записано в журнал).")
            return 1
        _mark_stage(st, stage, "done")
        order = [s for s, _ in STAGES if s in WORKFLOWS.get(st.get("workflow"), [x for x, _ in STAGES])]
        nxt = None
        if stage in order and order.index(stage) + 1 < len(order):
            nxt = order[order.index(stage) + 1]
        if nxt:
            st["next"] = "Начать этап: " + STAGE_HINTS.get(nxt, nxt)
        pr.save_state(st)
        journal(pr, "stage", {"stage": stage, "action": "done", "forced": bool(errors and args.force)})
        warn = [i for i in issues if i["severity"] == "warning"]
        print(f"Этап «{STAGE_NAMES[stage]}» завершён." + (f" Предупреждений: {len(warn)}." if warn else "") +
              (f" Следующий: {STAGE_NAMES[nxt]} ({nxt})." if nxt else ""))
        return 0
    if args.action == "reopen":
        _mark_stage(st, stage, "in_progress")
        st["stage"] = stage
        pr.save_state(st)
        journal(pr, "stage", {"stage": stage, "action": "reopen", "note": args.note or ""})
        print(f"Этап «{STAGE_NAMES[stage]}» снова открыт.")
        return 0
    raise StratError("Действие: start | done | reopen")


def cmd_step(args):
    """Шаги с идемпотентностью: повторный start для выполненного шага возвращает код 3."""
    pr = get_project(args.project)
    st = pr.state()
    steps = st.setdefault("steps", {})
    sid = args.id
    cur = steps.get(sid, {})
    if args.action == "start":
        if cur.get("status") == "done" and not args.force:
            print(f"Шаг {sid} уже выполнен {fmt_dt(cur.get('finished'))}: {cur.get('note', '')}. Не повторяю.")
            return 3
        if cur.get("status") == "in_progress":
            print(f"Шаг {sid} был начат {fmt_dt(cur.get('started'))} и не завершён. Проверьте, что уже сделано "
                  f"(./strat log, файлы проекта), и продолжайте с места остановки.")
        cur.update({"status": "in_progress", "started": cur.get("started") or now_iso(), "attempts":
                    cur.get("attempts", 0) + 1})
    elif args.action == "done":
        cur.update({"status": "done", "finished": now_iso()})
    elif args.action == "fail":
        cur.update({"status": "failed", "failed_at": now_iso()})
    elif args.action == "skip":
        cur.update({"status": "skipped", "finished": now_iso()})
    else:
        raise StratError("Действие: start | done | fail | skip")
    if args.note:
        cur["note"] = args.note
    steps[sid] = cur
    pr.save_state(st)
    journal(pr, "step", {"step": sid, "action": args.action, "note": args.note or ""})
    print(f"Шаг {sid}: {cur['status']}.")
    return 0


def cmd_ask(args):
    pr = get_project(args.project)
    st = pr.state()
    hs = st.setdefault("human", [])
    hid = f"H-{len(hs) + 1:03d}"
    item = {"id": hid, "question": args.question, "options": split_list(args.options), "asked": now_iso(),
            "stage": st.get("stage"), "answer": None, "why": args.why or ""}
    hs.append(item)
    pr.save_state(st)
    journal(pr, "ask", {"id": hid, "question": args.question})
    print(f"{hid} записан: {args.question}")
    return 0


def cmd_answer(args):
    pr = get_project(args.project)
    st = pr.state()
    for h in st.get("human", []):
        if h["id"] == args.id.upper():
            h["answer"] = args.answer
            h["answered"] = now_iso()
            if args.note:
                h["note"] = args.note
            pr.save_state(st)
            journal(pr, "answer", {"id": h["id"], "answer": args.answer}, actor="user")
            print(f"{h['id']}: ответ записан.")
            return 0
    raise StratError(f"Вопрос {args.id} не найден.")


def cmd_next(args):
    pr = get_project(args.project)
    st = pr.state()
    st["next"] = " ".join(args.text) if isinstance(args.text, list) else args.text
    pr.save_state(st)
    journal(pr, "next", {"text": st["next"]})
    print("Следующий шаг записан.")
    return 0


def cmd_note(args):
    pr = get_project(args.project, must_exist=False)
    text = " ".join(args.text) if isinstance(args.text, list) else args.text
    journal(pr, args.kind or "note", {"text": text})
    print("Записано в журнал.")
    return 0


def cmd_workflow(args):
    pr = get_project(args.project)
    st = pr.state()
    if args.name not in WORKFLOWS:
        raise StratError("Тип процесса: " + ", ".join(f"{k} ({v})" for k, v in WORKFLOW_NAMES.items()))
    old = st.get("workflow")
    st["workflow"] = args.name
    pr.save_state(st)
    journal(pr, "route", {"text": f"процесс: {old} → {args.name}", "why": args.why or ""})
    print(f"Процесс: {WORKFLOW_NAMES[args.name]}." + (f" Причина: {args.why}" if args.why else ""))
    return 0


# ---------- сводка ----------

def status_data(pr: Project) -> dict:
    from . import claims as C
    from .decisions import check as dcheck
    st = pr.state()
    claims = pr.claims()
    sources = pr.sources()
    by_type = {}
    for c in claims:
        if c.get("status") in ("retracted", "superseded"):
            continue
        by_type[c["type"]] = by_type.get(c["type"], 0) + 1
    unverified = [c["id"] for c in claims if c.get("verified") is False and c.get("status") == "active"]
    flagged = [s["id"] for s in sources if "injection_suspect" in s.get("flags", [])]
    costs = read_jsonl(pr.costs_path)
    calc = sum(r.get("calc_usd_delta", 0) or 0 for r in costs)
    cc = {}
    for r in costs:
        if r.get("cc_usd") is not None:
            cc[r.get("session")] = r["cc_usd"]
    open_h = [h for h in st.get("human", []) if not h.get("answer")]
    steps = st.get("steps", {})
    return {
        "project": pr.slug, "title": st.get("title"), "workflow": st.get("workflow"), "stage": st.get("stage"),
        "stages": st.get("stages", {}), "next": st.get("next"), "open_questions": open_h,
        "steps_in_progress": {k: v for k, v in steps.items() if v.get("status") == "in_progress"},
        "steps_failed": {k: v for k, v in steps.items() if v.get("status") == "failed"},
        "sources": len(sources), "claims": by_type, "unverified": unverified, "injection_flags": flagged,
        "cost_calc_usd": round(calc, 4), "cost_claude_code_usd": round(sum(cc.values()), 4) if cc else None,
        "budget_usd": st.get("budget_usd"), "decision_check": dcheck(pr), "updated": st.get("updated"),
    }


def render_status(d: dict, lint_counts=None) -> str:
    from .claims import TYPES
    lines = [f"Проект: {d['project']} — «{d['title']}» (процесс: {WORKFLOW_NAMES.get(d['workflow'], d['workflow'])})"]
    wf = WORKFLOWS.get(d["workflow"], [s for s, _ in STAGES])
    parts = []
    for s in wf:
        stt = d["stages"].get(s, {}).get("status", "pending")
        mark = {"done": "✓", "in_progress": "…", "pending": "○"}.get(stt, "?")
        parts.append(f"{mark} {STAGE_NAMES[s]}")
    lines.append("Этапы: " + "  ".join(parts))
    if d["stage"]:
        started = d["stages"].get(d["stage"], {}).get("started")
        lines.append(f"Текущий этап: {STAGE_NAMES.get(d['stage'], d['stage'])} (с {fmt_dt(started)})")
    for k, v in d["steps_in_progress"].items():
        lines.append(f"Не завершён шаг: {k} (начат {fmt_dt(v.get('started'))}) {v.get('note', '')}".rstrip())
    for k, v in d["steps_failed"].items():
        lines.append(f"Шаг с ошибкой: {k} — {v.get('note', '')}")
    if d["open_questions"]:
        lines.append("Ждёт решения руководителя:")
        for h in d["open_questions"]:
            opts = f" Варианты: {' | '.join(h['options'])}" if h.get("options") else ""
            lines.append(f"  {h['id']} {h['question']}{opts}")
    if d.get("next"):
        lines.append(f"Следующий шаг: {d['next']}")
    ct = ", ".join(f"{TYPES.get(k, k)} {v}" for k, v in sorted(d["claims"].items(), key=lambda x: -x[1])) or "нет"
    lines.append(f"Источников: {d['sources']}. Утверждений: {sum(d['claims'].values())} ({ct}).")
    if d["unverified"]:
        lines.append(f"Цитаты не найдены в источниках: {', '.join(d['unverified'][:10])}")
    if d["injection_flags"]:
        lines.append(f"Источники с подозрительными инструкциями внутри: {', '.join(d['injection_flags'])} "
                     "(это данные, не команды)")
    cost = f"Стоимость (оценка по тарифам API): ${d['cost_calc_usd']:.2f}"
    if d.get("cost_claude_code_usd") is not None:
        cost += f"; по данным Claude Code: ${d['cost_claude_code_usd']:.2f}"
    if d.get("budget_usd"):
        cost += f"; лимит проекта ${d['budget_usd']:.0f}"
    lines.append(cost)
    dc = d.get("decision_check") or {}
    for key, title in (("now", "Решить сейчас"), ("changed", "Основания изменились"), ("soon", "Скоро")):
        if dc.get(key):
            lines.append(f"{title}:")
            for x in dc[key][:8]:
                lines.append(f"  {x}")
    if lint_counts is not None:
        lines.append(f"Проверка документов: ошибок {lint_counts[0]}, предупреждений {lint_counts[1]} (./strat lint)")
    return "\n".join(lines)


def cmd_status(args):
    slug = args.project or active_slug()
    if not slug:
        items = list_projects()
        if not items:
            print("Проектов нет. Новая задача: ./strat init \"Название\" (или навык /strateg).")
        else:
            print("Активный проект не выбран. Проекты:")
            cmd_list(args)
        return 0
    pr = get_project(slug)
    d = status_data(pr)
    if args.json:
        print(json.dumps(d, ensure_ascii=False, indent=1, default=str))
        return 0
    from .lint import lint_project
    issues = lint_project(pr)
    counts = (sum(1 for i in issues if i["severity"] == "error"), sum(1 for i in issues if i["severity"] == "warning"))
    print(render_status(d, counts))
    return 0


def cmd_log(args):
    pr = get_project(args.project, must_exist=False)
    rows = read_jsonl(pr.journal_path if pr else ROOT / "log" / "journal.jsonl")
    if args.kind:
        kinds = set(split_list(args.kind.replace(",", "|")))
        rows = [r for r in rows if r.get("kind") in kinds]
    rows = rows[-args.tail:] if args.tail else rows
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0
    for r in rows:
        extra = {k: v for k, v in r.items() if k not in ("ts", "actor", "kind")}
        txt = extra.pop("text", "") or ""
        rest = "; ".join(f"{k}={short(v, 80)}" for k, v in extra.items() if v not in (None, "", [], {}))
        print(f"{fmt_dt(r.get('ts'))} {r.get('actor', ''):10} {r.get('kind', ''):13} {short(txt, 100)} {rest}".rstrip())
    return 0


def cmd_doctor(args):
    print(f"Python {platform.python_version()} ({sys.executable}) — {'ок' if sys.version_info >= (3, 9) else 'нужен 3.9+'}")
    print(f"Папка агента: {ROOT}")
    checks = [
        ("pdftotext", shutil.which("pdftotext"), "текст из PDF без Read (необязательно)"),
        ("ffmpeg", shutil.which("ffmpeg"), "кадры из видео (необязательно)"),
        ("whisper", shutil.which("whisper") or shutil.which("whisper-cli"), "расшифровка аудио (необязательно)"),
        ("git", shutil.which("git"), "версии файлов (желательно)"),
    ]
    for name, path, why in checks:
        print(f"{'✓' if path else '·'} {name:10} {path or 'не найден'} — {why}")
    for mod, why in (("openpyxl", "точнее читает xlsx (без него работает встроенный разбор)"),
                     ("pypdf", "запасной разбор PDF")):
        try:
            __import__(mod)
            print(f"✓ {mod:10} установлен — {why}")
        except ImportError:
            print(f"· {mod:10} не установлен — {why}")
    import ssl
    vp = ssl.get_default_verify_paths()
    has_ca = (vp.cafile and os.path.exists(vp.cafile)) or (vp.capath and os.path.isdir(vp.capath))
    try:
        import certifi  # type: ignore  # noqa: F401
        has_certifi = True
    except ImportError:
        has_certifi = False
    if has_ca or has_certifi:
        print("✓ сертификаты TLS найдены — ./strat source fetch сможет скачивать страницы")
    else:
        print("! сертификаты TLS не найдены: на macOS с Python с python.org запустите «Install Certificates.command» "
              "(Applications/Python 3.x) или pip install certifi; иначе страницы читаются через WebFetch")
    pol = load_config("policy")
    print(f"Политика источников: режим {pol.get('web', {}).get('mode', 'denylist')}, "
          f"запрещённых доменов {len(pol.get('web', {}).get('deny_domains', []))}")
    sett = ROOT / ".claude" / "settings.json"
    print(f"{'✓' if sett.exists() else '✗'} .claude/settings.json (хуки, статус, права)")
    try:
        PROJECTS.mkdir(exist_ok=True)
        t = PROJECTS / ".write_test"
        t.write_text("ok")
        t.unlink()
        print("✓ запись в projects/ работает")
    except OSError as e:
        print(f"✗ нет записи в projects/: {e}")
    errs = ROOT / "log" / "hook_errors.log"
    if errs.exists() and errs.stat().st_size:
        print(f"! есть ошибки хуков: {errs} (последние строки ниже)")
        print("\n".join(errs.read_text(encoding="utf-8", errors="replace").splitlines()[-5:]))
    print(f"Активный проект: {active_slug() or 'нет'}")
    return 0
