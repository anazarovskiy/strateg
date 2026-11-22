"""MCP-сервер (stdio, JSON-RPC 2.0) поверх команд strat.

Нужен для сред, где удобнее вызывать типизированные инструменты, а не оболочку: Cursor, Codex, Windsurf и др.
Логика одна и та же: каждый вызов инструмента выполняет ту же команду CLI в этом же процессе.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys

SERVER_INFO = {"name": "strat", "version": "1.0.0"}
DEFAULT_VERSION = "2025-06-18"

S = {"type": "string"}
B = {"type": "boolean"}
P = {"project": {"type": "string", "description": "Проект (по умолчанию активный)"}}


def _opt(args, key, flag):
    v = args.get(key)
    if v in (None, "", False):
        return []
    if v is True:
        return [flag]
    return [flag, str(v)]


TOOLS = [
    ("strat_status", "Сводка активного проекта: этап, незавершённые шаги, вопросы руководителю, источники, "
     "утверждения, стоимость. Вызывайте в начале работы и после перерыва.", {}, [],
     lambda a: ["status"]),
    ("strat_init", "Создать проект под новую задачу руководителя продукта и сделать его активным.",
     {"title": S, "workflow": {"type": "string", "enum": ["full", "quick", "comms", "diagnostic", "revisit", "brief"]}},
     ["title"], lambda a: ["init", a["title"]] + _opt(a, "workflow", "--workflow")),
    ("strat_stage", "Начать, закрыть или переоткрыть этап. Закрытие проверяет документы этапа.",
     {"name": {"type": "string", "enum": ["frame", "collect", "research", "options", "strategy", "report", "decide",
                                          "monitor"]},
      "action": {"type": "string", "enum": ["start", "done", "reopen"]}}, ["name", "action"],
     lambda a: ["stage", a["name"], a["action"]]),
    ("strat_step", "Отметить шаг (идемпотентно): start вернёт «уже выполнен», если шаг сделан раньше.",
     {"id": S, "action": {"type": "string", "enum": ["start", "done", "fail", "skip"]}, "note": S}, ["id", "action"],
     lambda a: ["step", a["id"], a["action"]] + _opt(a, "note", "--note")),
    ("strat_ask", "Записать вопрос руководителю (существенный выбор).", {"question": S, "options": S},
     ["question"], lambda a: ["ask", a["question"]] + _opt(a, "options", "--options")),
    ("strat_answer", "Записать ответ руководителя на вопрос H-...", {"id": S, "answer": S}, ["id", "answer"],
     lambda a: ["answer", a["id"], a["answer"]]),
    ("strat_next", "Записать следующий шаг (для продолжения после перерыва).", {"text": S}, ["text"],
     lambda a: ["next", a["text"]]),
    ("strat_source_fetch", "Скачать открытую страницу или файл по ссылке, сохранить снимок и зарегистрировать "
     "источник.", {"url": S, "title": S, "reliability": {"type": "string", "enum": ["A", "B", "C", "D", "?"]}},
     ["url"], lambda a: ["source", "fetch", a["url"]] + _opt(a, "title", "--title") +
     _opt(a, "reliability", "--reliability")),
    ("strat_source_add", "Зарегистрировать локальный файл (документ, таблицу, PDF, изображение, расшифровку).",
     {"path": S, "title": S, "kind": S, "reliability": S, "redact": B}, ["path"],
     lambda a: ["source", "add", a["path"]] + _opt(a, "title", "--title") + _opt(a, "kind", "--kind") +
     _opt(a, "reliability", "--reliability") + _opt(a, "redact", "--redact")),
    ("strat_source_note", "Зарегистрировать источник по тексту, полученному другим инструментом (дословность не "
     "гарантирована).", {"url": S, "title": S, "text": S, "reliability": S, "kind": S}, ["title", "text"],
     lambda a: ["source", "note", "--text", a["text"], "--title", a["title"]] + _opt(a, "url", "--url") +
     _opt(a, "reliability", "--reliability") + _opt(a, "kind", "--kind")),
    ("strat_source_list", "Список источников проекта.", {}, [], lambda a: ["source", "list"]),
    ("strat_claim_add", "Добавить утверждение в реестр. Для факта нужна дословная цитата из снимка источника.",
     {"text": S, "type": {"type": "string", "enum": ["fact", "observation", "estimate", "forecast", "hypothesis",
                                                     "assumption", "inference", "synthetic"]},
      "source": S, "quote": S, "loc": S, "basis": S, "method": S, "confidence": S, "as_of": S, "expires": S,
      "tags": S, "allow_unverified": B, "note": S}, ["text", "type"],
     lambda a: ["claim", "add", "--text", a["text"], "--type", a["type"]] + _opt(a, "source", "--source") +
     _opt(a, "quote", "--quote") + _opt(a, "loc", "--loc") + _opt(a, "basis", "--basis") +
     _opt(a, "method", "--method") + _opt(a, "confidence", "--confidence") + _opt(a, "as_of", "--as-of") +
     _opt(a, "expires", "--expires") + _opt(a, "tags", "--tags") + _opt(a, "allow_unverified", "--allow-unverified") +
     _opt(a, "note", "--note")),
    ("strat_claim_list", "Найти записи реестра по типу, статусу, источнику или тексту.",
     {"type": S, "status": S, "source": S, "q": S}, [],
     lambda a: ["claim", "list"] + _opt(a, "type", "--type") + _opt(a, "status", "--status") +
     _opt(a, "source", "--source") + _opt(a, "q", "--q")),
    ("strat_claim_status", "Оспорить, отозвать, заменить или восстановить запись реестра.",
     {"id": S, "action": {"type": "string", "enum": ["dispute", "retract", "supersede", "restore"]}, "by": S,
      "note": S}, ["id", "action"],
     lambda a: ["claim", a["action"], a["id"]] + _opt(a, "by", "--by") + _opt(a, "note", "--note")),
    ("strat_lint", "Проверить документы проекта: ссылки на реестр, факты и гипотезы, обязательные разделы.",
     {"stage": S}, [], lambda a: ["lint"] + _opt(a, "stage", "--stage")),
    ("strat_impact", "Что затрагивает изменение записи или источника: выводы, разделы документов, решения.",
     {"ids": S}, ["ids"], lambda a: ["impact"] + a["ids"].replace(",", " ").split()),
    ("strat_search", "Поиск по реестру, источникам и документам (BM25 с учётом словоформ).",
     {"query": S, "all_projects": B}, ["query"],
     lambda a: ["search", a["query"]] + (["--all"] if a.get("all_projects") else [])),
    ("strat_decision_add", "Записать принятое решение: основания, ответственный, показатели, условия пересмотра.",
     {"title": S, "chosen": S, "basis": S, "owner": S, "metric": S, "review_by": S, "review_if": S, "rejected": S},
     ["title", "basis", "review_by"],
     lambda a: ["decision", "add", "--title", a["title"], "--basis", a["basis"], "--review-by", a["review_by"]] +
     _opt(a, "chosen", "--chosen") + _opt(a, "owner", "--owner") + _opt(a, "metric", "--metric") +
     _opt(a, "review_if", "--review-if") + _opt(a, "rejected", "--rejected")),
    ("strat_decision_check", "Что решать сейчас: сроки пересмотра, изменившиеся основания, обязательства.", {}, [],
     lambda a: ["decision", "check"]),
    ("strat_cost", "Стоимость и время по этапам и моделям.", {}, [], lambda a: ["cost"]),
    ("strat_report", "Собрать итоговый отчёт report.html и report.md.", {}, [], lambda a: ["report"]),
    ("strat_model_sync", "Занести финансовую модель (analysis/model.json) в реестр: допущения и оценки с основаниями.",
     {}, [], lambda a: ["model", "sync"]),
    ("strat_deliverable_set", "Задать части итогового документа в структуре заказчика (через «|»).",
     {"parts": S, "lead": S, "source": S}, ["parts"],
     lambda a: ["deliverable", "set", "--parts", a["parts"]] + _opt(a, "lead", "--lead") + _opt(a, "source", "--source")),
]


def run_cli(argv) -> tuple:
    from .cli import main
    out, err = io.StringIO(), io.StringIO()
    os.environ["STRAT_ACTOR"] = os.environ.get("STRAT_ACTOR") or "mcp"
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(argv)
        except SystemExit as e:  # argparse
            code = e.code if isinstance(e.code, int) else 1
    text = out.getvalue()
    if err.getvalue().strip():
        text += ("\n" if text else "") + err.getvalue()
    return code or 0, text.strip() or "(пустой вывод)"


def _tool_list():
    tools = []
    for name, desc, props, req, _ in TOOLS:
        schema = {"type": "object", "properties": {**props, **P}, "required": req}
        tools.append({"name": name, "description": desc, "inputSchema": schema})
    return tools


def handle(msg: dict):
    mid = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") or {}

    def ok(result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def err(code, text):
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": text}}

    if method == "initialize":
        ver = params.get("protocolVersion") or DEFAULT_VERSION
        return ok({"protocolVersion": ver, "capabilities": {"tools": {"listChanged": False}},
                   "serverInfo": SERVER_INFO,
                   "instructions": "Инструменты агента «Стратег»: реестр источников и утверждений, проверки, "
                                   "состояние процесса. Правила работы — в AGENTS.md."})
    if mid is None:
        return None  # уведомления не требуют ответа
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": _tool_list()})
    if method == "resources/list":
        return ok({"resources": []})
    if method == "prompts/list":
        return ok({"prompts": []})
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        spec = next((t for t in TOOLS if t[0] == name), None)
        if not spec:
            return err(-32602, f"Неизвестный инструмент {name}")
        missing = [r for r in spec[3] if not args.get(r)]
        if missing:
            return ok({"content": [{"type": "text", "text": "Не хватает параметров: " + ", ".join(missing)}],
                       "isError": True})
        argv = spec[4](args)
        if args.get("project"):
            argv = ["--project", args["project"]] + argv
        code, text = run_cli(argv)
        return ok({"content": [{"type": "text", "text": text}], "isError": code not in (0, 3)})
    return err(-32601, f"Метод {method} не поддерживается")


def serve() -> int:
    stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace")
    stdout = sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            stdout.write(json.dumps({"jsonrpc": "2.0", "id": None,
                                     "error": {"code": -32700, "message": "parse error"}}) + "\n")
            stdout.flush()
            continue
        batch = msg if isinstance(msg, list) else [msg]
        replies = [r for r in (handle(m) for m in batch if isinstance(m, dict)) if r is not None]
        if replies:
            out = replies if isinstance(msg, list) else replies[0]
            stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
            stdout.flush()
    return 0
