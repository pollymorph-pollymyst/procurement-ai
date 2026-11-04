"""Локальный сервер для панели «Управление»: python -m procurement serve («Открыть закупки.bat»).

Работает только на этом компьютере (127.0.0.1). Отдаёт docs/ и маленький API:
  GET  /api/state            — журнал, настройки, расписание, идёт ли пересчёт
  POST /api/settings         — {"values": {"раздел.ключ": значение}, "categories": {...}, "recalc": bool}
  POST /api/settings/reset   — {"level": "main" | "extra" | null}
  POST /api/schedule         — {"включено", "время", "дни", "часовой_пояс"}
  POST /api/run              — пересчитать сейчас; GET /api/run — ход пересчёта
  POST /api/sent             — {"supplier_id", "on"} — отметка «Отправлено»
  POST /api/source           — {"folder": путь или null} — папка с выгрузками
  POST /api/source/pick      — окно выбора папки
  POST /api/mapping          — {"sig", "table", "columns", "recalc"} — что лежит в нераспознанном файле
  POST /api/holidays         — {"holidays": [...], "countries": {...}, "recalc"} — праздники у поставщиков
  POST /api/open             — {"what": "inbox" | "orders" | "config"} — открыть папку/файл
Каждый POST требует заголовок X-Procurement: 1 — чужая страница в браузере его не пошлёт.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from . import control, holidays, schedule, sources, writers
from . import settings as st
from .config import ROOT, load_settings

JOB_LOCK = threading.Lock()
JOB: dict = {"running": False}


def _as_of() -> str | None:
    p = load_settings().out_dir / "latest" / "result.json"
    if not p.exists():
        return None
    with open(p, encoding="utf-8") as f:  # meta стоит в начале файла — читаем немного
        head = f.read(4000)
    i = head.find('"as_of": "')
    return head[i + 10:i + 20] if i >= 0 else None


def state() -> dict:
    s = load_settings()
    return {"control": control.collect(s, _as_of()), "job": {k: v for k, v in JOB.items() if k != "proc"},
            "as_of": _as_of()}


def start_run() -> dict:
    with JOB_LOCK:
        if JOB.get("running"):
            return JOB
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen([sys.executable, "-m", "procurement", "run", "--no-open", "--trigger", "panel"],
                                cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                encoding="utf-8", errors="replace", creationflags=flags)
        JOB.clear()
        JOB.update(running=True, started=time.time(), lines=[], ok=None, proc=proc)

    def pump():
        for ln in proc.stdout:
            ln = ln.rstrip()
            if ln:
                JOB["lines"] = (JOB["lines"] + [ln])[-40:]
        code = proc.wait()
        JOB.update(running=False, ok=code == 0, code=code, finished=time.time(), seconds=round(time.time() - JOB["started"]))
    threading.Thread(target=pump, daemon=True).start()
    return JOB


def _open(what: str) -> None:
    s = load_settings()
    folder, custom = sources.source_folder(s)
    target = {"inbox": folder, "orders": s.docs_dir / "orders", "config": s.root / "config.toml"}[what]
    if what != "config" and not custom:
        target.mkdir(exist_ok=True)
    if sys.platform == "win32":
        os.startfile(target)  # noqa: S606 — только наши папки
    else:
        subprocess.Popen(["xdg-open" if sys.platform.startswith("linux") else "open", str(target)])


def _set_folder(folder) -> None:
    folder = str(folder or "").strip()
    if folder:
        p = Path(folder)
        if not p.is_absolute() or not p.is_dir():
            raise st.SettingsError(f"Папка не найдена: {folder}")
    st.save({("источник", "папка"): folder})


def _pick_folder() -> str:
    """Окно выбора папки Windows (в отдельном процессе — окну нужен свой главный поток)."""
    code = ("import tkinter as t,tkinter.filedialog as f;r=t.Tk();r.withdraw();r.attributes('-topmost',1);"
            "print(f.askdirectory(title='Папка, куда 1С кладёт выгрузки') or '')")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8",
                         env={**os.environ, "PYTHONIOENCODING": "utf-8"}, timeout=600)
    return out.stdout.strip()


class Handler(SimpleHTTPRequestHandler):
    server_version = "procurement"

    def log_message(self, fmt, *args):  # без шума в окне
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _trusted(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0]
        origin = self.headers.get("Origin")
        if host not in ("127.0.0.1", "localhost"):
            return False
        if origin and urlparse(origin).hostname not in ("127.0.0.1", "localhost"):
            return False
        return self.headers.get("X-Procurement") == "1"

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/state":
            return self._json(state())
        if path == "/api/run":
            return self._json({k: v for k, v in JOB.items() if k != "proc"})
        return super().do_GET()

    def do_POST(self):
        if not self._trusted():
            return self._json({"error": "Запрос отклонён."}, 403)
        path = urlparse(self.path).path
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}") if n < 200_000 else {}
            if path == "/api/settings":
                vals = st.validate(body.get("values") or {})
                if body.get("categories"):
                    st.save_categories(body["categories"])
                if vals:
                    st.save(vals)
                if body.get("recalc"):
                    start_run()
                return self._json({"ok": True, **state()})
            if path == "/api/settings/reset":
                st.reset_to_defaults(body.get("level"))
                if body.get("recalc"):
                    start_run()
                return self._json({"ok": True, **state()})
            if path == "/api/schedule":
                sch = st.save_schedule(body)
                schedule.apply(sch)
                schedule.write_workflow(sch)
                return self._json({"ok": True, **state()})
            if path == "/api/intake/accept":
                if not sources.accept_suspect(load_settings()):
                    raise sources.IntakeError("Нечего подтверждать: проверка полноты не срабатывала.")
                start_run()
                return self._json({"ok": True, "job": {k: v for k, v in JOB.items() if k != "proc"}})
            if path == "/api/run":
                start_run()
                return self._json({"ok": True, "job": {k: v for k, v in JOB.items() if k != "proc"}})
            if path == "/api/sent":
                s = load_settings()
                day = control.set_sent(s, _as_of() or "", str(body["supplier_id"]), bool(body.get("on", True)))
                return self._json({"ok": True, "sent": day})
            if path == "/api/source":
                _set_folder(body.get("folder"))
                return self._json({"ok": True, **state()})
            if path == "/api/source/pick":
                picked = _pick_folder()
                if picked:
                    _set_folder(picked)
                return self._json({"ok": True, "picked": bool(picked), **state()})
            if path == "/api/mapping":
                s = load_settings()
                sources.save_mapping(s, str(body["sig"]), str(body["table"]), dict(body.get("columns") or {}))
                if body.get("recalc"):
                    start_run()
                return self._json({"ok": True, "job": {k: v for k, v in JOB.items() if k != "proc"}, **state()})
            if path == "/api/holidays":
                holidays.save(body)
                if body.get("recalc"):
                    start_run()
                return self._json({"ok": True, "job": {k: v for k, v in JOB.items() if k != "proc"}, **state()})
            if path == "/api/intake/preview":
                return self._json({"ok": True, "preview": sources.preview(load_settings())})
            if path == "/api/setup/done":
                control.finish_setup()
                if body.get("schedule"):
                    # первая настройка — про этот компьютер; облачное расписание (daily.yml) не трогаем
                    schedule.apply(st.save_schedule(body["schedule"]))
                start_run()
                return self._json({"ok": True, "job": {k: v for k, v in JOB.items() if k != "proc"}, **state()})
            if path == "/api/writer/test":
                return self._json({"ok": True, "reply": writers.test(load_settings(), body)})
            if path == "/api/writer":
                writers.save(load_settings(), body)
                if body.get("recalc"):
                    start_run()
                return self._json({"ok": True, "job": {k: v for k, v in JOB.items() if k != "proc"}, **state()})
            if path == "/api/open":
                _open(body.get("what", "inbox"))
                return self._json({"ok": True})
            return self._json({"error": "Нет такого действия."}, 404)
        except (st.SettingsError, sources.IntakeError, writers.WriterError) as e:
            return self._json({"ok": False, "error": str(e)}, 400)
        except Exception as e:  # noqa: BLE001
            return self._json({"ok": False, "error": control.plain_error(e)}, 500)


def _free_port(start=8765, tries=20) -> int:
    for p in range(start, start + tries):
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", p)) != 0:
                return p
    raise RuntimeError("Не нашлось свободного порта 8765–8784.")


def serve(open_browser=True, port: int | None = None) -> int:
    s = load_settings()
    if not (s.docs_dir / "index.html").exists():
        print("Дашборда ещё нет — считаю в первый раз…")
        subprocess.run([sys.executable, "-m", "procurement", "run", "--no-open", "--trigger", "panel"], cwd=ROOT)
    port = port or _free_port()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, directory=str(s.docs_dir)))
    url = f"http://127.0.0.1:{port}/"
    print(f"Дашборд с управлением: {url}")
    print("Пока это окно открыто, в дашборде работают кнопки «Обновить сейчас», настройки и расписание.")
    print("Закрыть — просто закройте окно.")
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
