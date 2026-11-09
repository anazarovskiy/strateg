"""Автоматическая часть рубрики сравнения: применяется к любому markdown-ответу (агента или обычного чата).

Это проверка структуры и проверяемости, а не качества мысли. Качество оценивает человек по evals/rubric.md.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .core import StratError

URL_RE = re.compile(r"https?://\S+")
REF_RE = re.compile(r"\[(?:[CS]-\d{3,})(?:\s*[,;]\s*[CS]-\d{3,})*\]|\[\d{1,3}\]|\(\s*источник|https?://")
NUM_RE = re.compile(r"\d[\d\s.,]*\s?(%|млн|млрд|тыс|руб|₽|\$|п\.п\.|раз)")


def _low(t):
    return t.lower().replace("ё", "е")


def check_text(text: str) -> dict:
    body = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    low = _low(body)
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n[-*]\s+|\n\d+\.\s+", body) if len(s.strip()) > 25]
    with_src = [s for s in sentences if REF_RE.search(s)]
    numeric = [s for s in sentences if NUM_RE.search(s)]
    numeric_src = [s for s in numeric if REF_RE.search(s)]
    options = re.findall(r"^#{1,4}\s*(вариант|опция|сценарий)\b.*$", body, flags=re.I | re.M)
    checks = {
        "доля предложений со ссылкой на источник": round(len(with_src) / len(sentences), 2) if sentences else 0,
        "доля чисел со ссылкой на источник": round(len(numeric_src) / len(numeric), 2) if numeric else None,
        "вариантов (заголовков)": len(options),
        "есть «оставить как есть»": any(w in low for w in ("статус-кво", "оставить как есть", "ничего не менять")),
        "есть «закрыть направление»": any(w in low for w in ("закрыть направление", "свернуть", "закрытие направ")),
        "есть сильные возражения": "возражени" in low or "контраргумент" in low,
        "факты отделены от гипотез (явные метки)": ("гипотез" in low and ("факт" in low or "что известно" in low)),
        "названы пробелы («чего не знаем»)": any(w in low for w in ("не знаем", "пробел", "нет данных", "неизвестно")),
        "продуктовая стратегия: аудитория и задача": "аудитори" in low and ("задач" in low or "jtbd" in low),
        "продуктовая стратегия: осознанные отказы": "отказ" in low or "не делаем" in low,
        "продуктовая стратегия: затраты и доходы": any(w in low for w in ("затрат", "доход", "выручк", "юнит-эконом",
                                                                         "arpu", "ltv", "cac")),
        "коммуникации: барьер": "барьер" in low,
        "коммуникации: ключевое сообщение": "ключевое сообщение" in low or "главное сообщение" in low,
        "коммуникации: каналы": "канал" in low,
        "коммуникации: обещания сверены с продуктом": "обещани" in low,
        "план проверки: с чем сравниваем": any(w in low for w in ("контрольн", "сравниваем", "базов")),
        "план проверки: срок наблюдения": "срок" in low or "недел" in low,
        "план проверки: нежелательные последствия": any(w in low for w in ("нежелательн", "побочн", "guardrail",
                                                                          "ограничительн")),
        "синтетические ответы помечены": ("синтет" in low) if "респондент" in low else None,
        "ссылок (URL)": len(URL_RE.findall(body)),
        "слов": len(body.split()),
    }
    bools = [v for k, v in checks.items() if isinstance(v, bool)]
    checks["выполнено структурных требований"] = f"{sum(bools)} из {len(bools)}"
    return checks


def cmd_eval(args):
    p = Path(args.file)
    if not p.exists():
        raise StratError(f"Нет файла {p}")
    res = check_text(p.read_text(encoding="utf-8", errors="replace"))
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0
    print(f"Автоматическая проверка: {p}")
    for k, v in res.items():
        if isinstance(v, bool):
            v = "да" if v else "нет"
        elif v is None:
            v = "—"
        print(f"  {k}: {v}")
    return 0
