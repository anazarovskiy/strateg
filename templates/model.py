"""Финансовая модель проекта.

Порядок работы:
1. FACTS — числа из материалов руководителя. Каждое сначала занесите в реестр с цитатой
   (./strat claim add --type fact --source S-001 --quote "…"), затем впишите сюда номер записи.
2. ASSUMPTIONS — всё, чего нет в материалах. У допущения есть базовое значение, диапазон, направление
   «что хуже» (worse: "low" или "high"), почему выбрано такое значение и как его проверить.
3. compute(f, a) — сам расчёт. Возвращает результаты: значение, единицу, формулировку и ключи, из которых
   посчитано (basis). Без basis результат не попадёт в реестр.
4. Запуск: python3 analysis/model.py — печатает таблицы и сохраняет analysis/model.json.
   Затем ./strat model sync — допущения и результаты становятся записями реестра; на них ссылаются документы.

Правила расчёта (подробно: knowledge/unit-economics.md):
- Сравнивайте с одной базой («без изменений») и за один период. Эффект инициативы — разница с базой,
  а не все подключения за год.
- Сводите остатки: было на начало + новые − ушедшие = на конец.
- Одно подключение относите к одному каналу привлечения, правило отнесения запишите в допущение.
- У каждого слагаемого прибыли должны быть объём, цена и период.
- Слабый сценарий берёт худшую границу каждого допущения; чувствительность показывает, какое допущение
  сильнее всего двигает главный результат.
"""
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent

# ключ: (значение, единица, формулировка, запись реестра с цитатой)
FACTS = {
    # "gp_no_change": (279_000_000, "руб.", "Валовая прибыль планового года без изменений", "C-004"),
}

# ключ: (базовое, низкое, высокое, единица, формулировка, что хуже, почему так, как проверить)
ASSUMPTIONS = {
    # "conv_offer": (0.05, 0.02, 0.08, "доля", "Доля увидевших предложение, кто создаёт группу", "low",
    #                "по аналогии с …", "показ на 5% базы в первом квартале"),
}

MAIN = ""  # ключ главного результата для таблицы чувствительности, например "gp_delta"


def compute(f, a):
    """f[ключ] и a[ключ] — значения. Верните {ключ: (значение, единица, формулировка, [ключи-основания])}."""
    r = {}
    # пример:
    # extra_months = a["users_reached"] * a["retention_uplift_months"]
    # r["extra_months"] = (extra_months, "мес.", "Дополнительные оплаченные месяцы за год",
    #                      ["users_reached", "retention_uplift_months"])
    # r["gp_delta"] = (extra_months * f["gp_per_month"] - a["direct_costs"], "руб.",
    #                  "Прирост валовой прибыли к сценарию без изменений", ["extra_months", "gp_per_month",
    #                  "direct_costs"])
    return r


# --- служебная часть: обычно менять не нужно ---------------------------------------------------------------

def _values(scenario):
    f = {k: v[0] for k, v in FACTS.items()}
    a = {}
    for k, (base, low, high, *_rest) in ASSUMPTIONS.items():
        worse = ASSUMPTIONS[k][5]
        if scenario == "base":
            a[k] = base
        elif scenario == "weak":
            a[k] = low if worse == "low" else high
        elif scenario == "strong":
            a[k] = high if worse == "low" else low
    return f, a


def run(scenario="base", override=None):
    f, a = _values(scenario)
    a.update(override or {})
    return compute(f, a)


def breakeven(key, result_key, target, lo, hi, steps=60):
    """Какое значение допущения key даёт result_key = target (остальное — базовое). Бисекция."""
    def val(x):
        return run("base", {key: x})[result_key][0] - target
    a, b = lo, hi
    fa, fb = val(a), val(b)
    if fa * fb > 0:
        return None
    for _ in range(steps):
        m = (a + b) / 2
        fm = val(m)
        if fa * fm <= 0:
            b, fb = m, fm
        else:
            a, fa = m, fm
    return (a + b) / 2


def payback(profit_by_q, invest_by_q):
    """Квартал, в котором накопленная прибыль после прямых расходов покрыла накопленные вложения (1..n)."""
    cp = ci = 0
    for i, (p, c) in enumerate(zip(profit_by_q, invest_by_q), 1):
        cp, ci = cp + p, ci + c
        if cp >= ci and ci > 0:
            return i
    return None


def _num(v):
    if isinstance(v, float) and not v.is_integer():
        return f"{v:,.3f}".replace(",", " ").replace(".", ",").rstrip("0").rstrip(",")
    return f"{int(round(v)):,}".replace(",", " ")


def main():
    base, weak = run("base"), run("weak")
    results = []
    for k, (v, unit, text, basis) in base.items():
        results.append({"key": k, "value": v, "unit": unit, "text": text, "basis": basis, "scenario": "базовый"})
    for k, (v, unit, text, basis) in weak.items():
        wb = [b + "_weak" if b in weak else b for b in basis]  # слабый результат опирается на слабые результаты
        results.append({"key": k + "_weak", "value": v, "unit": unit, "text": text, "basis": wb,
                        "scenario": "слабый"})
    tables = []
    rows = ["| Показатель | Базовый | Слабый | Единица |", "|---|---|---|---|"]
    for k, (v, unit, text, _b) in base.items():
        rows.append(f"| {text} | {_num(v)} | {_num(weak[k][0])} | {unit} |")
    tables.append({"title": "Сценарии", "markdown": "\n".join(rows)})
    if MAIN and MAIN in base:
        rows = ["| Допущение | Низкое → результат | Высокое → результат |", "|---|---|---|"]
        spread = []
        for k, (b0, lo, hi, unit, text, *_r) in ASSUMPTIONS.items():
            r_lo, r_hi = run("base", {k: lo})[MAIN][0], run("base", {k: hi})[MAIN][0]
            spread.append((abs(r_hi - r_lo), f"| {text} | {_num(lo)} → {_num(r_lo)} | {_num(hi)} → {_num(r_hi)} |"))
        rows += [r for _, r in sorted(spread, reverse=True)]
        tables.append({"title": f"Чувствительность: {base[MAIN][2]}", "markdown": "\n".join(rows)})
    model = {
        "facts": [{"key": k, "value": v[0], "unit": v[1], "text": v[2], "claim": v[3]} for k, v in FACTS.items()],
        "assumptions": [{"key": k, "value": v[0], "low": v[1], "high": v[2], "unit": v[3], "text": v[4],
                         "why": v[6], "check": v[7]} for k, v in ASSUMPTIONS.items()],
        "results": results,
        "tables": tables,
    }
    (HERE / "model.json").write_text(json.dumps(model, ensure_ascii=False, indent=1), encoding="utf-8")
    for t in tables:
        print(f"\n## {t['title']}\n\n{t['markdown']}")
    print("\nСохранено: analysis/model.json. Дальше: ./strat model sync")


if __name__ == "__main__":
    main()
