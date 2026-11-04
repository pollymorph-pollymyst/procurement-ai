"""История запусков и «что изменилось с прошлого раза».

После каждого запуска в outputs/history/ сохраняется короткий снимок: по каждому товару
статус, количество к заказу, дата заказа, «хватит до». Следующий запуск сравнивает себя
с последним снимком на более раннюю дату данных (или с последним запуском вообще).
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .config import Settings

KEEP = ["status", "status_label", "qty", "order_date", "cover_until", "on_hand", "pipeline", "category_name",
        "supplier_id", "deficit"]


def snapshot(result: dict) -> dict:
    return {
        "as_of": result["meta"]["as_of"],
        "generated_at": result["meta"]["generated_at"],
        "kpi": result["kpi"],
        "skus": {r["sku_id"]: {k: r.get(k) for k in KEEP} for r in result["skus"]},
    }


def save(s: Settings, snap: dict, label: str = "") -> Path:
    d = s.out_dir / "history"
    d.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromisoformat(snap["generated_at"]).strftime("%Y-%m-%d_%H%M%S")
    path = d / f"{snap['as_of']}__{stamp}{label}.json"
    path.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
    return path


def previous(s: Settings, as_of: str) -> dict | None:
    """Последний сохранённый запуск. Предпочитаем запуск на более раннюю дату данных."""
    d = s.out_dir / "history"
    if not d.exists():
        return None
    files = sorted(d.glob("*.json"))
    earlier = [f for f in files if f.name.split("__")[0] < as_of]
    pick = (earlier or files)[-1:] if files else []
    if not pick:
        return None
    return json.loads(pick[0].read_text(encoding="utf-8"))


def _sent_why(sent: list | None) -> dict:
    """Артикул → «заказ отправлен 4 ноября» для товаров из отправленных заказов."""
    from .plan import ru_date
    out = {}
    for x in sent or []:
        day = ru_date(str(x.get("when") or "")[:10]) if x.get("when") else ""
        for sid in x.get("sku_ids", []):
            out[sid] = f"заказ поставщику {x['supplier_id']} отправлен{' ' + day if day else ''}, учтён как в пути"
    return out


def diff(prev: dict | None, cur: dict, sent: list | None = None) -> dict:
    if not prev:
        return {"available": False}
    p, c = prev["skus"], cur["skus"]
    why = _sent_why(sent)
    became_now, left_now, status_changed, qty_changed = [], [], [], []
    for sid, r in c.items():
        o = p.get(sid)
        if not o:
            continue
        if r["status"] == "ORDER_NOW" and o["status"] != "ORDER_NOW":
            became_now.append({"sku_id": sid, "from": o["status_label"], "qty": r["qty"],
                               "category_name": r["category_name"], "supplier_id": r["supplier_id"]})
        elif o["status"] == "ORDER_NOW" and r["status"] != "ORDER_NOW":
            left_now.append({"sku_id": sid, "to": r["status_label"], "category_name": r["category_name"],
                             "why": why.get(sid, "")})
        elif r["status"] != o["status"]:
            status_changed.append({"sku_id": sid, "from": o["status_label"], "to": r["status_label"],
                                   "category_name": r["category_name"], "why": why.get(sid, "")})
        if r["status"] == o["status"] == "ORDER_NOW":
            dq = (r["qty"] or 0) - (o["qty"] or 0)
            base = max(o["qty"] or 0, 1)
            if abs(dq) >= 10 and abs(dq) / base >= 0.2:
                qty_changed.append({"sku_id": sid, "was": o["qty"], "now": r["qty"],
                                    "category_name": r["category_name"]})
    new_skus = [sid for sid in c if sid not in p]
    kpi_delta = {k: (cur["kpi"].get(k, 0) or 0) - (prev["kpi"].get(k, 0) or 0)
                 for k in cur["kpi"] if isinstance(cur["kpi"].get(k), (int, float))}
    return {
        "available": True,
        "prev_as_of": prev["as_of"], "prev_generated_at": prev["generated_at"],
        "kpi_prev": prev["kpi"], "kpi_delta": kpi_delta,
        "became_order_now": sorted(became_now, key=lambda x: -(x["qty"] or 0)),
        "left_order_now": left_now,
        "status_changed": status_changed,
        "qty_changed": sorted(qty_changed, key=lambda x: -abs((x["now"] or 0) - (x["was"] or 0))),
        "new_skus": new_skus,
        "sent_skus": len(why),
    }
