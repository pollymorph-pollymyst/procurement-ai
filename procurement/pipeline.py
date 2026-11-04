"""Полный расчёт: данные → прогноз → план → проверка прогноза → итоговая сводка для дашборда."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime

import numpy as np

from .backtest import run_backtest
from .config import Settings
from .data import Dataset
from .forecast import fit, index_month
from .plan import STATUS, build_plan

ORDER_FIELDS = ["sku_id", "category_name", "brand_id", "abc", "qty", "round_step", "on_hand", "pipeline",
                "cover_until", "arrival_date", "cover_after", "deficit", "lost_units", "reason", "flags",
                "weight_kg", "markup", "holiday_days", "holiday"]


def compute(ds: Dataset, s: Settings, with_backtest: bool = True, stop: dict | None = None) -> dict:
    from . import stoplist
    fr = fit(ds, s)
    stop = stoplist.read(s) if stop is None else stop
    p = build_plan(ds, s, fr, stop)
    weight = ds.sku.set_index("sku_id")["weight_kg"].to_dict() if "weight_kg" in ds.sku else {}
    for r in p.skus:
        w = weight.get(r["sku_id"])
        r["weight_kg"] = float(w) if w is not None and np.isfinite(w) else None

    skus = sorted(p.skus, key=lambda r: (r["priority"], not r["deficit"], -(r["qty"] or 0)))
    by = defaultdict(list)
    for r in skus:
        by[r["status"]].append(r)

    # ---- заказы по поставщикам
    def group(status_list):
        groups = defaultdict(list)
        for st in status_list:
            for r in by.get(st, []):
                groups[r["supplier_id"]].append(r)
        out = []
        for sup_id, lines in groups.items():
            info = p.suppliers.get(sup_id)
            wt = [l["qty"] * l["weight_kg"] for l in lines if l["weight_kg"]]
            out.append({
                "supplier_id": sup_id,
                "supply_type": info.supply_type if info else None,
                "lead_days": info.lead_days if info else None,
                "holiday_days": max((l.get("holiday_days") or 0 for l in lines), default=0),
                "holiday": next((l["holiday"] for l in sorted(lines, key=lambda l: -(l.get("holiday_days") or 0)) if l.get("holiday")), ""),
                "country": lines[0].get("country"),
                "fill_rate": round(info.fill_rate, 2) if info and info.fill_rate is not None else None,
                "lines": [{k: l.get(k) for k in ORDER_FIELDS} | {"order_date": l["order_date"]} for l in lines],
                "skus": len(lines),
                "qty": int(sum(l["qty"] for l in lines)),
                "deficit": sum(1 for l in lines if l["deficit"]),
                "weight_kg": round(sum(wt)) if wt else None,
                "arrival_date": min((l["arrival_date"] for l in lines if l["arrival_date"]), default=None),
                "order_date": min((l["order_date"] for l in lines if l["order_date"]), default=None),
            })
        return sorted(out, key=lambda g: (-g["deficit"], -g["qty"]))

    order_now = group(["ORDER_NOW"])
    order_soon = group(["ORDER_SOON"])

    active = [r for r in p.skus if r["status"] not in ("DEAD", "INACTIVE", "STOPPED")]
    kpi = {
        "order_now_skus": len(by["ORDER_NOW"]),
        "order_now_qty": int(sum(r["qty"] for r in by["ORDER_NOW"])),
        "order_now_suppliers": len(order_now),
        "order_soon_skus": len(by["ORDER_SOON"]),
        "order_soon_qty": int(sum(r["qty"] for r in by["ORDER_SOON"])),
        "deficit_skus": sum(1 for r in p.skus if r["deficit"]),
        "deficit_lost_units": int(sum(r["lost_units"] for r in p.skus if r["deficit"])),
        "out_of_stock_skus": sum(1 for r in active if r["out_of_stock"]),
        "excess_skus": len(by["EXCESS"]),
        "excess_qty": int(sum(r["excess_qty"] for r in by["EXCESS"])),
        "dead_skus": len(by["DEAD"]),
        "dead_qty": int(sum(r["on_hand"] for r in by["DEAD"])),
        "review_price_skus": len(by["REVIEW_PRICE"]),
        "need_supplier_skus": len(by["NEED_SUPPLIER"]),
        "rare_skus": len(by["RARE"]),
        "stopped_skus": len(by["STOPPED"]),
        "planned_skus": len(by["PLANNED"]),
        "ok_skus": len(by["OK"]),
        "active_skus": len(active),
        "total_skus": len(p.skus),
        "pipeline_qty": int(sum(r["pipeline"] for r in p.skus)),
        "doubtful_qty": int(sum(r["doubtful"] for r in p.skus)),
        "doubtful_po": sum(1 for o in p.open_po if o["state"] == "сомнительный"),
        "late_po": sum(1 for o in p.open_po if o["state"] == "опаздывает"),
        "open_po": len(p.open_po),
    }

    # ---- календарь по месяцам
    cal_month = defaultdict(lambda: {"qty": 0, "skus": set(), "by_cat": defaultdict(int), "suppliers": set()})
    for c in p.calendar:
        m = c["date"][:7]
        cal_month[m]["qty"] += c["qty"]
        cal_month[m]["skus"].add(c["sku_id"])
        cal_month[m]["suppliers"].add(c["supplier_id"])
        cal_month[m]["by_cat"][s.category_name(c["category"])] += c["qty"]
    calendar = [{"month": m, "qty": v["qty"], "skus": len(v["skus"]), "suppliers": len(v["suppliers"]),
                 "by_category": dict(v["by_cat"])} for m, v in sorted(cal_month.items())]

    # ---- сезонность и продажи по категориям
    cats = []
    hist_cols = [c for c in fr.sales.columns if c > fr.start_month - 37]
    sku_cat = ds.sku.set_index("sku_id")["category"]
    for cat, season in fr.cat_season.items():
        ids = [sid for sid in fr.sales.index if sku_cat.get(sid) == cat]
        hist = fr.sales.loc[ids, hist_cols].sum(axis=0).round().astype(int).tolist() if ids else []
        fc = np.zeros(s.horizon_months + 1)
        for sid in ids:
            f = fr.by_sku.get(sid)
            if f is not None:
                fc += f.forecast
        cats.append({"category": cat, "category_name": s.category_name(cat),
                     "season": [round(float(x), 2) for x in season],
                     "peak_month": int(np.argmax(season)) + 1,
                     "history": hist, "forecast": [round(float(x)) for x in fc],
                     "active_skus": sum(1 for r in active if r["category"] == cat),
                     "order_now_skus": sum(1 for r in by["ORDER_NOW"] if r["category"] == cat),
                     "order_now_qty": int(sum(r["qty"] for r in by["ORDER_NOW"] if r["category"] == cat))})
    cats.sort(key=lambda c: -sum(c["history"][-12:]))

    # ---- поставщики
    suppliers = []
    for sid, info in p.suppliers.items():
        mine = [r for r in active if r["supplier_id"] == sid]
        if not mine and not info.open_orders:
            continue
        suppliers.append({"supplier_id": sid, "supply_type": info.supply_type, "lead_days": info.lead_days,
                          "cycle_days": info.cycle_days,
                          "fill_rate": round(info.fill_rate, 2) if info.fill_rate is not None else None,
                          "closed_orders": info.closed_orders, "open_orders": info.open_orders,
                          "active_skus": len(mine), "stopped": stoplist.active(stop["suppliers"].get(sid), p.as_of),
                          "order_now_qty": int(sum(r["qty"] for r in mine if r["status"] == "ORDER_NOW"))})
    suppliers.sort(key=lambda x: -x["order_now_qty"])

    # ---- общие оси для графиков (чтобы не повторять их в каждом товаре)
    first = next((r for r in p.skus if r["history"]["months"]), p.skus[0])
    axes = {"history_months": first["history"]["months"], "forecast_months": first["forecast"]["months"],
            "stock_dates": first["stock_path"]["dates"]}
    for r in p.skus:
        r["history"] = {"sales": [None if (x != x) else round(x) for x in r["history"]["sales"]],
                        "stockout": r["history"]["stockout"]} if r["history"]["sales"] else None
        r["forecast"] = r["forecast"]["qty"]
        r["stock_path"] = {"no_order": r["stock_path"]["no_order"], "with_plan": r["stock_path"]["with_plan"]}

    result = {
        "meta": {"as_of": p.as_of.isoformat(), "generated_at": datetime.now().replace(microsecond=0).isoformat(),
                 "horizon_end": p.meta["horizon_end"], "source": ds.source,
                 "partial_month": ds.partial_month,
                 "statuses": {k: {"label": v[0], "priority": v[1]} for k, v in STATUS.items()},
                 "settings": {"min_markup_pct": s.min_markup_pct, "excess_months": s.excess_months,
                              "service": {"A": s.service_A, "B": s.service_B, "C": s.service_C},
                              "doubtful_overdue_days": s.doubtful_overdue_days,
                              "rare_per_year": s.rare_per_year, "horizon_months": s.horizon_months}},
        "kpi": kpi,
        "order_now": order_now,
        "order_soon": order_soon,
        "lists": {
            "review_price": [_short(r) for r in by["REVIEW_PRICE"]],
            "need_supplier": [_short(r) for r in by["NEED_SUPPLIER"]],
            "rare": [_short(r) for r in by["RARE"]],
            "excess": [_short(r) for r in sorted(by["EXCESS"], key=lambda r: -r["excess_qty"])],
            "dead": [_short(r) for r in sorted(by["DEAD"], key=lambda r: -r["on_hand"])],
            "stopped": [_short(r) | {"stop": r["stop"]} for r in by["STOPPED"]],
            "deficit": [_short(r) for r in skus if r["deficit"]],
        },
        "calendar": calendar,
        "calendar_lines": p.calendar,
        "categories": cats,
        "suppliers": suppliers,
        "open_po": p.open_po,
        "axes": axes,
        "skus": skus,
        "backtest": run_backtest(ds, s) if with_backtest else {"available": False},
    }
    return result


def _short(r: dict) -> dict:
    keys = ["sku_id", "category_name", "supplier_id", "status_label", "on_hand", "pipeline", "qty", "markup",
            "level", "demand_12m", "cover_until", "excess_qty", "deficit", "gap_until", "lost_units", "reason", "abc"]
    return {k: r.get(k) for k in keys}


__all__ = ["compute", "index_month"]
