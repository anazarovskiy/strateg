# -*- coding: utf-8 -*-
"""Мощность теста отката онбординга на Android: вход sources/files/S-002.csv, выход analysis/power.md"""
import csv, pathlib, math
here = pathlib.Path(__file__).resolve().parent
rows = list(csv.DictReader(open(here.parent / "sources/files/S-002.csv")))
out = []
def p(s=""): out.append(s); print(s)
A = [r for r in rows if r["platform"] == "android" and r["cohort_week"] >= "2026-04-06"]
weeks = sorted({r["cohort_week"] for r in A})
per_week = sum(int(r["new_users"]) for r in A) / len(weeks)
base = sum(int(r["retained_d7"]) for r in A) / sum(int(r["new_users"]) for r in A)
p("# Мощность теста отката онбординга на Android (скрипт power.py, вход S-002)\n")
p(f"- Новых на Android в неделю в апр–июне: {per_week:.0f} (среднее по {len(weeks)} неделям с данными)")
p(f"- D7 Android в апр–июне: {100*base:.1f}%\n")
za, zb = 1.96, 0.8416  # двусторонний α=0.05, мощность 80%
p("| Ожидаемый прирост D7, п.п. | Пользователей на группу | Недель набора при 50/50 |"); p("|---|---|---|")
for d in (1.5, 2.0, 3.0, 4.0, 4.7):
    p1, p2 = base, base + d / 100; pb = (p1 + p2) / 2
    n = ((za * math.sqrt(2 * pb * (1 - pb)) + zb * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))) ** 2) / (p2 - p1) ** 2
    p(f"| {d} | {n:.0f} | {n/(per_week/2):.1f} |")
p("\nНабор плюс 7 дней ожидания D7 для последней когорты. D30 за 2–3 недели не наблюдается.")

p("\n## Полуширина 95% интервала разницы D7 при заданном N на группу (база как выше)\n")
p("| Пользователей на группу | Полуширина, п.п. |"); p("|---|---|")
for n in (3254, 7222, 12751):
    p(f"| {n} | {100*1.96*math.sqrt(2*base*(1-base)/n):.1f} |")

(here / "power.md").write_text("\n".join(out) + "\n", encoding="utf-8")
