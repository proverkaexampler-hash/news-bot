"""Память бота: какие статьи уже опубликованы. Хранится в Google Таблице (диск GitHub Actions не сохраняется)."""
import json
import os
from datetime import datetime

HEADERS = ["URL", "Статус", "Спроб", "Заголовок", "Дата"]
DONE = {"posted", "skipped", "skipped_old", "skipped_initial", "gave_up"}


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


class MemoryStore:
    def __init__(self):
        self.rows = {}

    def get(self, url):
        return self.rows.get(url)

    def is_empty(self):
        return not self.rows

    def set(self, url, status, attempts, title=""):
        self.rows[url] = {"status": status, "attempts": int(attempts), "title": title, "date": _now()}

    def seed(self, items):
        for url, status, title in items:
            self.set(url, status, 0, title)


class FileStore(MemoryStore):
    def __init__(self, path):
        super().__init__()
        self.path = path
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                self.rows = json.load(f)

    def _save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.rows, f, ensure_ascii=False, indent=1)

    def set(self, url, status, attempts, title=""):
        super().set(url, status, attempts, title)
        self._save()

    def seed(self, items):
        super().seed(items)
        self._save()


class SheetStore(MemoryStore):
    """Лист «Новини» в Google Таблице: бот создаёт его сам."""
    def __init__(self, credentials_json, sheet_id):
        import gspread
        super().__init__()
        sh = gspread.service_account_from_dict(json.loads(credentials_json)).open_by_key(sheet_id)
        try:
            self.ws = sh.worksheet("Новини")
        except gspread.WorksheetNotFound:
            self.ws = sh.add_worksheet("Новини", rows=3000, cols=6)
            self.ws.append_row(HEADERS)
        self._idx = {}
        for i, r in enumerate(self.ws.get_all_values()[1:], start=2):
            if r and r[0]:
                r = (r + [""] * 5)[:5]
                self.rows[r[0]] = {"status": r[1], "attempts": int(r[2] or 0), "title": r[3], "date": r[4]}
                self._idx[r[0]] = i

    def set(self, url, status, attempts, title=""):
        super().set(url, status, attempts, title)
        row = [status, int(attempts), title, self.rows[url]["date"]]
        if url in self._idx:
            i = self._idx[url]
            self.ws.update(values=[row], range_name=f"B{i}:E{i}")
        else:
            res = self.ws.append_row([url] + row, value_input_option="RAW")
            rng = res["updates"]["updatedRange"]                    # например «Новини!A12:E12»
            self._idx[url] = int("".join(c for c in rng.split("!")[1].split(":")[0] if c.isdigit()))

    def seed(self, items):
        rows = []
        for url, status, title in items:
            MemoryStore.set(self, url, status, 0, title)
            rows.append([url, status, 0, title, self.rows[url]["date"]])
        if rows:
            self.ws.append_rows(rows, value_input_option="RAW")
            for i, r in enumerate(self.ws.get_all_values()[1:], start=2):
                if r and r[0]:
                    self._idx[r[0]] = i
