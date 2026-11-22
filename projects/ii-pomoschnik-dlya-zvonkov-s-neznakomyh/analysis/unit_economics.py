"""Порядок стоимости ИИ-обработки звонков на одного подписчика в месяц.
Цены — из реестра (C-045..C-052, руб с НДС, 16.11.2026); допущения о нагрузке ниже — наши, не факты.
Запуск: python3 -I analysis/unit_economics.py
"""

# Цены
ASR_RUB_PER_MIN = {"sber": 0.6, "yandex_stream": 0.1626 * 4}      # C-049; C-045 (15 с -> 60 с)
TTS_RUB_PER_CHAR = {"sber": 0.000186, "yandex_v1": 1342 / 1e6}    # C-050; C-046
LLM_RUB_PER_1K = {"low": 0.065, "base": 0.5, "high": 1.2}         # GigaChat Lite / Pro (C-052); YandexGPT Pro 5 (C-048)

# Допущения о звонке (ASSUMPTION)
SCEN = {
    "low":  dict(asr_min=0.5, chars=250, tokens=1500, asr="yandex_stream", tts="yandex_v1", llm="low"),
    "base": dict(asr_min=1.0, chars=500, tokens=3000, asr="sber",          tts="sber",      llm="base"),
    "high": dict(asr_min=2.0, chars=900, tokens=6000, asr="sber",          tts="sber",      llm="high"),
}
CALLS_PER_MONTH = [20, 55, 100]  # 55 — вендорная оценка МТС (C-027), остальное — диапазон
PRICES = [99, 149, 199]          # диапазон конкурентов (C-031, C-032, C-006)

print("Стоимость одного звонка, руб (без телефонии и без накладных расходов):")
cost = {}
for name, s in SCEN.items():
    c = (s["asr_min"] * ASR_RUB_PER_MIN[s["asr"]]
         + s["chars"] * TTS_RUB_PER_CHAR[s["tts"]]
         + s["tokens"] / 1000 * LLM_RUB_PER_1K[s["llm"]])
    cost[name] = c
    print(f"  {name}: {c:.2f}")

print("\nИИ-затраты на подписчика в месяц, руб (звонков/мес -> low/base/high):")
for n in CALLS_PER_MONTH:
    print(f"  {n:>3}: " + " / ".join(f"{cost[k]*n:.0f}" for k in ("low", "base", "high")))

print("\nМаржа от ИИ-затрат при цене P (звонков 55, base), % :")
for p in PRICES:
    print(f"  {p} руб/мес: {(p - cost['base']*55)/p*100:.0f}%")
print("\nМаксимум звонков в месяц при base-стоимости и марже > 0:")
for p in PRICES:
    print(f"  {p} руб: {p/cost['base']:.0f} звонков")
