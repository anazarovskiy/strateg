"""Командная строка strat: точка входа для агента, хуков, MCP и человека."""
from __future__ import annotations

import argparse
import os
import sys

from .core import StratError, eprint


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-p", "--project", default=argparse.SUPPRESS, help="проект (по умолчанию активный)")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="вывод в JSON")

    ap = argparse.ArgumentParser(prog="strat", description="Инструменты агента «Стратег». Подробно: README.md")
    ap.add_argument("-p", "--project", default=None, help="проект (по умолчанию активный)")
    ap.add_argument("--json", action="store_true", default=False, help="вывод в JSON, где поддерживается")
    sub = ap.add_subparsers(dest="cmd", metavar="команда")

    def sp(name, help_, parent=sub):
        return parent.add_parser(name, help=help_, parents=[common], description=help_)

    p = sp("init", "создать проект под новую задачу")
    p.add_argument("title", nargs="+", help="название задачи")
    p.add_argument("--slug", help="имя папки проекта (латиницей)")
    p.add_argument("--workflow", choices=["full", "quick", "comms", "diagnostic", "revisit", "brief"], help="тип процесса")
    p.add_argument("--budget", help="лимит стоимости, USD")
    p.add_argument("--owner", help="руководитель продукта (роль, без персональных данных)")

    p = sp("use", "сделать проект активным")
    p.add_argument("slug")
    sp("list", "список проектов")
    sp("status", "сводка состояния проекта")
    sp("doctor", "проверка окружения и возможностей")

    p = sp("stage", "этап процесса: start | done | reopen")
    p.add_argument("name")
    p.add_argument("action", choices=["start", "done", "reopen"])
    p.add_argument("--force", action="store_true", help="закрыть этап, несмотря на ошибки проверки")
    p.add_argument("--note")

    p = sp("step", "шаг внутри этапа (идемпотентно)")
    p.add_argument("id")
    p.add_argument("action", choices=["start", "done", "fail", "skip"])
    p.add_argument("--note")
    p.add_argument("--force", action="store_true", help="повторить уже выполненный шаг")

    p = sp("ask", "записать вопрос руководителю")
    p.add_argument("question")
    p.add_argument("--options", help="варианты через |")
    p.add_argument("--why", help="почему вопрос важен для следующего шага")

    p = sp("answer", "записать ответ руководителя")
    p.add_argument("id")
    p.add_argument("answer")
    p.add_argument("--note")

    p = sp("next", "записать следующий шаг")
    p.add_argument("text", nargs="+")

    p = sp("note", "запись в журнал (в том числе выбор маршрута: --kind route)")
    p.add_argument("text", nargs="+")
    p.add_argument("--kind", default="note")

    p = sp("workflow", "сменить тип процесса (маршрутизация)")
    p.add_argument("name")
    p.add_argument("--why")

    p = sp("log", "журнал действий")
    p.add_argument("--tail", type=int, default=40)
    p.add_argument("--kind")

    # источники
    src = sp("source", "реестр источников").add_subparsers(dest="sub", metavar="действие")
    p = sp("add", "зарегистрировать файл или папку", src)
    p.add_argument("path")
    p.add_argument("--title")
    p.add_argument("--kind", help="doc|table|pdf|web|image|audio|video|interview|data|synthetic|analysis")
    p.add_argument("--reliability", help="A|B|C|D|?")
    p.add_argument("--publisher")
    p.add_argument("--published", help="дата публикации")
    p.add_argument("--note")
    p.add_argument("--redact", action="store_true", help="обезличить расшифровку интервью")
    p.add_argument("--refresh", action="store_true")
    p = sp("fetch", "скачать открытую страницу или файл", src)
    p.add_argument("url")
    p.add_argument("--title")
    p.add_argument("--reliability")
    p.add_argument("--publisher")
    p.add_argument("--published")
    p.add_argument("--refresh", action="store_true")
    p = sp("note", "зарегистрировать источник по тексту из другого инструмента (WebFetch и т. п.)", src)
    p.add_argument("--url")
    p.add_argument("--title")
    p.add_argument("--text")
    p.add_argument("--text-file", dest="text_file", help="файл с текстом или - для stdin")
    p.add_argument("--kind")
    p.add_argument("--reliability")
    p.add_argument("--publisher")
    p.add_argument("--published")
    p.add_argument("--verbatim", action="store_true", help="текст дословный")
    p.add_argument("--via", help="чем получен текст")
    p.add_argument("--note")
    p.add_argument("--cleanup", action="store_true", help="удалить временный файл после регистрации")
    p.add_argument("--refresh", action="store_true")
    sp("list", "список источников", src)
    p = sp("show", "показать источник", src)
    p.add_argument("id")
    p.add_argument("--text", action="store_true", help="показать текст снимка")
    p.add_argument("--chars", type=int, default=4000)
    p = sp("rate", "оценить надёжность или уточнить сведения об источнике", src)
    p.add_argument("id")
    p.add_argument("--reliability")
    p.add_argument("--publisher")
    p.add_argument("--published")
    p.add_argument("--note")

    # утверждения
    cl = sp("claim", "реестр утверждений").add_subparsers(dest="sub", metavar="действие")
    p = sp("add", "добавить утверждение", cl)
    p.add_argument("--text", required=True)
    p.add_argument("--type", required=True,
                   help="fact|observation|estimate|forecast|hypothesis|assumption|inference|synthetic")
    p.add_argument("--source", help="S-001,S-002")
    p.add_argument("--quote", help="дословная цитата из снимка")
    p.add_argument("--loc", help="место: страница, лист и ячейка, экран, время")
    p.add_argument("--basis", help="основания для вывода или оценки: C-001,C-002")
    p.add_argument("--method", help="как получена оценка")
    p.add_argument("--confidence", choices=["high", "medium", "low"])
    p.add_argument("--as-of", dest="as_of", help="на какую дату верно")
    p.add_argument("--expires", help="когда устареет (YYYY-MM-DD)")
    p.add_argument("--tags", help="метки через |")
    p.add_argument("--allow-unverified", dest="allow_unverified", action="store_true")
    p.add_argument("--note")
    p.add_argument("--by", help="кто добавил (researcher, extractor, user)")
    p = sp("list", "найти записи", cl)
    p.add_argument("--type")
    p.add_argument("--status")
    p.add_argument("--source")
    p.add_argument("--tag")
    p.add_argument("--q")
    p.add_argument("--unverified", action="store_true")
    p = sp("show", "показать запись и где она используется", cl)
    p.add_argument("id")
    p = sp("verify", "сверить цитаты со снимками источников", cl)
    p.add_argument("ids", nargs="*")
    p.add_argument("--strict", action="store_true")
    for action, h in (("dispute", "оспорить"), ("retract", "отозвать"), ("supersede", "заменить новой записью"),
                      ("restore", "вернуть в действующие")):
        p = sp(action, h, cl)
        p.add_argument("id")
        p.add_argument("--by", help="запись или источник, из-за которого меняется статус")
        p.add_argument("--note")
        p.set_defaults(action=action)
    p = sp("update", "исправить запись (с историей)", cl)
    p.add_argument("id")
    for f in ("text", "quote", "loc", "method", "expires", "note"):
        p.add_argument(f"--{f}")
    p.add_argument("--confidence", choices=["high", "medium", "low"])
    p.add_argument("--as-of", dest="as_of")
    p.add_argument("--tags")
    p.add_argument("--add-source", dest="add_source")

    p = sp("lint", "проверить документы проекта")
    p.add_argument("files", nargs="*")
    p.add_argument("--stage")
    p.add_argument("--strict", action="store_true")
    p.add_argument("--only-integrity", dest="only_integrity", action="store_true")

    p = sp("release-check", "проверить репозиторий перед отправкой: личные пути, контакты, ключи")
    p.add_argument("--fix-paths", dest="fix_paths", action="store_true")

    sp("consistency", "найти темы, описанные в документах разными числами (сроки, пороги)")

    p = sp("impact", "что затрагивает изменение записи или источника")
    p.add_argument("ids", nargs="+")

    p = sp("search", "поиск по реестру, источникам и документам")
    p.add_argument("query", nargs="+")
    p.add_argument("--all", action="store_true", help="во всех проектах")
    p.add_argument("--type", help="claim,source,doc")
    p.add_argument("-n", type=int, default=10)
    p.add_argument("--links", action="store_true", help="показать связи записей")

    dc = sp("decision", "решения").add_subparsers(dest="sub", metavar="действие")
    p = sp("add", "записать решение", dc)
    p.add_argument("--title", required=True)
    p.add_argument("--basis", required=True)
    p.add_argument("--review-by", dest="review_by", required=True)
    p.add_argument("--chosen")
    p.add_argument("--rejected", help="отвергнутые варианты через |")
    p.add_argument("--owner")
    p.add_argument("--metric", help="показатели через |")
    p.add_argument("--review-if", dest="review_if", help="условия пересмотра через |")
    p.add_argument("--note")
    sp("list", "список решений", dc)
    sp("check", "что решать сейчас", dc)
    p = sp("update", "изменить статус или срок пересмотра", dc)
    p.add_argument("id")
    p.add_argument("--status", choices=["active", "revised", "reversed", "closed"])
    p.add_argument("--review-by", dest="review_by")
    p.add_argument("--add-basis", dest="add_basis")
    p.add_argument("--remove-basis", dest="remove_basis", help="убрать основания, на которые решение больше не опирается")
    p.add_argument("--note")

    km = sp("commit", "обязательства по решениям").add_subparsers(dest="sub", metavar="действие")
    p = sp("add", "добавить обязательство", km)
    p.add_argument("--who", required=True)
    p.add_argument("--what", required=True)
    p.add_argument("--due", required=True)
    p.add_argument("--decision")
    p.add_argument("--note")
    for action in ("done", "drop"):
        p = sp(action, "закрыть обязательство" if action == "done" else "снять обязательство", km)
        p.add_argument("id")
        p.add_argument("--note")
    sp("list", "список обязательств", km)

    sp("cost", "стоимость и время")
    sp("report", "собрать итоговый отчёт (HTML и Markdown)")

    mo = sp("model", "финансовая модель: допущения и результаты расчёта в реестре").add_subparsers(
        dest="sub", metavar="действие")
    p = sp("init", "положить шаблон расчёта в analysis/model.py", mo)
    p.add_argument("--force", action="store_true")
    p = sp("sync", "занести analysis/model.json в реестр (допущения и оценки)", mo)
    p.add_argument("file", nargs="?")
    p.add_argument("--verbose", action="store_true")
    sp("show", "записи модели: ключ → номер в реестре", mo)

    dl = sp("deliverable", "итоговый документ в структуре, которую задал заказчик").add_subparsers(
        dest="sub", metavar="действие")
    p = sp("set", "задать части документа (из брифа)", dl)
    p.add_argument("--parts", required=True, help="«Клиент и его проблема|Предложение и путь к клиенту|…»")
    p.add_argument("--lead", help="что сказать в начале документа, если бриф это требует")
    p.add_argument("--source", help="источник, где задана структура (обычно бриф, S-001)")
    sp("show", "показать заданную структуру", dl)

    da = sp("data", "анализ данных").add_subparsers(dest="sub", metavar="действие")
    p = sp("profile", "профиль таблицы: типы, пропуски, периоды, единицы", da)
    p.add_argument("file")
    p.add_argument("--sheet")
    p.add_argument("--out")

    me = sp("media", "изображения, видео, аудио").add_subparsers(dest="sub", metavar="действие")
    p = sp("frames", "извлечь кадры из видео (нужен ffmpeg)", me)
    p.add_argument("video")
    p.add_argument("--every", type=int, default=5, help="интервал, секунд")
    p.add_argument("--max", type=int, default=40)
    p.add_argument("--name")
    p = sp("transcribe", "расшифровать аудио (нужен whisper)", me)
    p.add_argument("audio")
    p.add_argument("--language", default="ru")

    p = sp("pii", "персональные данные: scan | redact")
    p.add_argument("action", choices=["scan", "redact"])
    p.add_argument("file")
    p.add_argument("--out")

    p = sp("eval", "автоматическая часть рубрики для любого markdown-ответа")
    p.add_argument("file")

    p = sp("hook", "обработчик хука Claude Code (вызывается из .claude/settings.json)")
    p.add_argument("event")
    sp("mcp", "запустить MCP-сервер (stdio)")
    return ap


def main(argv=None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    if not getattr(args, "cmd", None):
        ap.print_help()
        return 0
    try:
        return dispatch(args, ap) or 0
    except StratError as e:
        eprint(f"Ошибка: {e}")
        return e.code
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:  # вывод обрезан (например, | head) — это не ошибка
        try:
            sys.stdout = open(os.devnull, "w")
        except OSError:
            pass
        return 0


def dispatch(args, ap):
    from . import claims, costs, data, decisions, deliverable, evalcheck, impact, lint, model, release, report, \
        sources, state
    c = args.cmd
    simple = {
        "init": state.cmd_init, "use": state.cmd_use, "list": state.cmd_list, "status": state.cmd_status,
        "doctor": state.cmd_doctor, "stage": state.cmd_stage, "step": state.cmd_step, "ask": state.cmd_ask,
        "answer": state.cmd_answer, "next": state.cmd_next, "note": state.cmd_note, "workflow": state.cmd_workflow,
        "log": state.cmd_log, "lint": lint.cmd_lint, "consistency": lint.cmd_consistency, "release-check": release.cmd_release_check, "impact": impact.cmd_impact, "search": impact.cmd_search,
        "cost": costs.cmd_cost, "report": report.cmd_report, "pii": data.cmd_pii, "eval": evalcheck.cmd_eval,
    }
    if c in ("stage", "step", "ask", "answer", "next", "workflow"):
        # состояние читается и перезаписывается целиком; параллельные субагенты не должны затирать шаги друг друга
        from .core import FileLock, get_project
        pr = get_project(getattr(args, "project", None))
        with FileLock(pr.state_path):
            return simple[c](args)
    if c in simple:
        return simple[c](args)
    if c == "hook":
        from .hooks import run
        return run(args.event)
    if c == "mcp":
        from .mcp import serve
        return serve()
    sub = getattr(args, "sub", None)
    table = {
        ("source", "add"): sources.cmd_add, ("source", "fetch"): sources.cmd_fetch, ("source", "note"): sources.cmd_note,
        ("source", "list"): sources.cmd_list, ("source", "show"): sources.cmd_show, ("source", "rate"): sources.cmd_rate,
        ("claim", "add"): claims.cmd_add, ("claim", "list"): claims.cmd_list, ("claim", "show"): claims.cmd_show,
        ("claim", "verify"): claims.cmd_verify, ("claim", "update"): claims.cmd_update,
        ("claim", "dispute"): claims.cmd_status, ("claim", "retract"): claims.cmd_status,
        ("claim", "supersede"): claims.cmd_status, ("claim", "restore"): claims.cmd_status,
        ("decision", "add"): decisions.cmd_add, ("decision", "list"): decisions.cmd_list,
        ("decision", "check"): decisions.cmd_check, ("decision", "update"): decisions.cmd_update,
        ("commit", "add"): decisions.commit_add, ("commit", "list"): decisions.commit_list,
        ("data", "profile"): data.cmd_profile, ("media", "frames"): data.cmd_frames,
        ("media", "transcribe"): data.cmd_transcribe,
        ("model", "init"): model.cmd_init, ("model", "sync"): model.cmd_sync, ("model", "show"): model.cmd_show,
        ("deliverable", "set"): deliverable.cmd_set, ("deliverable", "show"): deliverable.cmd_show,
    }
    if (c, sub) in table:
        return table[(c, sub)](args)
    if c == "commit" and sub in ("done", "drop"):
        return decisions.commit_set(args, "done" if sub == "done" else "dropped")
    ap.parse_args([c, "--help"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
