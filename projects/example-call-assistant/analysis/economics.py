"""Экономика платной версии для работы (вариант 3) на 100 тыс. активных пользователей бесплатной функции.

Входы — допущения из реестра: конверсия 1–3% [C-031], цена 199–299 руб. [C-032], переменные затраты 30% [C-033].
Запуск: python3 projects/example-call-assistant/analysis/economics.py
"""
from pathlib import Path

ACTIVE = 100_000
CONVERSION = [0.01, 0.02, 0.03]
PRICE = [199, 249, 299]
VAR_COST_SHARE = 0.30
FIXED_STEP = 10_000_000  # каждые 10 млн руб. постоянных затрат в год

rows = []
for conv, price, name in zip(CONVERSION, PRICE, ["осторожный", "базовый", "смелый"]):
    payers = ACTIVE * conv
    revenue_year = payers * price * 12
    margin_year = revenue_year * (1 - VAR_COST_SHARE)
    users_for_fixed = FIXED_STEP / (conv * price * 12 * (1 - VAR_COST_SHARE))
    rows.append((name, conv, price, payers, revenue_year, margin_year, users_for_fixed))

lines = [
    "# Экономика версии для работы (расчёт по допущениям)",
    "",
    "Вход: 100 000 активных пользователей бесплатной функции; конверсия, цена и доля переменных затрат — допущения "
    "C-031, C-032, C-033. Скрипт: analysis/economics.py.",
    "",
    "| Сценарий | Конверсия | Цена, руб./мес | Платящих | Выручка, млн руб./год | Маржинальный доход, млн руб./год | "
    "Активных пользователей на каждые 10 млн руб. постоянных затрат |",
    "|---|---|---|---|---|---|---|",
]
def num(x):
    return f"{x:,.0f}".replace(",", " ")


for name, conv, price, payers, rev, margin, need in rows:
    lines.append(f"| {name} | {conv:.0%} | {price} | {num(payers)} | {rev / 1e6:.1f} | {margin / 1e6:.1f} | {num(need)} |")
lines += ["", f"Итог: выручка от {rows[0][4] / 1e6:.1f} до {rows[2][4] / 1e6:.1f} млн руб. в год на каждые 100 тыс. "
          f"активных пользователей; чтобы покрыть 10 млн руб. постоянных затрат в год, нужно от {num(rows[2][6])} до "
          f"{num(rows[0][6])} активных пользователей бесплатной функции."]
text = "\n".join(lines) + "\n"
Path(__file__).with_name("economics.md").write_text(text, encoding="utf-8")
print(text)
