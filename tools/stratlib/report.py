"""Итоговый отчёт: собирает документы проекта в один HTML и Markdown с приложениями и прозрачностью."""
from __future__ import annotations

import html
import re
from collections import Counter

from .claims import STATUSES, TYPES
from .core import fmt_dt, get_project, human_duration, journal, parse_dt, read_jsonl, short, write_text
from .lint import ID_RE, REF_RE, lint_project, strip_comments

SECTIONS = [
    ("summary.md", "Короткий ответ"),
    ("brief.md", "Постановка задачи"),
    ("research/findings.md", "Картина ситуации"),
    ("options.md", "Варианты"),
    ("strategy/product.md", "Продуктовая стратегия"),
    ("strategy/comms.md", "Коммуникационная стратегия"),
    ("strategy/validation.md", "План проверки"),
]

CSS = """
:root{--bg:#fbfaf7;--fg:#1d1f21;--muted:#5f6368;--line:#e2ded6;--card:#fff;--accent:#1a5fb4;
--fact:#1f7a3a;--obs:#2e7d6b;--est:#1a5fb4;--fc:#6b3fa0;--hyp:#b25d00;--asm:#7a6a2e;--inf:#555;--syn:#b3261e}
@media (prefers-color-scheme:dark){:root{--bg:#16181b;--fg:#e8e6e3;--muted:#a3a7ad;--line:#33363b;--card:#1d2024;
--accent:#7fb0ff;--fact:#6cc28a;--obs:#6bc2b0;--est:#7fb0ff;--fc:#c3a2f0;--hyp:#f0a860;--asm:#d6c27a;--inf:#aaa;--syn:#f28b82}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.6 -apple-system,
BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif}
main{max-width:920px;margin:0 auto;padding:32px 16px 80px}
h1{font-size:30px;line-height:1.25;margin:0 0 8px}h2{font-size:23px;margin:48px 0 12px;padding-top:12px;
border-top:2px solid var(--line)}h3{font-size:19px;margin:28px 0 8px}h4,h5,h6{font-size:16px;margin:20px 0 6px}
p,li{max-width:76ch}a{color:var(--accent)}code{font:13px/1.4 ui-monospace,Menlo,monospace;background:var(--card);
padding:1px 4px;border:1px solid var(--line);border-radius:4px}
table{border-collapse:collapse;width:100%;margin:12px 0;font-size:14px;background:var(--card)}
th,td{border:1px solid var(--line);padding:6px 8px;vertical-align:top;text-align:left}th{background:var(--bg)}
td:first-child{white-space:nowrap}
.meta{color:var(--muted);font-size:14px}.legend{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:13px;
margin:14px 0;color:var(--muted)}.ref{font:12px/1 ui-monospace,Menlo,monospace;text-decoration:none;
padding:1px 4px;border-radius:4px;border:1px solid currentColor;white-space:nowrap}
.t-fact{color:var(--fact)}.t-observation{color:var(--obs)}.t-estimate{color:var(--est)}.t-forecast{color:var(--fc)}
.t-hypothesis{color:var(--hyp)}.t-assumption{color:var(--asm)}.t-inference{color:var(--inf)}
.t-synthetic{color:var(--syn);border-style:dashed}.t-src{color:var(--muted)}.st-bad{text-decoration:line-through}
nav.toc{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 16px;margin:20px 0}
nav.toc a{text-decoration:none}nav.toc ol{margin:6px 0;padding-left:20px}
.box{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 16px;margin:16px 0}
blockquote{margin:12px 0;padding:4px 14px;border-left:3px solid var(--line);color:var(--muted)}
.small{font-size:13px;color:var(--muted)}
@media print{nav.toc{display:none}h2{break-before:page}body{background:#fff}}
@media (max-width:600px){table{display:block;overflow-x:auto}}
"""


def _inline(text: str, ref_html) -> str:
    codes = []

    def keep_code(m):
        codes.append(m.group(1))
        return f"\x00{len(codes) - 1}\x00"

    t = re.sub(r"`([^`]+)`", keep_code, text)
    refs = []

    def keep_ref(m):
        refs.append(m.group(1))
        return f"\x01{len(refs) - 1}\x01"

    t = REF_RE.sub(keep_ref, t)
    t = html.escape(t, quote=False)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", t)
    t = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+|#[^)\s]+|[\w./-]+)\)",
               lambda m: f'<a href="{html.escape(m.group(2))}">{m.group(1)}</a>', t)
    t = re.sub(r"(?<![\"'>=])(https?://[^\s<)]+)", r'<a href="\1">\1</a>', t)
    t = re.sub(r"\x01(\d+)\x01", lambda m: ref_html(refs[int(m.group(1))]), t)
    t = re.sub(r"\x00(\d+)\x00", lambda m: f"<code>{html.escape(codes[int(m.group(1))])}</code>", t)
    return t


def md_to_html(text: str, ref_html, shift=0) -> str:
    lines = strip_comments(text).splitlines()
    out, para, stack = [], [], []
    i = 0

    def close_para():
        if para:
            out.append("<p>" + _inline(" ".join(para), ref_html) + "</p>")
            para.clear()

    def close_lists(to=0):
        while len(stack) > to:
            out.append("</li></%s>" % stack.pop()[1])

    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if s.startswith("```"):
            close_para()
            close_lists()
            buf = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            out.append("<pre><code>" + html.escape("\n".join(buf)) + "</code></pre>")
            i += 1
            continue
        m = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if m:
            close_para()
            close_lists()
            lvl = min(6, len(m.group(1)) + shift)
            title = m.group(2)
            anchor = re.sub(r"[^\w-]+", "-", title.lower()).strip("-")[:60]
            out.append(f'<h{lvl} id="{html.escape(anchor)}">{_inline(title, ref_html)}</h{lvl}>')
            i += 1
            continue
        if s.startswith("|"):
            close_para()
            close_lists()
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(lines[i].strip())
                i += 1
            body = [r for r in rows if not ("-" in r and set(r) <= set("|:- "))]
            if not body:
                continue
            cells = lambda r: [c.strip() for c in r.strip("|").split("|")]  # noqa: E731
            out.append("<table><thead><tr>" + "".join(f"<th>{_inline(c, ref_html)}</th>" for c in cells(body[0])) +
                       "</tr></thead><tbody>")
            for r in body[1:]:
                out.append("<tr>" + "".join(f"<td>{_inline(c, ref_html)}</td>" for c in cells(r)) + "</tr>")
            out.append("</tbody></table>")
            continue
        if re.match(r"^\s*(-{3,}|\*{3,})\s*$", line):
            close_para()
            close_lists()
            out.append("<hr>")
            i += 1
            continue
        if s.startswith(">"):
            close_para()
            close_lists()
            buf = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip()[1:].strip())
                i += 1
            out.append("<blockquote>" + _inline(" ".join(buf), ref_html) + "</blockquote>")
            continue
        mi = re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$", line)
        if mi:
            close_para()
            indent = len(mi.group(1).replace("\t", "    "))
            kind = "ol" if mi.group(2)[0].isdigit() else "ul"
            if not stack or indent > stack[-1][0]:
                out.append(f"<{kind}><li>")
                stack.append((indent, kind))
            else:
                while stack and indent < stack[-1][0]:
                    out.append("</li></%s>" % stack.pop()[1])
                if stack and stack[-1][1] != kind:
                    out.append("</li></%s>" % stack.pop()[1])
                    out.append(f"<{kind}><li>")
                    stack.append((indent, kind))
                else:
                    out.append("</li><li>")
            out.append(_inline(mi.group(3), ref_html))
            i += 1
            continue
        if not s:
            close_para()
            i += 1
            continue
        if stack and (line.startswith(" ") or line.startswith("\t")):
            out.append(" " + _inline(s, ref_html))
            i += 1
            continue
        close_lists()
        para.append(s)
        i += 1
    close_para()
    close_lists()
    return "\n".join(out)


def drop_empty_sections(text: str) -> str:
    """Убирает заголовки без содержимого (остатки шаблона), чтобы отчёт не выглядел заполненным, когда он пуст."""
    lines = text.splitlines()
    heads = [(i, len(m.group(1))) for i, l in enumerate(lines) for m in [re.match(r"^(#{1,6})\s+\S", l)] if m]
    drop = set()
    for n, (i, lvl) in enumerate(heads):
        end = len(lines)
        for j, lv in heads[n + 1:]:
            if lv <= lvl:
                end = j
                break
        body = [l for l in lines[i + 1:end] if l.strip() and not re.match(r"^#{1,6}\s", l)]
        body = [l for l in body if not re.fullmatch(r"\|?(\s*\|)+\s*", l.strip()) and
                not (set(l.strip()) <= set("|:- ") and "-" in l)]
        only_table_header = body and all(l.lstrip().startswith("|") for l in body) and len(body) <= 1
        if not body or only_table_header:
            drop.update(range(i, end))
    return "\n".join(l for k, l in enumerate(lines) if k not in drop)


def _ref_renderer(cmap, smap, dids):
    def render(group: str) -> str:
        parts = []
        for rid in ID_RE.findall(group):
            if rid.startswith("C-") and rid in cmap:
                c = cmap[rid]
                bad = " st-bad" if c.get("status") in ("retracted", "superseded") else ""
                tip = f"{TYPES.get(c['type'], c['type'])}: {c.get('text', '')}"
                if c.get("status") != "active":
                    tip += f" [{STATUSES.get(c.get('status'), c.get('status'))}]"
                parts.append(f'<a class="ref t-{c["type"]}{bad}" href="#{rid}" title="{html.escape(tip)}">{rid}</a>')
            elif rid.startswith("S-") and rid in smap:
                parts.append(f'<a class="ref t-src" href="#{rid}" title="{html.escape(smap[rid].get("title", ""))}">'
                             f'{rid}</a>')
            elif rid.startswith("D-") and rid in dids:
                parts.append(f'<a class="ref t-src" href="#{rid.lower()}">{rid}</a>')
            else:
                parts.append(f'<span class="ref t-syn" title="нет в реестре">{rid}?</span>')
        return " ".join(parts)
    return render


def _process_md(pr) -> str:
    st = pr.state()
    from .state import STAGE_NAMES
    lines = ["| Этап | Начат | Завершён | Длительность |", "|---|---|---|---|"]
    for key, info in st.get("stages", {}).items():
        if info.get("status") == "pending":
            continue
        a, b = parse_dt(info.get("started")), parse_dt(info.get("finished"))
        dur = human_duration((b - a).total_seconds()) if a and b else "—"
        lines.append(f"| {STAGE_NAMES.get(key, key)} | {fmt_dt(info.get('started'))} | "
                     f"{fmt_dt(info.get('finished')) if b else 'в работе'} | {dur} |")
    j = read_jsonl(pr.journal_path)
    tools = Counter(r.get("tool") for r in j if r.get("kind") == "tool" and r.get("tool"))
    errors = [r for r in j if r.get("kind") in ("tool_error", "tool_failure")]
    prompts = sum(1 for r in j if r.get("kind") == "user_prompt")
    asked = st.get("human", [])
    costs = read_jsonl(pr.costs_path)
    calc = sum(r.get("calc_usd_delta", 0) or 0 for r in costs)
    claims = pr.claims()
    act = [c for c in claims if c.get("status") == "active"]
    quoted = [c for c in act if c.get("quote")]
    ver = sum(1 for c in quoted if c.get("verified"))
    srcs = pr.sources()
    rel = Counter(s.get("reliability", "?") for s in srcs)
    issues = lint_project(pr)
    e = sum(1 for i in issues if i["severity"] == "error")
    out = ["### Этапы", "\n".join(lines), "", "### Что сделано и сколько стоило",
           f"- Источников: {len(srcs)} (надёжность: " + ", ".join(f"{k} — {v}" for k, v in sorted(rel.items())) + ").",
           f"- Записей в реестре: {len(claims)}, действующих {len(act)}; цитат проверено по снимкам: {ver} из "
           f"{len(quoted)}.",
           "- Вызовы инструментов: " + (", ".join(f"{k} — {v}" for k, v in tools.most_common(10)) or "журнал пуст") + ".",
           f"- Сбоев инструментов: {len(errors)}" + (": " + "; ".join(short(r.get('error') or r.get('text'), 60)
                                                                     for r in errors[:5]) if errors else "") + ".",
           f"- Сообщений руководителя: {prompts}; вопросов агента руководителю: {len(asked)}, "
           f"с ответом: {sum(1 for h in asked if h.get('answer'))}.",
           f"- Стоимость по тарифам API: ${calc:.2f} (подробно: ./strat cost).",
           f"- Проверка документов: ошибок {e}, предупреждений {len(issues) - e}."]
    if asked:
        out += ["", "### Решения, которые принимал человек"]
        for h in asked:
            out.append(f"- {h['id']} {h['question']} — {h.get('answer') or 'нет ответа'}")
    return "\n".join(out)


def build(pr) -> tuple:
    cmap, smap = pr.claim_map(), pr.source_map()
    decisions = pr.decisions()
    dids = {d["id"] for d in decisions}
    render_ref = _ref_renderer(cmap, smap, dids)
    st = pr.state()
    title = st.get("title", pr.slug)
    md_parts = [f"# {title}", ""]
    html_parts = []
    toc = []
    n = 0
    for rel, name in SECTIONS:
        p = pr.dir / rel
        if not p.exists():
            continue
        text = strip_comments(p.read_text(encoding="utf-8", errors="replace"))
        text = re.sub(r"^#\s+.*\n", "", text.lstrip(), count=1)
        text = drop_empty_sections(text)
        if not text.strip():
            continue
        n += 1
        anchor = f"sec{n}"
        toc.append((anchor, name))
        md_parts += [f"## {name}", "", text.strip(), ""]
        html_parts.append(f'<section><h2 id="{anchor}">{name}</h2>' + md_to_html(text, render_ref, shift=1) +
                          "</section>")
    dec_dir = pr.dir / "decisions"
    dec_files = sorted(dec_dir.glob("D-*.md")) if dec_dir.exists() else []
    if dec_files:
        toc.append(("decisions", "Решения и обязательства"))
        body = []
        for f in dec_files:
            t = strip_comments(f.read_text(encoding="utf-8", errors="replace"))
            body.append(f'<div id="{f.stem.lower()}">' + md_to_html(t, render_ref, shift=2) + "</div>")
            md_parts += [t, ""]
        commits = pr.commitments()
        if commits:
            rows = "".join(f"<tr><td>{k['id']}</td><td>{html.escape(k.get('who', ''))}</td>"
                           f"<td>{html.escape(k.get('what', ''))}</td><td>{k.get('due')}</td><td>{k.get('status')}</td>"
                           f"</tr>" for k in commits)
            body.append("<h3>Обязательства</h3><table><thead><tr><th>ID</th><th>Кто</th><th>Что</th><th>Срок</th>"
                        f"<th>Статус</th></tr></thead><tbody>{rows}</tbody></table>")
        html_parts.append('<section><h2 id="decisions">Решения и обязательства</h2>' + "".join(body) + "</section>")
    rev = pr.dir / "revisions.md"
    if rev.exists() and strip_comments(rev.read_text(encoding="utf-8")).strip():
        t = strip_comments(rev.read_text(encoding="utf-8"))
        t = re.sub(r"^#\s+.*\n", "", t.lstrip(), count=1)
        toc.append(("revisions", "Пересмотры"))
        html_parts.append('<section><h2 id="revisions">Пересмотры</h2>' + md_to_html(t, render_ref, shift=1) + "</section>")
        md_parts += ["## Пересмотры", "", t, ""]
    # приложения
    used = set()
    for part in md_parts:
        for g in REF_RE.findall(part):
            used.update(ID_RE.findall(g))
    claims_sorted = sorted(cmap.values(), key=lambda c: c["id"])
    rows = []
    md_rows = ["| ID | Тип | Утверждение | Источник | Цитата / место | Проверка | Статус |", "|---|---|---|---|---|---|---|"]
    for c in claims_sorted:
        src = ", ".join(f'<a href="#{s}">{s}</a>' for s in c.get("sources", [])) or \
              ("осн.: " + ", ".join(f'<a href="#{b}">{b}</a>' for b in c.get("basis", [])) if c.get("basis") else "—")
        ver = {True: "найдена", False: "не найдена", None: "не проверялась"}[c.get("verified")]
        mark = "" if c["id"] in used else ' class="small"'
        quote = (c.get("quote") or "") + (f" ({c['loc']})" if c.get("loc") else "")
        rows.append(f'<tr id="{c["id"]}"{mark}><td>{c["id"]}</td><td class="t-{c["type"]}">'
                    f'{TYPES.get(c["type"], c["type"])}</td><td>{html.escape(c.get("text", ""))}</td><td>{src}</td>'
                    f'<td>{html.escape(short(quote, 400))}</td><td>{ver}</td>'
                    f'<td>{STATUSES.get(c.get("status"), c.get("status"))}</td></tr>')
        md_rows.append(f"| {c['id']} | {TYPES.get(c['type'], c['type'])} | {c.get('text', '').replace('|', '/')} | "
                       f"{', '.join(c.get('sources', []) or c.get('basis', []))} | "
                       f"{short(quote, 200).replace('|', '/')} | {ver} | {STATUSES.get(c.get('status'))} |")
    srows, md_src = [], ["| ID | Источник | Где | Дата | Надёжность |", "|---|---|---|---|---|"]
    for s in sorted(smap.values(), key=lambda s: s["id"]):
        where = s.get("url") or s.get("path") or ""
        link = f'<a href="{html.escape(where)}">{html.escape(short(where, 70))}</a>' if where.startswith("http") \
            else html.escape(short(where, 70))
        flags = " ⚠ внутри найдены инструкции для ИИ" if "injection_suspect" in s.get("flags", []) else ""
        srows.append(f'<tr id="{s["id"]}"><td>{s["id"]}</td><td>{html.escape(s.get("title", ""))}{flags}</td>'
                     f'<td>{link}</td><td>{html.escape(s.get("published") or "")} '
                     f'<span class="small">получен {fmt_dt(s.get("retrieved_at"))}</span></td>'
                     f'<td>{s.get("reliability", "?")}</td></tr>')
        md_src.append(f"| {s['id']} | {s.get('title', '').replace('|', '/')} | {where} | {s.get('published', '')} | "
                      f"{s.get('reliability', '?')} |")
    proc = _process_md(pr)
    toc += [("claims", "Приложение А. Реестр утверждений"), ("sources", "Приложение Б. Источники"),
            ("process", "Приложение В. Как получен результат")]
    html_parts.append('<section><h2 id="claims">Приложение А. Реестр утверждений</h2>'
                      '<p class="small">Серым — записи, на которые нет ссылок в тексте отчёта.</p>'
                      '<table><thead><tr><th>ID</th><th>Тип</th><th>Утверждение</th><th>Источник</th>'
                      '<th>Цитата / место</th><th>Цитата в снимке</th><th>Статус</th></tr></thead><tbody>' +
                      "".join(rows) + "</tbody></table></section>")
    html_parts.append('<section><h2 id="sources">Приложение Б. Источники</h2><p class="small">Надёжность: A — '
                      'первоисточник (регулятор, статистика, отчётность), B — исследование с методикой или '
                      'официальный сайт, C — обзор или СМИ без методики, D — блог, форум, отзывы.</p>'
                      '<table><thead><tr><th>ID</th><th>Источник</th><th>Где</th><th>Дата</th><th>Надёжность</th>'
                      '</tr></thead><tbody>' + "".join(srows) + "</tbody></table></section>")
    html_parts.append('<section><h2 id="process">Приложение В. Как получен результат</h2>' +
                      md_to_html(proc, render_ref, shift=1) + "</section>")
    md_parts += ["## Приложение А. Реестр утверждений", "", "\n".join(md_rows), "",
                 "## Приложение Б. Источники", "", "\n".join(md_src), "", "## Приложение В. Как получен результат", "",
                 proc]
    legend = "".join(f'<span class="ref t-{k}">{v}</span>' for k, v in TYPES.items())
    status = "черновик" if st.get("stages", {}).get("report", {}).get("status") != "done" else "готов"
    head = (f'<header><h1>{html.escape(title)}</h1><div class="meta">Отчёт агента «Стратег» · '
            f'{fmt_dt(st.get("updated"))} · статус: {status} · источников {len(smap)}, записей {len(cmap)}</div>'
            f'<div class="legend">Метки ссылок: {legend}</div>'
            '<p class="small">Каждая метка ведёт к записи реестра с источником и цитатой. Наведите курсор, чтобы '
            'увидеть формулировку.</p></header>')
    toc_html = '<nav class="toc"><strong>Содержание</strong><ol>' + "".join(
        f'<li><a href="#{a}">{html.escape(t)}</a></li>' for a, t in toc) + "</ol></nav>"
    doc = (f'<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" '
           f'content="width=device-width,initial-scale=1"><title>{html.escape(short(title, 60))}</title>'
           f'<style>{CSS}</style></head><body><main>{head}{toc_html}{"".join(html_parts)}</main></body></html>')
    return "\n".join(md_parts), doc


def cmd_report(args):
    pr = get_project(args.project)
    md, doc = build(pr)
    write_text(pr.dir / "report.md", md)
    write_text(pr.dir / "report.html", doc)
    journal(pr, "report", {"files": ["report.md", "report.html"]})
    print(f"Отчёт собран: projects/{pr.slug}/report.html и report.md")
    issues = lint_project(pr)
    e = sum(1 for i in issues if i["severity"] == "error")
    if e:
        print(f"Внимание: в документах {e} ошибок проверки (./strat lint). Отчёт помечен как черновик, пока этап "
              f"report не закрыт.")
    return 0
