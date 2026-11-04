"""Журнал запусков, отметки «Отправлено» и сводка для панели «Управление».

outputs/runs.jsonl — по строке на запуск (последние 200);
outputs/sent.json  — {дата анализа: {поставщик: когда отмечено}}.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

from . import settings as st
from .config import Settings

TRIGGERS = {"manual": "вручную", "panel": "из дашборда", "schedule": "по расписанию",
            "github": "в облаке", "claude": "через Claude Code"}
KEEP_RUNS = 200


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ------------------------------------------------------------------ журнал запусков

def runs_path(s: Settings) -> Path:
    return s.out_dir / "runs.jsonl"


def read_runs(s: Settings, limit: int = 20) -> list[dict]:
    p = runs_path(s)
    if not p.exists():
        return []
    out = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out[-limit:][::-1]  # новые сверху


def log_run(s: Settings, rec: dict) -> dict:
    rec = {"finished": _now(), **rec}
    rec["trigger_label"] = TRIGGERS.get(rec.get("trigger", "manual"), rec.get("trigger"))
    p = runs_path(s)
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
    lines = (lines + [json.dumps(rec, ensure_ascii=False, default=str)])[-KEEP_RUNS:]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return rec


def plain_error(e: BaseException) -> str:
    from .data import DataError
    if isinstance(e, DataError):
        return str(e)
    if isinstance(e, PermissionError):
        return ("Файл занят другой программой (чаще всего открыт в Excel). "
                "Закройте файл заказа и обновите ещё раз.")
    if isinstance(e, st.SettingsError):
        return f"Ошибка в настройках: {e}"
    return f"Неожиданная ошибка: {type(e).__name__}: {e}. Предыдущий результат сохранён."


# ------------------------------------------------------------------ отметки «Отправлено»

def sent_path(s: Settings) -> Path:
    return s.out_dir / "sent.json"


def read_sent(s: Settings) -> dict:
    p = sent_path(s)
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except json.JSONDecodeError:
        return {}


def set_sent(s: Settings, as_of: str, supplier_id: str, on: bool) -> dict:
    data = read_sent(s)
    day = data.setdefault(as_of, {})
    orders = _read_json(sent_orders_path(s))
    od = orders.setdefault(as_of, {})
    if on:
        day[supplier_id] = _now()
        # запоминаем, что именно отправлено: до следующей выгрузки из 1С это заказ в пути
        lines = _order_lines(s, supplier_id)
        if lines:
            od[supplier_id] = {"when": day[supplier_id], "lines": lines}
    else:
        day.pop(supplier_id, None)
        od.pop(supplier_id, None)
    for k in sorted(orders)[:-12]:
        orders.pop(k)
    sent_orders_path(s).write_text(json.dumps(orders, ensure_ascii=False, indent=1), encoding="utf-8")
    # старые даты не нужны: храним последние 12
    for k in sorted(data)[:-12]:
        data.pop(k)
    sent_path(s).parent.mkdir(parents=True, exist_ok=True)
    sent_path(s).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return day


def sent_orders_path(s: Settings) -> Path:
    return s.out_dir / "sent_orders.json"


def _read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except json.JSONDecodeError:
        return {}


def _order_lines(s: Settings, supplier_id: str) -> list:
    res = _read_json(s.out_dir / "latest" / "result.json")
    g = next((g for g in res.get("order_now", []) if str(g["supplier_id"]) == supplier_id), None)
    return [[ln["sku_id"], int(ln["qty"]), ln.get("arrival_date")] for ln in g["lines"]] if g else []


def add_sent_orders(s: Settings, ds):
    """Отправленные из дашборда заказы, которых ещё нет в выгрузке, считаем заказами в пути.

    Отметка учитывается, пока выгрузка не новее отправки: в выгрузке, сделанной после отправки,
    заказ уже должен быть в учётной системе.
    """
    import pandas as pd
    from .plan import SENT_PREFIX
    as_of = ds.as_of.isoformat()
    rows, used = [], []
    for key, sups in _read_json(sent_orders_path(s)).items():
        for sup, rec in sups.items():
            when = str(rec.get("when", ""))[:10]
            if key != as_of and when < as_of:
                continue
            for sku, qty, arrival in rec.get("lines", []):
                rows.append({"po_id": f"{SENT_PREFIX}{sup}-{key}", "sku_id": sku, "supplier_id": sup,
                             "order_date": pd.Timestamp(ds.as_of), "promised_date": pd.Timestamp(arrival) if arrival else pd.NaT,
                             "planned_lead_days": None, "status": "open", "qty_ordered": qty, "qty_confirmed": 0,
                             "qty_received": 0, "qty_pending": qty})
            used.append({"supplier_id": sup, "when": rec.get("when"), "as_of": key, "skus": len(rec.get("lines", [])),
                         "qty": int(sum(q for _, q, _ in rec.get("lines", []))),
                         "sku_ids": [ln[0] for ln in rec.get("lines", [])]})
    if rows:
        ds.po = pd.concat([ds.po, pd.DataFrame(rows)], ignore_index=True)
    return ds, used


# ------------------------------------------------------------------ репозиторий (ссылки для онлайн-версии)

def repo_info(root: Path) -> dict | None:
    def git(*a):
        try:
            return subprocess.run(["git", *a], cwd=root, capture_output=True, text=True, timeout=5).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""
    url = git("remote", "get-url", "origin")
    m = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$", url)
    if not m:
        return None
    branch = git("rev-parse", "--abbrev-ref", "HEAD") or "main"
    return {"url": f"https://github.com/{m.group(1)}/{m.group(2)}", "branch": branch,
            "pages": f"https://{m.group(1).lower()}.github.io/{m.group(2)}/"}


# ------------------------------------------------------------------ всё вместе — в дашборд

def collect(s: Settings, as_of: str | None = None) -> dict:
    from . import holidays, moysklad, schedule, sources, writers
    sch = st.schema()
    folder, custom = sources.source_folder(s)
    sent = read_sent(s).get(as_of or "", {}) if as_of else {}
    return {
        "built": _now(),
        "runs": read_runs(s),
        "settings": sch,
        "schedule": {**sch["schedule"], "task": schedule.status(), "cloud_cron": schedule.cron_line(sch["schedule"])},
        "sent": sent,
        "repo": repo_info(s.root),
        "inbox": str(s.inbox_dir),
        "intake": sources.read_report(s),
        "source": {"folder": str(folder), "custom": custom},
        "moysklad": moysklad.status(s),
        "tables": sources.schema_for_panel(),
        "holidays": holidays.schema(),
        "writer": writers.state(s),
        "setup": {"done": setup_done()},
    }


def setup_done(path=None) -> bool:
    """Пройдена ли первая настройка. Нет раздела — считаем, что да (старые установки)."""
    return str(st.read_raw(path).get("установка", {}).get("пройдена", "да")) == "да"


def finish_setup(path=None) -> None:
    st.save({("установка", "пройдена"): "да"}, path)
