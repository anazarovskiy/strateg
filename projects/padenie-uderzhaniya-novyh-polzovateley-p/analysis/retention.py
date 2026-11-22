"""Диагностика удержания: вход sources/files/S-002.csv, выход analysis/retention.md"""
import csv, collections, pathlib, sys
here = pathlib.Path(__file__).resolve().parent
rows = list(csv.DictReader(open(here.parent / "sources/files/S-002.csv")))
M = ["d1", "d7", "d30"]
for r in rows:
    r["n"] = int(r["new_users"])
    for m in M:
        v = r["retained_" + m].strip()
        r[m] = int(v) if v else None
weeks = sorted({r["cohort_week"] for r in rows})
out = []
def p(s=""): out.append(s); print(s)

def rate(rs, m):
    rs = [r for r in rs if r[m] is not None]
    n = sum(r["n"] for r in rs)
    return 100 * sum(r[m] for r in rs) / n if n else float("nan")

p("# Удержание по когортам: расчёт (скрипт retention.py, вход S-002)\n")
p("## 1. Общая динамика по неделям (%)\n")
p("| Неделя | Новые | D1 | D7 | D30 |"); p("|---|---|---|---|---|")
for w in weeks:
    rs = [r for r in rows if r["cohort_week"] == w]
    p(f"| {w} | {sum(r['n'] for r in rs)} | {rate(rs,'d1'):.1f} | {rate(rs,'d7'):.1f} | " + (f"{rate(rs,'d30'):.1f}" if rs[0]['d30'] is not None else "—") + " |")

p("\n## 2. Доли каналов и платформ в новых пользователях (%)\n")
p("| Неделя | android | organic | partner | paid_social |"); p("|---|---|---|---|---|")
for w in weeks:
    rs = [r for r in rows if r["cohort_week"] == w]; t = sum(r["n"] for r in rs)
    f = lambda k, v: 100 * sum(r["n"] for r in rs if r[k] == v) / t
    p(f"| {w} | {f('platform','android'):.1f} | {f('channel','organic'):.1f} | {f('channel','partner'):.1f} | {f('channel','paid_social'):.1f} |")

p("\n## 3. D7 по срезам платформа × канал (%)\n")
segs = sorted({(r["platform"], r["channel"]) for r in rows})
p("| Неделя | " + " | ".join(f"{a}/{b}" for a, b in segs) + " |"); p("|---|" + "---|" * len(segs))
for w in weeks:
    p(f"| {w} | " + " | ".join(
        (f"{rate([r for r in rows if r['cohort_week']==w and r['platform']==a and r['channel']==b],'d7'):.1f}"
         if any(r['cohort_week']==w and r['platform']==a and r['channel']==b for r in rows) else "—") for a, b in segs) + " |")

# Периоды: база и после; границы задаются аргументами
base = (sys.argv[1] if len(sys.argv) > 1 else "2026-01-05", sys.argv[2] if len(sys.argv) > 2 else "2026-03-30")
new = (sys.argv[3] if len(sys.argv) > 3 else "2026-04-27", sys.argv[4] if len(sys.argv) > 4 else "2026-06-15")
B = [r for r in rows if base[0] <= r["cohort_week"] <= base[1]]
N = [r for r in rows if new[0] <= r["cohort_week"] <= new[1]]
p(f"\n## 4. Декомпозиция: база {base[0]}…{base[1]} → период {new[0]}…{new[1]}\n")
p("ΔM = Σ Δw·m(база) [состав] + Σ w(новая)·Δm [внутри групп]; сегменты платформа×канал. Для D30 берутся только зрелые когорты.\n")
p("| Метрика | База % | Новый % | Δ п.п. | Состав п.п. | Внутри групп п.п. |"); p("|---|---|---|---|---|---|")
for m in M:
    Bm = [r for r in B if r[m] is not None]; Nm = [r for r in N if r[m] is not None]
    if not Nm: p(f"| {m} | — | — | нет зрелых когорт | | |"); continue
    nb, nn = sum(r["n"] for r in Bm), sum(r["n"] for r in Nm)
    comp = inner = 0
    for s in segs:
        bs = [r for r in Bm if (r["platform"], r["channel"]) == s]; ns = [r for r in Nm if (r["platform"], r["channel"]) == s]
        wb = sum(r["n"] for r in bs) / nb; wn = sum(r["n"] for r in ns) / nn
        mb = sum(r[m] for r in bs) / sum(r["n"] for r in bs); mn = sum(r[m] for r in ns) / sum(r["n"] for r in ns)
        comp += (wn - wb) * mb; inner += wn * (mn - mb)
    b, n_ = rate(Bm, m), rate(Nm, m)
    p(f"| {m.upper()} | {b:.1f} | {n_:.1f} | {n_-b:+.1f} | {100*comp:+.1f} | {100*inner:+.1f} |")
p("\n## 5. Изменение внутри групп по платформе и каналу, D7, п.п.\n")
p("| Сегмент | База % | Новый % | Δ п.п. | Доля базы % | Доля нового % |"); p("|---|---|---|---|---|---|")
nb, nn = sum(r["n"] for r in B), sum(r["n"] for r in N)
for s in segs:
    bs = [r for r in B if (r["platform"], r["channel"]) == s]; ns = [r for r in N if (r["platform"], r["channel"]) == s]
    p(f"| {s[0]}/{s[1]} | {rate(bs,'d7'):.1f} | {rate(ns,'d7'):.1f} | {rate(ns,'d7')-rate(bs,'d7'):+.1f} | {100*sum(r['n'] for r in bs)/nb:.1f} | {100*sum(r['n'] for r in ns)/nn:.1f} |")

# 6. Три периода и шаги между ними
import statistics
PER = [("P1 янв–фев", "2026-01-05", "2026-02-23"), ("P2 март", "2026-03-02", "2026-03-30"), ("P3 апр–июнь", "2026-04-06", "2026-06-15")]
def sel(a, b, plat=None): return [r for r in rows if a <= r["cohort_week"] <= b and (plat is None or r["platform"] == plat)]
p("\n## 6. Три периода: удержание по платформам (%)\n")
p("Периоды: P1 05.01–23.02, P2 02.03–30.03, P3 06.04–15.06 (по датам перелома из разделов 2–3). D30 только по зрелым когортам.\n")
p("| Платформа | Период | Новые | Доля paid_social % | D1 | D7 | D30 |"); p("|---|---|---|---|---|---|---|")
for plat in ["android", "ios", None]:
    for nm, a, b in PER:
        rs = sel(a, b, plat); n = sum(r["n"] for r in rs)
        p(f"| {plat or 'все'} | {nm} | {n} | {100*sum(r['n'] for r in rs if r['channel']=='paid_social')/n:.1f} | {rate(rs,'d1'):.1f} | {rate(rs,'d7'):.1f} | {rate(rs,'d30'):.1f} |")
p("\n## 7. Шаги P1→P2 и P2→P3: состав (канал) и внутри каналов, п.п.; по платформам\n")
p("| Платформа | Шаг | Метрика | Δ общий | Состав | Внутри каналов |"); p("|---|---|---|---|---|---|")
chans = ["organic", "partner", "paid_social"]
for plat in ["android", "ios", None]:
    for (n1, a1, b1), (n2, a2, b2) in [(PER[0], PER[1]), (PER[1], PER[2])]:
        for m in M:
            A = [r for r in sel(a1, b1, plat) if r[m] is not None]; Bq = [r for r in sel(a2, b2, plat) if r[m] is not None]
            na, nb_ = sum(r["n"] for r in A), sum(r["n"] for r in Bq); comp = inner = 0
            for c in chans:
                ac = [r for r in A if r["channel"] == c]; bc = [r for r in Bq if r["channel"] == c]
                wa = sum(r["n"] for r in ac) / na; wb = sum(r["n"] for r in bc) / nb_
                ma = sum(r[m] for r in ac) / sum(r["n"] for r in ac); mb = sum(r[m] for r in bc) / sum(r["n"] for r in bc)
                comp += (wb - wa) * ma; inner += wb * (mb - ma)
            p(f"| {plat or 'все'} | {n1[:2]}→{n2[:2]} | {m.upper()} | {rate(Bq,m)-rate(A,m):+.1f} | {100*comp:+.1f} | {100*inner:+.1f} |")
p("\n## 8. Шум: недельный разброс D7 внутри P1 (стандартное отклонение, п.п.)\n")
for plat in ["android", "ios"]:
    v = [rate([r for r in rows if r["cohort_week"] == w and r["platform"] == plat], "d7") for w in weeks if "2026-01-05" <= w <= "2026-02-23"]
    p(f"- {plat}: среднее {statistics.mean(v):.1f}, σ {statistics.stdev(v):.2f} (n={len(v)} недель)")

p("\n## 9. Сценарий: вернуть состав каналов P1 (15/55/30) при удержании внутри каналов как в P3\n")
p("Это пересчёт состава, а не прогноз: он не учитывает потерю объёма и изменение качества трафика при смене закупки.\n")
p("| Метрика | Факт P3 % | При составе P1 % | Разница п.п. |"); p("|---|---|---|---|")
for m in M:
    A = [r for r in sel(PER[0][1], PER[0][2]) if r[m] is not None]; C = [r for r in sel(PER[2][1], PER[2][2]) if r[m] is not None]
    na = sum(r["n"] for r in A); alt = 0
    for c in chans:
        wa = sum(r["n"] for r in A if r["channel"] == c) / na
        cc = [r for r in C if r["channel"] == c]; mc = sum(r[m] for r in cc) / sum(r["n"] for r in cc)
        alt += wa * mc
    p(f"| {m.upper()} | {rate(C,m):.1f} | {100*alt:.1f} | {100*alt-rate(C,m):+.1f} |")
p("\n## 10. Сценарий: Android в P3 с удержанием внутри каналов как в P2 (при составе P3), D7\n")
C = sel(PER[2][1], PER[2][2]); Bq = sel(PER[1][1], PER[1][2]); tot = sum(r["n"] for r in C); alt = 0
for plat in ["android", "ios"]:
    for c in chans:
        cc = [r for r in C if r["platform"] == plat and r["channel"] == c]; bb = [r for r in Bq if r["platform"] == plat and r["channel"] == c]
        m_new = sum(r["d7"] for r in cc) / sum(r["n"] for r in cc); m_old = sum(r["d7"] for r in bb) / sum(r["n"] for r in bb)
        alt += sum(r["n"] for r in cc) / tot * (m_old if plat == "android" else m_new)
p(f"- D7 всех новых в P3 факт {rate(C,'d7'):.1f}%, если бы Android внутри каналов оставался на уровне марта: {100*alt:.1f}% ({100*alt-rate(C,'d7'):+.1f} п.п.)")

(here / "retention.md").write_text("\n".join(out) + "\n")
