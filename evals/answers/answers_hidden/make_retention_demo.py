"""Генератор синтетического датасета для проверки навыка анализа данных.

ДАННЫЕ ВЫДУМАНЫ и нужны только для проверки агента: в них заложены известные причины изменения
удержания, поэтому можно измерить, находит ли их агент. Сид фиксирован — результат воспроизводим.

Запуск: python3 evals/answers/make_retention_demo.py  →  evals/data/retention_demo.csv
Скрипт лежит в evals/answers/, потому что его комментарии раскрывают ответы: папку убирают на время прогона.
Описание заложенных эффектов — в evals/answers/retention_demo.md (агенту при проверке не показывать).
"""
import csv
import datetime as dt
import random
from pathlib import Path

random.seed(20261109)
OUT = Path(__file__).resolve().parents[1] / "data" / "retention_demo.csv"

START = dt.date(2026, 1, 5)          # понедельник, первая когорта
WEEKS = 24                           # до середины июня
RELEASE = dt.date(2026, 4, 6)        # релиз нового онбординга Android
TRACKING_GAP = dt.date(2026, 5, 11)  # неделя без данных (сбой сбора)
REPORT_DATE = dt.date(2026, 6, 22)   # дата выгрузки: у свежих когорт D30 ещё нет

BASE = {  # базовое удержание по каналам (доли): D1, D7, D30
    "organic": (0.52, 0.34, 0.24),
    "partner": (0.47, 0.30, 0.21),
    "paid_social": (0.38, 0.20, 0.12),
}
PLATFORM_ADJ = {"android": 0.0, "ios": 0.03}


def channel_mix(week_start):
    """С марта растёт доля платного трафика из соцсетей (смена состава)."""
    if week_start < dt.date(2026, 3, 2):
        return {"organic": 0.55, "partner": 0.30, "paid_social": 0.15}
    if week_start < dt.date(2026, 4, 6):
        return {"organic": 0.45, "partner": 0.27, "paid_social": 0.28}
    return {"organic": 0.40, "partner": 0.25, "paid_social": 0.35}


rows = []
for w in range(WEEKS):
    week = START + dt.timedelta(weeks=w)
    if week == TRACKING_GAP:
        continue
    season = 1.0 + (0.04 if week.month in (1, 5) else 0.0)  # праздничные недели: больше установок
    for platform in ("android", "ios"):
        total = int((4200 if platform == "android" else 2300) * season * random.uniform(0.95, 1.05))
        for channel, share in channel_mix(week).items():
            n = max(50, int(total * share))
            d1, d7, d30 = BASE[channel]
            adj = PLATFORM_ADJ[platform]
            if platform == "android" and week >= RELEASE:
                d1 -= 0.06   # новый онбординг: часть людей бросает на первом экране
                d7 -= 0.05
                d30 -= 0.04
            r1 = sum(1 for _ in range(n) if random.random() < d1 + adj)
            r7 = min(r1, sum(1 for _ in range(n) if random.random() < d7 + adj))
            r30 = min(r7, sum(1 for _ in range(n) if random.random() < d30 + adj))
            d30_ready = week + dt.timedelta(days=30) <= REPORT_DATE
            rows.append([week.isoformat(), platform, channel, n, r1, r7, r30 if d30_ready else ""])

with open(OUT, "w", newline="", encoding="utf-8") as f:
    wr = csv.writer(f)
    wr.writerow(["cohort_week", "platform", "channel", "new_users", "retained_d1", "retained_d7", "retained_d30"])
    wr.writerows(rows)
print(f"Записано строк: {len(rows)} → {OUT}")
