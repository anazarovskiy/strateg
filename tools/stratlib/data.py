"""Данные и медиа: профиль таблицы (типы, пропуски, периоды, единицы), кадры из видео, расшифровка, ПДн."""
from __future__ import annotations

import re
import shutil
import statistics
import subprocess
from collections import Counter
from pathlib import Path

from . import textio
from .core import StratError, get_project, journal, short, write_text

DATE_PATTERNS = [
    (re.compile(r"^\d{4}-\d{2}-\d{2}"), "%Y-%m-%d"),
    (re.compile(r"^\d{2}\.\d{2}\.\d{4}"), "%d.%m.%Y"),
    (re.compile(r"^\d{4}-\d{2}$"), "%Y-%m"),
    (re.compile(r"^\d{2}/\d{2}/\d{4}"), "%m/%d/%Y"),
]
MISSING = {"", "na", "n/a", "nan", "null", "none", "-", "—", "#н/д", "#n/a"}


def _num(s: str):
    t = s.strip().replace(" ", "").replace(" ", "")
    pct = t.endswith("%")
    t = t.rstrip("%")
    if re.match(r"^-?\d+(,\d+)?$", t):
        t = t.replace(",", ".")
    try:
        return float(t), pct
    except ValueError:
        return None, False


def _date(s: str):
    import datetime as dt
    for rx, fmt in DATE_PATTERNS:
        if rx.match(s.strip()):
            try:
                return dt.datetime.strptime(s.strip()[:len(dt.datetime.now().strftime(fmt))], fmt).date()
            except ValueError:
                return None
    return None


def profile_rows(rows, name="") -> str:
    rows = [r for r in rows if any(str(c).strip() for c in r)]
    if len(rows) < 2:
        return f"## {name}\n\nДанных нет или только заголовок."
    head = [str(h).strip() or f"col{i + 1}" for i, h in enumerate(rows[0])]
    body = rows[1:]
    width = max(len(head), max(len(r) for r in body))
    head += [f"col{i + 1}" for i in range(len(head), width)]
    out = [f"## {name}" if name else "## Таблица", "",
           f"Строк: {len(body)}, столбцов: {width}. Полных дубликатов строк: "
           f"{len(body) - len(set(tuple(r) for r in body))}.", "",
           "| Столбец | Тип | Пропуски | Уникальных | Диапазон / частые значения | Замечания |", "|---|---|---|---|---|---|"]
    warnings = []
    for j in range(width):
        col = [str(r[j]).strip() if j < len(r) and r[j] is not None else "" for r in body]
        miss = sum(1 for v in col if v.lower() in MISSING)
        vals = [v for v in col if v.lower() not in MISSING]
        nums, pcts, dates = [], 0, []
        for v in vals:
            n, p = _num(v)
            if n is not None:
                nums.append(n)
                pcts += p
                continue
            d = _date(v)
            if d:
                dates.append(d)
        notes = []
        if vals and len(nums) >= 0.9 * len(vals):
            typ = "число"
            rng = f"{min(nums):g} … {max(nums):g}, медиана {statistics.median(nums):g}"
            if pcts and pcts < len(nums):
                notes.append("смешаны значения с % и без — проверьте единицы")
            if nums and max(nums) <= 1 and min(nums) >= 0 and len(set(nums)) > 2 and "%" not in head[j]:
                notes.append("значения 0…1 — доли или проценты?")
            if any(n < 0 for n in nums):
                notes.append("есть отрицательные")
            if len(nums) > 8:
                q = statistics.quantiles(nums, n=4)
                iqr = q[2] - q[0]
                outl = [n for n in nums if iqr and (n < q[0] - 3 * iqr or n > q[2] + 3 * iqr)]
                if outl:
                    notes.append(f"выбросы: {len(outl)}")
        elif vals and len(dates) >= 0.9 * len(vals):
            typ = "дата"
            ds = sorted(dates)
            rng = f"{ds[0]} … {ds[-1]}"
            uniq = sorted(set(ds))
            if len(uniq) > 3:
                diffs = [(b - a).days for a, b in zip(uniq, uniq[1:])]
                step = statistics.median(diffs)
                if step in (1, 7):
                    import datetime as _dt
                    holes = []
                    for a, b in zip(uniq, uniq[1:]):
                        k = 1
                        while (b - a).days > step * k:
                            holes.append(str(a + _dt.timedelta(days=int(step * k))))
                            k += 1
                    if holes:
                        unit = "дни" if step == 1 else "недели"
                        notes.append(f"пропущены {unit}: " + ", ".join(holes[:6]) + ("…" if len(holes) > 6 else ""))
            months = sorted({(d.year, d.month) for d in ds})
            gaps = []
            y, m = months[0]
            while (y, m) < months[-1]:
                m += 1
                if m > 12:
                    y, m = y + 1, 1
                if (y, m) not in months:
                    gaps.append(f"{y}-{m:02d}")
            if gaps:
                notes.append("нет данных за месяцы: " + ", ".join(gaps[:6]) + ("…" if len(gaps) > 6 else ""))
        else:
            typ = "текст" if vals else "пусто"
            top = Counter(vals).most_common(3)
            rng = "; ".join(f"{short(k, 24)} ({v})" for k, v in top)
            mixed = sum(1 for v in vals if _num(v)[0] is not None)
            if vals and 0.1 * len(vals) < mixed < 0.9 * len(vals):
                notes.append("смешаны числа и текст")
        if miss:
            share = miss / len(col)
            if share > 0.2:
                notes.append(f"пропусков {share:.0%}")
        out.append(f"| {head[j]} | {typ} | {miss} | {len(set(vals))} | {rng} | {'; '.join(notes)} |")
        if notes:
            warnings.append(f"{head[j]}: {'; '.join(notes)}")
    if warnings:
        out += ["", "Что проверить перед расчётами:"] + [f"- {w}" for w in warnings]
    return "\n".join(out)


def cmd_profile(args):
    path = Path(args.file)
    if not path.exists():
        raise StratError(f"Нет файла {path}")
    ext = path.suffix.lower()
    parts = []
    if ext in (".csv", ".tsv", ".txt"):
        parts.append(profile_rows(textio.csv_rows(path), path.name))
    elif ext in (".xlsx", ".xlsm"):
        for name, rows in textio.xlsx_sheets(path).items():
            if args.sheet and name != args.sheet:
                continue
            parts.append(profile_rows(rows, f"{path.name} — лист «{name}»"))
    else:
        raise StratError("Профиль строится для CSV/TSV и XLSX.")
    text = "\n\n".join(parts)
    print(text)
    if args.out:
        write_text(args.out, f"# Профиль данных: {path.name}\n\n" + text + "\n")
        print(f"\nСохранено: {args.out}")
    try:
        pr = get_project(args.project)
        journal(pr, "data_profile", {"file": str(path)})
    except StratError:
        pass
    return 0


def cmd_frames(args):
    exe = shutil.which("ffmpeg")
    if not exe:
        raise StratError("ffmpeg не установлен (macOS: brew install ffmpeg). Без него кадры не извлечь: "
                         "сделайте скриншоты нужных моментов вручную и добавьте их как изображения.", code=6)
    src = Path(args.video)
    if not src.exists():
        raise StratError(f"Нет файла {src}")
    pr = get_project(args.project)
    out = pr.dir / "sources" / "media" / (args.name or src.stem)
    out.mkdir(parents=True, exist_ok=True)
    cmd = [exe, "-hide_banner", "-loglevel", "error", "-i", str(src), "-vf", f"fps=1/{args.every},scale=1280:-2",
           "-frames:v", str(args.max), str(out / "frame_%04d.jpg")]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise StratError("ffmpeg завершился с ошибкой: " + short(r.stderr, 300))
    frames = sorted(out.glob("frame_*.jpg"))
    journal(pr, "media_frames", {"video": str(src), "frames": len(frames), "every_s": args.every})
    print(f"Кадров: {len(frames)} (каждые {args.every} с) в {pr.rel(out)}. Кадр N соответствует времени "
          f"≈ (N−1)×{args.every} с. Смотрите кадры через Read; наблюдения заносите с --loc \"мм:сс\".")
    return 0


def cmd_transcribe(args):
    exe = shutil.which("whisper")
    src = Path(args.audio)
    if not src.exists():
        raise StratError(f"Нет файла {src}")
    if not exe:
        raise StratError("Локальная расшифровка недоступна: не найден whisper (pip install openai-whisper). "
                         "Добавьте готовую расшифровку файлом .txt/.srt/.vtt — агент работает с ней.", code=6)
    pr = get_project(args.project)
    out = pr.dir / "interviews" / "real"
    r = subprocess.run([exe, str(src), "--language", args.language, "--output_format", "srt", "--output_dir", str(out)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise StratError("whisper завершился с ошибкой: " + short(r.stderr, 300))
    srt = out / (src.stem + ".srt")
    text = srt.read_text(encoding="utf-8", errors="replace") if srt.exists() else ""
    hits = textio.pii_scan(text)
    if hits:
        srt.write_text(textio.pii_redact(text), encoding="utf-8")
    journal(pr, "transcribe", {"audio": str(src), "pii_redacted": len(hits)})
    print(f"Расшифровка: {pr.rel(srt)}" + (f" (обезличено фрагментов: {len(hits)})" if hits else "") +
          ". Зарегистрируйте её: ./strat source add <файл> --kind interview")
    return 0


def cmd_pii(args):
    p = Path(args.file)
    if not p.exists():
        raise StratError(f"Нет файла {p}")
    text = p.read_text(encoding="utf-8", errors="replace")
    hits = textio.pii_scan(text)
    if args.action == "scan":
        if not hits:
            print("Персональные данные по шаблонам не найдены (имена и адреса шаблоны не ловят — проверьте глазами).")
            return 0
        for h in hits:
            print(f"строка {h['line']}: {h['type']} {h['masked']}")
        print(f"Найдено: {len(hits)}. Обезличить: ./strat pii redact {p} --out <файл>")
        return 2
    out = Path(args.out) if args.out else p.with_name(p.stem + ".redacted" + p.suffix)
    out.write_text(textio.pii_redact(text), encoding="utf-8")
    print(f"Обезличено фрагментов: {len(hits)} → {out}. Имена людей замените вручную.")
    return 0
