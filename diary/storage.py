"""Хранение профиля, отметок о пройденных обследованиях и загруженных файлов.

Всё лежит в папке data/user/ (она не попадает в репозиторий), поэтому данные
сохраняются между перезапусками сервера.
"""

import base64
import json
import re
import shutil
import uuid
from datetime import date
from pathlib import Path

from diary.metrics import clean_metric
from diary.privacy import consent_record, detect_file_type

ROOT = Path(__file__).resolve().parent.parent
DEMO_DIR = ROOT / "data" / "demo"
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_COMMENT = 500
MAX_NAME = 100
MAX_RESULT = 2000
KNOWN_CONDITIONS = ("mastectomy", "hysterectomy", "flu_risk")
NO_CONSENT = "Сначала дайте согласие на обработку данных о здоровье (раздел «Профиль»)."


class Storage:
    def __init__(self, user_dir=None):
        self.user_dir = Path(user_dir or ROOT / "data" / "user")
        self.files_dir = self.user_dir / "files"
        self.state_path = self.user_dir / "state.json"

    def load(self):
        if not self.state_path.exists():
            return {"name": "", "profile": {}, "history": [], "consent": None}
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def save(self, state):
        self.user_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.state_path)

    def save_profile(self, profile, name=None, consent=False):
        state = self.load()
        if consent and not state.get("consent"):
            state["consent"] = consent_record()
        if not state.get("consent"):
            raise ValueError(NO_CONSENT)
        clean = {}
        for key in ("birth_date", "sex", "region", "smoking"):
            value = profile.get(key)
            if value not in (None, ""):
                clean[key] = str(value)[:MAX_NAME]
        # Принимаем только известные отметки: всё остальное в профиль не попадает.
        conditions = profile.get("conditions") if isinstance(profile.get("conditions"), list) else []
        clean["conditions"] = [c for c in KNOWN_CONDITIONS if c in conditions]
        state["profile"] = clean
        if name is not None:
            if len(str(name)) > MAX_NAME:
                raise ValueError(f"Имя длиннее {MAX_NAME} символов.")
            state["name"] = str(name)
        self.save(state)
        return state

    def add_record(self, record):
        test = record.get("test")
        record_date = record.get("date")
        if not isinstance(test, str) or not isinstance(record_date, str) or not test or not record_date:
            raise ValueError("Укажите обследование и дату прохождения.")
        date.fromisoformat(record_date)
        _check_result(record.get("result"))
        if not self.load().get("consent"):
            raise ValueError(NO_CONSENT)
        entry = {
            "id": uuid.uuid4().hex[:12],
            "test": str(test),
            "date": record_date,
            "result": str(record.get("result") or ""),
        }
        upload = record.get("file")
        if upload and not isinstance(upload, dict):
            raise ValueError("Неверный формат файла.")
        if upload and upload.get("data"):
            entry["file"] = self._save_file(entry["id"], upload)
        state = self.load()
        state["history"].append(entry)
        self.save(state)
        return entry

    def update_record(self, record_id, changes, known_tests=None):
        """Исправить обследование, дату или результат. Файл остаётся прежним."""
        state = self.load()
        record = next((r for r in state["history"] if r.get("id") == record_id), None)
        if record is None:
            raise KeyError(record_id)
        test = changes.get("test", record["test"])
        record_date = changes.get("date", record["date"])
        if not isinstance(test, str) or not isinstance(record_date, str) or not test or not record_date:
            raise ValueError("Укажите обследование и дату прохождения.")
        _check_result(changes.get("result"))
        if known_tests is not None and test not in known_tests:
            raise ValueError("Неизвестное обследование.")
        date.fromisoformat(record_date)
        record.update(test=str(test), date=record_date, result=str(changes.get("result", record.get("result")) or ""))
        self.save(state)
        return record

    def add_metric(self, record, today=None):
        """Показатель здоровья: давление, вес, холестерин или сахар."""
        state = self.load()
        if not state.get("consent"):
            raise ValueError(NO_CONSENT)
        entry = dict(clean_metric(record, today), id=uuid.uuid4().hex[:12])
        state.setdefault("metrics", []).append(entry)
        self.save(state)
        return entry

    def add_dispute(self, record, known_tests, today):
        """Отметка «в выписке ОМС есть услуга, которую я не получал». Хранится только здесь."""
        state = self.load()
        if not state.get("consent"):
            raise ValueError(NO_CONSENT)
        test = record.get("test")
        if test not in known_tests:
            raise ValueError("Выберите обследование из списка.")
        try:
            when = date.fromisoformat(str(record.get("date")))
        except ValueError:
            raise ValueError("Укажите дату из выписки.")
        if when > today:
            raise ValueError("Дата не может быть в будущем.")
        comment = str(record.get("comment") or "").strip()
        if len(comment) > MAX_COMMENT:
            raise ValueError(f"Комментарий длиннее {MAX_COMMENT} символов.")
        entry = {"id": uuid.uuid4().hex[:12], "test": test, "date": when.isoformat(), "comment": comment}
        state.setdefault("disputes", []).append(entry)
        self.save(state)
        return entry

    def delete_dispute(self, dispute_id):
        state = self.load()
        kept = [d for d in state.get("disputes", []) if d.get("id") != dispute_id]
        if len(kept) == len(state.get("disputes", [])):
            raise KeyError(dispute_id)
        state["disputes"] = kept
        self.save(state)
        return state

    def delete_metric(self, metric_id):
        state = self.load()
        kept = [m for m in state.get("metrics", []) if m.get("id") != metric_id]
        if len(kept) == len(state.get("metrics", [])):
            raise KeyError(metric_id)
        state["metrics"] = kept
        self.save(state)
        return state

    def delete_record(self, record_id):
        state = self.load()
        kept = [r for r in state["history"] if r.get("id") != record_id]
        if len(kept) == len(state["history"]):
            raise KeyError(record_id)
        for r in state["history"]:
            if r.get("id") == record_id and r.get("file"):
                (self.files_dir / r["file"]["stored"]).unlink(missing_ok=True)
        state["history"] = kept
        self.save(state)
        return state

    def file_path(self, stored_name):
        if not re.fullmatch(r"[\w.\-]+", stored_name):
            return None
        path = self.files_dir / stored_name
        return path if path.is_file() else None

    def reset(self):
        """Удаляет профиль, отметки, показатели, приписки и загруженные файлы."""
        if self.files_dir.exists():
            shutil.rmtree(self.files_dir)
        log = self.user_dir / "llm_log.jsonl"
        log.unlink(missing_ok=True)
        state = {"name": "", "profile": {}, "history": [], "metrics": [], "disputes": [], "consent": None}
        self.save(state)
        return state

    def load_demo(self, demo_name):
        if not re.fullmatch(r"[\w\-]+", demo_name):
            raise KeyError(demo_name)
        path = DEMO_DIR / f"{demo_name}.json"
        if not path.exists():
            raise KeyError(demo_name)
        demo = json.loads(path.read_text(encoding="utf-8"))
        if self.files_dir.exists():
            shutil.rmtree(self.files_dir)
        history = [dict(r, id=uuid.uuid4().hex[:12]) for r in demo.get("history", [])]
        metrics = [dict(m, id=uuid.uuid4().hex[:12]) for m in demo.get("metrics", [])]
        # демо-пациенты вымышленные: согласие не требуется, но отмечаем, что это демо
        state = {"name": demo.get("name", demo_name), "profile": demo["profile"], "history": history,
                 "metrics": metrics, "consent": {"demo": True}}
        self.save(state)
        return state

    def _save_file(self, record_id, upload):
        raw = base64.b64decode(str(upload["data"]).split(",")[-1])
        if len(raw) > MAX_FILE_BYTES:
            raise ValueError("Файл больше 10 МБ.")
        kind = detect_file_type(raw)
        if kind is None:
            raise ValueError("Можно загружать только PDF, JPG или PNG.")
        original = Path(str(upload.get("name") or "file")).name
        # расширение берём по содержимому, а не из имени: файл «отчёт.html» с PDF внутри сохранится как .pdf
        stem = re.sub(r"[^\w\-]", "_", Path(original).stem)[-60:] or "file"
        stored = f"{record_id}_{stem}.{kind}"
        self.files_dir.mkdir(parents=True, exist_ok=True)
        (self.files_dir / stored).write_bytes(raw)
        return {"name": original, "stored": stored, "size": len(raw)}


def _check_result(value):
    if value is not None and len(str(value)) > MAX_RESULT:
        raise ValueError(f"Результат длиннее {MAX_RESULT} символов: сократите текст или приложите файл.")


def list_demos():
    demos = []
    for path in sorted(DEMO_DIR.glob("*.json")):
        demo = json.loads(path.read_text(encoding="utf-8"))
        demos.append({"id": path.stem, "name": demo.get("name", path.stem),
                      "description": demo.get("description", "")})
    return demos
