"""Общие функции: пути, время, файлы, блокировки, идентификаторы, журнал."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# Папку проектов можно переопределить (нужно для тестов и для хранения проектов отдельно от агента)
PROJECTS = Path(os.environ.get("STRAT_PROJECTS_DIR") or (ROOT / "projects")).resolve()
CONFIG = ROOT / "config"
TEMPLATES = ROOT / "templates"
ROOT_LOG = ROOT / "log"

if sys.version_info < (3, 9):  # pragma: no cover
    sys.stderr.write("Нужен Python 3.9 или новее.\n")
    sys.exit(127)


class StratError(Exception):
    """Ошибка, о которой нужно понятно сообщить пользователю или агенту."""

    def __init__(self, msg: str, code: int = 1):
        super().__init__(msg)
        self.code = code


# ---------- время ----------

def now() -> dt.datetime:
    return dt.datetime.now().astimezone()


def now_iso() -> str:
    return now().isoformat(timespec="seconds")


def today() -> dt.date:
    return dt.date.today()


def parse_dt(s):
    if not s:
        return None
    s = str(s).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        d = dt.datetime.fromisoformat(s)
    except ValueError:
        d = None
        for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
            try:
                d = dt.datetime.strptime(s[:10], fmt)
                break
            except ValueError:
                continue
        if d is None:
            return None
    if d.tzinfo is None:
        d = d.astimezone()
    return d


def parse_date(s):
    """YYYY-MM-DD, DD.MM.YYYY, YYYY-MM или YYYY -> date (неполные даты берутся по началу периода)."""
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y-%m", "%Y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    d = parse_dt(s)
    return d.date() if d else None


def fmt_dt(iso) -> str:
    d = parse_dt(iso)
    if not d:
        return "—"
    return d.strftime("%d.%m %H:%M")


def human_duration(seconds) -> str:
    seconds = int(max(0, seconds or 0))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h} ч {m} мин"
    if m:
        return f"{m} мин"
    return f"{s} с"


# ---------- файлы ----------

def read_text(path, default=""):
    p = Path(path)
    if not p.exists():
        return default
    return p.read_text(encoding="utf-8", errors="replace")


def write_text(path, text: str):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + f".tmp{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)


def load_json(path, default=None):
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def save_json(path, data):
    write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def read_jsonl(path) -> list:
    p = Path(path)
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def write_jsonl(path, rows):
    write_text(path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


def append_jsonl(path, row):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


class FileLock:
    """Простая межпроцессная блокировка через файл (работает на macOS, Linux и Windows).

    Нужна, потому что несколько субагентов могут одновременно добавлять записи в реестры.
    """

    def __init__(self, path, timeout=15.0, stale=60.0):
        self.path = str(path) + ".lock"
        self.timeout = timeout
        self.stale = stale
        self.fd = None

    def __enter__(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        start = time.time()
        while True:
            try:
                self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.fd, str(os.getpid()).encode())
                return self
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(self.path) > self.stale:
                        os.remove(self.path)
                        continue
                except OSError:
                    pass
                if time.time() - start > self.timeout:
                    raise StratError(f"Не удалось получить блокировку {self.path}. "
                                     "Если никакая команда strat сейчас не выполняется, удалите этот файл.")
                time.sleep(0.05)

    def __exit__(self, *exc):
        try:
            if self.fd is not None:
                os.close(self.fd)
            os.remove(self.path)
        except OSError:
            pass


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


# ---------- идентификаторы и имена ----------

_TRANSLIT = dict(zip(
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t",
     "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"],
))


def slugify(text: str, maxlen: int = 40) -> str:
    out = []
    for ch in text.lower():
        out.append(_TRANSLIT.get(ch, ch))
    s = re.sub(r"[^a-z0-9]+", "-", "".join(out)).strip("-")
    s = s[:maxlen].rstrip("-")
    return s or "project"


ID_RE = re.compile(r"^([A-Z])-(\d{3,})$")


def next_id(rows, prefix: str) -> str:
    mx = 0
    for r in rows:
        m = ID_RE.match(str(r.get("id", "")))
        if m and m.group(1) == prefix:
            mx = max(mx, int(m.group(2)))
    return f"{prefix}-{mx + 1:03d}"


def split_ids(value) -> list:
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        items = []
        for v in value:
            items.extend(split_ids(v))
        return items
    return [x.strip().upper() for x in re.split(r"[,;\s]+", str(value)) if x.strip()]


def split_list(value) -> list:
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        out = []
        for v in value:
            out.extend(split_list(v))
        return out
    return [x.strip() for x in re.split(r"[|;]", str(value)) if x.strip()]


# ---------- проекты ----------

class Project:
    def __init__(self, slug: str):
        self.slug = slug
        self.dir = PROJECTS / slug

    # пути
    @property
    def state_path(self):
        return self.dir / "state.json"

    @property
    def sources_path(self):
        return self.dir / "sources" / "registry.jsonl"

    @property
    def snapshots(self):
        return self.dir / "sources" / "snapshots"

    @property
    def files_dir(self):
        return self.dir / "sources" / "files"

    @property
    def claims_path(self):
        return self.dir / "evidence" / "claims.jsonl"

    @property
    def decisions_path(self):
        return self.dir / "decisions" / "registry.jsonl"

    @property
    def commitments_path(self):
        return self.dir / "decisions" / "commitments.jsonl"

    @property
    def journal_path(self):
        return self.dir / "log" / "journal.jsonl"

    @property
    def costs_path(self):
        return self.dir / "log" / "costs.jsonl"

    @property
    def inputs(self):
        return self.dir / "inputs"

    def rel(self, path) -> str:
        try:
            return str(Path(path).resolve().relative_to(self.dir.resolve())).replace(os.sep, "/")
        except ValueError:
            return str(path)

    def exists(self) -> bool:
        return self.state_path.exists()

    # данные
    def state(self) -> dict:
        return load_json(self.state_path, {}) or {}

    def save_state(self, st: dict):
        st["updated"] = now_iso()
        save_json(self.state_path, st)

    def sources(self) -> list:
        return read_jsonl(self.sources_path)

    def claims(self) -> list:
        return read_jsonl(self.claims_path)

    def decisions(self) -> list:
        return read_jsonl(self.decisions_path)

    def commitments(self) -> list:
        return read_jsonl(self.commitments_path)

    def source_map(self) -> dict:
        return {s["id"]: s for s in self.sources()}

    def claim_map(self) -> dict:
        return {c["id"]: c for c in self.claims()}


def active_slug():
    env = os.environ.get("STRAT_PROJECT")
    if env:
        return env.strip()
    p = PROJECTS / ".active"
    if p.exists():
        s = p.read_text(encoding="utf-8").strip()
        return s or None
    return None


def set_active(slug: str):
    PROJECTS.mkdir(parents=True, exist_ok=True)
    (PROJECTS / ".active").write_text(slug + "\n", encoding="utf-8")


def get_project(slug=None, must_exist=True):
    slug = slug or active_slug()
    if not slug:
        if must_exist:
            raise StratError("Нет активного проекта. Создайте: ./strat init \"Название задачи\" "
                             "или выберите: ./strat use <проект> (список: ./strat list).")
        return None
    pr = Project(slug)
    if must_exist and not pr.exists():
        raise StratError(f"Проект «{slug}» не найден в папке projects/. Список: ./strat list.")
    return pr


def list_projects() -> list:
    if not PROJECTS.exists():
        return []
    out = []
    for d in sorted(PROJECTS.iterdir()):
        if d.is_dir() and (d / "state.json").exists():
            out.append(Project(d.name))
    return out


# ---------- журнал ----------

_HOME = os.path.expanduser("~")


def _scrub_paths(obj):
    """Журнал уходит в репозиторий: абсолютные пути к папке агента и к домашней папке заменяем короткими."""
    if isinstance(obj, str):
        return obj.replace(str(ROOT) + os.sep, "").replace(str(ROOT), ".").replace(_HOME, "~") \
            if _HOME not in ("/", "") else obj.replace(str(ROOT) + os.sep, "")
    if isinstance(obj, dict):
        return {k: _scrub_paths(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub_paths(v) for v in obj]
    return obj


def journal(project, kind: str, detail, actor: str = None):
    """Запись в журнал действий проекта (или в общий журнал, если проекта нет)."""
    actor = actor or os.environ.get("STRAT_ACTOR") or "agent"
    row = {"ts": now_iso(), "actor": actor, "kind": kind}
    if isinstance(detail, dict):
        row.update(detail)
    else:
        row["text"] = str(detail)
    path = project.journal_path if project else ROOT_LOG / "journal.jsonl"
    row = _scrub_paths(row)
    try:
        append_jsonl(path, row)
    except OSError:
        pass


def load_config(name: str, default=None):
    return load_json(CONFIG / f"{name}.json", default if default is not None else {})


def eprint(*args):
    print(*args, file=sys.stderr)


def short(text, n=120) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= n else text[: n - 1] + "…"
