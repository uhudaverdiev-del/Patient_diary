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

ROOT = Path(__file__).resolve().parent.parent
DEMO_DIR = ROOT / "data" / "demo"
MAX_FILE_BYTES = 10 * 1024 * 1024


class Storage:
    def __init__(self, user_dir=None):
        self.user_dir = Path(user_dir or ROOT / "data" / "user")
        self.files_dir = self.user_dir / "files"
        self.state_path = self.user_dir / "state.json"

    def load(self):
        if not self.state_path.exists():
            return {"name": "", "profile": {}, "history": []}
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def save(self, state):
        self.user_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.state_path)

    def save_profile(self, profile, name=None):
        state = self.load()
        clean = {}
        for key in ("birth_date", "sex", "region", "smoking"):
            value = profile.get(key)
            if value not in (None, ""):
                clean[key] = str(value)
        clean["conditions"] = [str(c) for c in profile.get("conditions") or []]
        state["profile"] = clean
        if name is not None:
            state["name"] = str(name)
        self.save(state)
        return state

    def add_record(self, record):
        test = record.get("test")
        record_date = record.get("date")
        if not test or not record_date:
            raise ValueError("Укажите обследование и дату прохождения.")
        date.fromisoformat(record_date)
        entry = {
            "id": uuid.uuid4().hex[:12],
            "test": str(test),
            "date": record_date,
            "result": str(record.get("result") or ""),
        }
        upload = record.get("file")
        if upload and upload.get("data"):
            entry["file"] = self._save_file(entry["id"], upload)
        state = self.load()
        state["history"].append(entry)
        self.save(state)
        return entry

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
        state = {"name": demo.get("name", demo_name), "profile": demo["profile"], "history": history}
        self.save(state)
        return state

    def _save_file(self, record_id, upload):
        raw = base64.b64decode(upload["data"].split(",")[-1])
        if len(raw) > MAX_FILE_BYTES:
            raise ValueError("Файл больше 10 МБ.")
        original = Path(str(upload.get("name") or "file")).name
        safe = re.sub(r"[^\w.\-]", "_", original)[-80:] or "file"
        stored = f"{record_id}_{safe}"
        self.files_dir.mkdir(parents=True, exist_ok=True)
        (self.files_dir / stored).write_bytes(raw)
        return {"name": original, "stored": stored, "size": len(raw)}


def list_demos():
    demos = []
    for path in sorted(DEMO_DIR.glob("*.json")):
        demo = json.loads(path.read_text(encoding="utf-8"))
        demos.append({"id": path.stem, "name": demo.get("name", path.stem),
                      "description": demo.get("description", "")})
    return demos
