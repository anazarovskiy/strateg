"""Извлечение текста из файлов и страниц, сверка цитат, поиск инъекций и персональных данных.

Только стандартная библиотека Python. Если установлены openpyxl, pypdf или pdftotext, они используются
для более точного извлечения, но не обязательны.
"""
from __future__ import annotations

import csv
import io
import re
import shutil
import subprocess
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree as ET

# ---------- нормализация и сверка цитат ----------

_DASHES = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"
_QUOTES = "«»„“”‟\"'‘’‚‛`´"
_SPACES = "\u00a0\u2002\u2003\u2007\u2009\u200a\u202f\u205f\u3000"


def norm(text: str) -> str:
    t = (text or "").lower().replace("ё", "е").replace("\u00ad", "")
    t = t.replace("\u200b", "").replace("\ufeff", "")
    for ch in _DASHES:
        t = t.replace(ch, "-")
    for ch in _QUOTES:
        t = t.replace(ch, "")
    for ch in _SPACES:
        t = t.replace(ch, " ")
    return re.sub(r"\s+", " ", t).strip()


def loose(text: str) -> str:
    t = norm(text)
    t = re.sub(r"[^\w\s%]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def verify_quote(quote: str, text: str) -> dict:
    """Ищет цитату в тексте снимка.

    status: exact — найдена дословно (с точностью до регистра, кавычек и пробелов);
            close — совпадает не меньше 80% словесных триграмм (мелкие расхождения);
            not_found — не найдена; no_text — у источника нет текстового снимка.
    """
    if not text or not text.strip():
        return {"status": "no_text", "score": 0.0, "snippet": ""}
    if not quote or not quote.strip():
        return {"status": "not_found", "score": 0.0, "snippet": ""}
    nq, nt = norm(quote), norm(text)
    pos = nt.find(nq)
    if pos >= 0:
        return {"status": "exact", "score": 1.0, "snippet": nt[max(0, pos - 60): pos + len(nq) + 60]}
    lq, lt = loose(quote), loose(text)
    pos = lt.find(lq)
    if lq and pos >= 0:
        return {"status": "exact", "score": 1.0, "snippet": lt[max(0, pos - 60): pos + len(lq) + 60]}
    qt, tt = lq.split(), lt.split()
    if len(qt) < 3:
        return {"status": "not_found", "score": 0.0, "snippet": _best_window(qt, tt)}
    qg = list(zip(qt, qt[1:], qt[2:]))
    first = {}
    for i, g in enumerate(zip(tt, tt[1:], tt[2:])):
        first.setdefault(g, i)
    hits = [g for g in qg if g in first]
    score = len(hits) / len(qg)
    snippet = ""
    if hits:
        i = first[hits[0]]
        snippet = " ".join(tt[max(0, i - 8): i + len(qt) + 8])
    else:
        snippet = _best_window(qt, tt)
    return {"status": "close" if score >= 0.8 else "not_found", "score": round(score, 2), "snippet": snippet}


def _best_window(qt, tt) -> str:
    """Самый похожий фрагмент по числу общих слов (для подсказки при несовпадении)."""
    if not qt or not tt:
        return ""
    qs = set(w for w in qt if len(w) > 3) or set(qt)
    n = max(len(qt), 6)
    best, best_i = 0, 0
    step = max(1, n // 3)
    for i in range(0, max(1, len(tt) - n + 1), step):
        sc = len(qs.intersection(tt[i:i + n]))
        if sc > best:
            best, best_i = sc, i
    if best == 0:
        return ""
    return " ".join(tt[max(0, best_i - 4): best_i + n + 4])


# ---------- HTML ----------

class _HTMLText(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "template", "iframe", "canvas", "object", "form", "button",
            "select", "nav"}
    BLOCK = {"p", "div", "section", "article", "main", "header", "footer", "aside", "li", "ul", "ol", "table",
             "tr", "blockquote", "pre", "figure", "figcaption", "dl", "dt", "dd", "br", "hr", "h1", "h2", "h3",
             "h4", "h5", "h6"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip = 0
        self.title = ""
        self._in_title = False
        self.meta = {}
        self._cell = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in self.SKIP:
            self.skip += 1
            return
        if tag == "title":
            self._in_title = True
        if tag == "meta":
            key = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
            if key in ("description", "og:description", "article:published_time", "datepublished",
                       "og:title", "og:site_name", "author", "pubdate", "date"):
                self.meta[key] = a.get("content", "")
        if tag == "link" and (a.get("rel") or "").lower() == "canonical":
            self.meta["canonical"] = a.get("href", "")
        if self.skip:
            return
        if tag in ("h1", "h2", "h3", "h4"):
            self.parts.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in ("td", "th"):
            self.parts.append(" | ")
        elif tag == "br":
            self.parts.append("\n")
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if tag == "title":
            self._in_title = False
        if self.skip:
            return
        if tag in self.BLOCK or tag in ("td", "th"):
            self.parts.append("\n" if tag not in ("td", "th") else "")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
            return
        if self.skip:
            return
        self.parts.append(data)


def html_to_text(html: str):
    p = _HTMLText()
    try:
        p.feed(html)
        p.close()
    except Exception:  # noqa: BLE001 — битый HTML не должен ронять сбор
        pass
    text = "".join(p.parts)
    lines = []
    for line in text.splitlines():
        line = re.sub(r"[ \t\r\f\v]+", " ", line).strip()
        line = re.sub(r"^\|\s*", "| ", line)
        lines.append(line)
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return p.title.strip(), text, p.meta


def sniff_charset(data: bytes, header_charset=None) -> str:
    if header_charset:
        return header_charset
    m = re.search(rb"charset=[\"']?([A-Za-z0-9_\-]+)", data[:4096])
    if m:
        return m.group(1).decode("ascii", "ignore")
    try:
        data.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return "cp1251"


# ---------- офисные форматы ----------

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _w_par_text(p) -> str:
    out = []
    for node in p.iter():
        if node.tag == W + "t":
            out.append(node.text or "")
        elif node.tag == W + "tab":
            out.append("\t")
        elif node.tag in (W + "br", W + "cr"):
            out.append("\n")
    return "".join(out).strip()


def docx_to_text(path) -> str:
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    body = root.find(W + "body")
    lines = []
    if body is None:
        return ""
    for el in body:
        if el.tag == W + "p":
            txt = _w_par_text(el)
            if not txt:
                continue
            style = el.find(f"{W}pPr/{W}pStyle")
            sval = (style.get(W + "val") if style is not None else "") or ""
            m = re.search(r"(heading|заголовок)\s*(\d)", sval, re.I)
            if m:
                lines.append("\n" + "#" * min(4, int(m.group(2))) + " " + txt)
            elif el.find(f"{W}pPr/{W}numPr") is not None:
                lines.append("- " + txt)
            else:
                lines.append(txt)
        elif el.tag == W + "tbl":
            rows = []
            for tr in el.iter(W + "tr"):
                cells = []
                for tc in tr.findall(W + "tc"):
                    cells.append(" ".join(_w_par_text(p) for p in tc.iter(W + "p")).strip())
                rows.append(cells)
            if rows:
                lines.append(rows_to_markdown(rows))
    return "\n".join(lines).strip()


def pptx_to_text(path) -> str:
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)]
        names.sort(key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[1]).group(1)))
        out = []
        for i, n in enumerate(names, 1):
            root = ET.fromstring(z.read(n))
            paras = []
            for p in root.iter(A + "p"):
                t = "".join((r.text or "") for r in p.iter(A + "t")).strip()
                if t:
                    paras.append(t)
            out.append(f"\n## Слайд {i}\n" + "\n".join(paras))
    return "\n".join(out).strip()


def _col_index(ref: str) -> int:
    letters = re.match(r"[A-Z]+", ref or "A")
    n = 0
    for ch in (letters.group(0) if letters else "A"):
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def xlsx_sheets(path, max_rows=None) -> dict:
    """Возвращает {имя листа: [[ячейки], ...]}. Сначала пробует openpyxl, затем разбирает XML сам."""
    try:
        import openpyxl  # type: ignore

        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        out = {}
        for ws in wb.worksheets:
            rows = []
            for i, row in enumerate(ws.iter_rows(values_only=True)):
                if max_rows and i >= max_rows:
                    break
                rows.append(["" if v is None else str(v) for v in row])
            out[ws.title] = _trim_rows(rows)
        return out
    except ImportError:
        pass
    except Exception:  # noqa: BLE001 — перейдём на ручной разбор
        pass
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            sroot = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in sroot.findall(S + "si"):
                shared.append("".join(t.text or "" for t in si.iter(S + "t")))
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        rel_map = {r.get("Id"): r.get("Target") for r in rels.findall(PKG_REL + "Relationship")}
        out = {}
        sheets_el = wb.find(S + "sheets")
        for sh in (sheets_el if sheets_el is not None else []):
            name = sh.get("name")
            target = rel_map.get(sh.get(R_NS + "id"), "")
            target = target.lstrip("/")
            if not target.startswith("xl/"):
                target = "xl/" + target
            if target not in z.namelist():
                continue
            root = ET.fromstring(z.read(target))
            rows = []
            for i, row in enumerate(root.iter(S + "row")):
                if max_rows and i >= max_rows:
                    break
                cells = {}
                for c in row.findall(S + "c"):
                    t = c.get("t")
                    v = c.find(S + "v")
                    val = v.text if v is not None else ""
                    if t == "s" and val:
                        try:
                            val = shared[int(val)]
                        except (ValueError, IndexError):
                            pass
                    elif t == "inlineStr":
                        val = "".join(x.text or "" for x in c.iter(S + "t"))
                    elif t == "b":
                        val = "TRUE" if val == "1" else "FALSE"
                    cells[_col_index(c.get("r", "A"))] = val or ""
                if cells:
                    width = max(cells) + 1
                    rows.append([cells.get(j, "") for j in range(width)])
                else:
                    rows.append([])
            out[name] = _trim_rows(rows)
        return out


def _trim_rows(rows):
    while rows and not any(str(x).strip() for x in rows[-1]):
        rows.pop()
    return rows


def csv_rows(path, max_rows=None) -> list:
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "cp1251", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    sample = text[:20000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        class _Semi(csv.excel):
            delimiter = ";"
        dialect = _Semi if sample.count(";") > sample.count(",") else csv.excel
    rows = []
    for i, row in enumerate(csv.reader(io.StringIO(text), dialect)):
        if max_rows and i >= max_rows:
            break
        rows.append(row)
    return rows


def rows_to_markdown(rows, max_rows=200) -> str:
    rows = [r for r in rows if any(str(c).strip() for c in r)]
    if not rows:
        return ""
    width = max(len(r) for r in rows)

    def cell(x):
        return str(x).replace("|", "\\|").replace("\n", " ").strip()

    head = [cell(x) for x in rows[0]] + [""] * (width - len(rows[0]))
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * width]
    for r in rows[1:max_rows + 1]:
        r = [cell(x) for x in r] + [""] * (width - len(r))
        out.append("| " + " | ".join(r) + " |")
    if len(rows) - 1 > max_rows:
        out.append(f"\n… показано {max_rows} строк из {len(rows) - 1}; полный файл лежит в проекте.")
    return "\n".join(out)


def pdf_to_text(path):
    exe = shutil.which("pdftotext")
    if exe:
        try:
            r = subprocess.run([exe, "-layout", "-enc", "UTF-8", str(path), "-"], capture_output=True, timeout=120)
            if r.returncode == 0:
                return r.stdout.decode("utf-8", errors="replace")
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        import pypdf  # type: ignore

        reader = pypdf.PdfReader(str(path))
        parts = []
        for i, page in enumerate(reader.pages, 1):
            parts.append(f"\n[стр. {i}]\n" + (page.extract_text() or ""))
        return "\n".join(parts)
    except Exception:  # noqa: BLE001
        return None


def subtitles_to_text(path) -> str:
    out = []
    ts = None
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        m = re.match(r"(\d{1,2}:\d{2}:\d{2})[.,]\d{3}\s*-->", line) or re.match(r"(\d{1,2}:\d{2})\.\d{3}\s*-->", line)
        if m:
            ts = m.group(1)
            continue
        if not line or line.isdigit() or line.upper().startswith("WEBVTT"):
            continue
        out.append(f"[{ts}] {line}" if ts else line)
    return "\n".join(out)


IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".heic"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".opus"}
VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".avi"}


def extract_text(path):
    """Возвращает (text, kind, verbatim, note). verbatim=False, если дословного текста нет."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext in (".md", ".txt", ".markdown", ".rst"):
        return p.read_text(encoding="utf-8", errors="replace"), "doc", True, ""
    if ext in (".csv", ".tsv"):
        rows = csv_rows(p)
        return rows_to_markdown(rows), "table", True, f"Строк данных: {max(0, len(rows) - 1)}."
    if ext in (".xlsx", ".xlsm"):
        sheets = xlsx_sheets(p)
        parts = []
        for name, rows in sheets.items():
            parts.append(f"## Лист «{name}»\n\n" + rows_to_markdown(rows))
        return "\n\n".join(parts), "table", True, "Даты в Excel могут выглядеть как числа (серийный номер дня)."
    if ext == ".docx":
        return docx_to_text(p), "doc", True, ""
    if ext == ".pptx":
        return pptx_to_text(p), "doc", True, ""
    if ext == ".pdf":
        text = pdf_to_text(p)
        if text and text.strip():
            return text, "pdf", True, ""
        return "", "pdf", False, ("Текст PDF не извлечён (нет pdftotext/pypdf или это скан). "
                                   "Прочитайте файл через Read и фиксируйте сведения с указанием страницы.")
    if ext in (".html", ".htm"):
        title, text, _ = html_to_text(p.read_text(encoding="utf-8", errors="replace"))
        if title and title not in text[:200]:
            text = f"# {title}\n\n{text}"
        return text, "web", True, ""
    if ext in (".srt", ".vtt"):
        return subtitles_to_text(p), "interview", True, "Расшифровка с метками времени."
    if ext == ".json":
        return p.read_text(encoding="utf-8", errors="replace"), "data", True, ""
    if ext in IMAGE_EXT:
        return "", "image", False, ("Изображение: текстового снимка нет. Смотрите файл через Read, наблюдения "
                                    "заносите как observation с --loc (экран, область).")
    if ext in AUDIO_EXT:
        return "", "audio", False, ("Аудио: нужна расшифровка. ./strat media transcribe, если установлен whisper, "
                                    "или добавьте готовую расшифровку (.txt/.srt/.vtt).")
    if ext in VIDEO_EXT:
        return "", "video", False, ("Видео: извлеките кадры ./strat media frames и смотрите их через Read; "
                                    "звук — через расшифровку.")
    try:
        return p.read_text(encoding="utf-8"), "doc", True, ""
    except UnicodeDecodeError:
        return "", "file", False, "Двоичный файл неизвестного формата."


# ---------- инъекции и персональные данные ----------

INJECTION_PATTERNS = [
    r"ignore (all |any )?(the )?(previous|prior|above|earlier) (instructions|prompts|messages)",
    r"disregard (all |any )?(the )?(previous|prior|above) (instructions|rules)",
    r"(reveal|print|show) (your|the) (system prompt|instructions)",
    r"you are now (a|an|the) ",
    r"new instructions?:",
    r"do not (tell|inform|alert) the user",
    r"as an ai (agent|assistant),? you (must|should)",
    r"<\s*/?\s*(system|assistant|instructions?)\s*>",
    r"(игнорируй|проигнорируй|забудь)\s+(все\s+)?(предыдущие|прошлые|прежние|свои|системные)?\s*(инструкции|указания|правила)",
    r"(не\s+(сообщай|говори|показывай)\s+(это\s+)?пользователю)",
    r"(ты|вы)\s+теперь\s+(—\s*)?(другой|новый|являешься|работаешь)",
    r"системн(ый|ого|ые)\s+промпт",
    r"(новые|следующие)\s+инструкции\s+для\s+(ии|ai|модели|ассистента|агента)",
    r"(выполни|запусти)\s+(команду|скрипт)\s*[:`]",
    r"(отправь|перешли)\s+.{0,40}(ключ|токен|пароль|api)",
]
_INJ = [re.compile(p, re.I) for p in INJECTION_PATTERNS]


def injection_scan(text: str, limit=5) -> list:
    found = []
    if not text:
        return found
    for rx in _INJ:
        for m in rx.finditer(text):
            s = max(0, m.start() - 40)
            found.append(re.sub(r"\s+", " ", text[s: m.end() + 60]).strip())
            if len(found) >= limit:
                return found
    return found


_PII = [
    ("email", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("phone", re.compile(r"(?<!\d)(?:\+7|8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}(?!\d)")),
    ("snils", re.compile(r"(?<!\d)\d{3}-\d{3}-\d{3}[\s-]\d{2}(?!\d)")),
    ("passport", re.compile(r"(?i)паспорт\w*[^\n]{0,20}?(?<!\d)\d{2}\s?\d{2}\s?\d{6}(?!\d)")),
    ("card", re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")),
]


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for d in reversed(digits):
        n = int(d)
        if alt:
            n *= 2
            if n > 9:
                n -= 9
        total += n
        alt = not alt
    return total % 10 == 0


def pii_scan(text: str) -> list:
    hits = []
    for ln, line in enumerate((text or "").splitlines(), 1):
        for kind, rx in _PII:
            for m in rx.finditer(line):
                val = m.group(0)
                if kind == "card":
                    digits = re.sub(r"\D", "", val)
                    if not (13 <= len(digits) <= 19 and _luhn(digits)):
                        continue
                masked = val[:2] + "…" + val[-2:] if len(val) > 5 else "…"
                hits.append({"type": kind, "line": ln, "masked": masked})
    return hits


def pii_redact(text: str) -> str:
    labels = {"email": "[EMAIL]", "phone": "[ТЕЛЕФОН]", "snils": "[СНИЛС]", "passport": "[ПАСПОРТ]", "card": "[КАРТА]"}
    out = text or ""
    for kind, rx in _PII:
        if kind == "card":
            out = rx.sub(lambda m: labels["card"] if _luhn(re.sub(r"\D", "", m.group(0))) and
                         13 <= len(re.sub(r"\D", "", m.group(0))) <= 19 else m.group(0), out)
        else:
            out = rx.sub(labels[kind], out)
    return out


# ---------- поиск: токены и стемминг ----------

_RU_ENDINGS = sorted([
    "иями", "ями", "ами", "ость", "ости", "остью", "ений", "ение", "ения", "ением", "ании", "ание", "ания",
    "ого", "его", "ому", "ему", "ыми", "ими", "ая", "яя", "ое", "ее", "ой", "ей", "ий", "ый", "ые", "ие",
    "ом", "ем", "ам", "ям", "ах", "ях", "ов", "ев", "ую", "юю", "ть", "ться", "ся", "ет", "ут", "ют", "ит",
    "ат", "ят", "ы", "и", "а", "я", "о", "е", "у", "ю", "ь",
], key=len, reverse=True)


def stem(word: str) -> str:
    w = word.lower().replace("ё", "е")
    if re.match(r"^[a-z]+$", w):
        for suf in ("ings", "ing", "edly", "ed", "ies", "es", "s"):
            if w.endswith(suf) and len(w) - len(suf) >= 4:
                return w[: -len(suf)]
        return w
    for suf in _RU_ENDINGS:
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: -len(suf)]
    return w


def tokens(text: str) -> list:
    return [stem(t) for t in re.findall(r"[\wёЁ%]+", (text or "").lower()) if len(t) > 1]
