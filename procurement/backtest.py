"""Проверка прогноза на прошлом: «как будто анализ делали год назад».

Сравниваем ошибку нашего прогноза с наивным способом «средние продажи за месяц × 12»,
по каждой категории, на уровне товар × месяц. Ошибка — WAPE: Σ|прогноз − факт| / Σ факт.
Месяцы с упущенным спросом (товара не было на складе) из сравнения исключаем.
"""
from __future__ import annotations

from datetime import date

import numpy as np

from .config import Settings
from .data import Dataset, truncate
from .forecast import fit


def run_backtest(ds: Dataset, s: Settings, months: int = 12) -> dict:
    y, m = ds.as_of.year, ds.as_of.month
    cut = date(y - 1, m, 1)
    past = truncate(ds, cut)
    fr_past = fit(past, s, horizon_months=months)
    fr_now = fit(ds, s, horizon_months=1)  # фактические продажи и упущенный спрос до даты анализа
    start = fr_past.start_month
    cols = [start + h for h in range(months) if start + h in fr_now.sales.columns]
    if not cols:
        return {"available": False}

    cats = ds.sku.set_index("sku_id")["category"]
    res = {}
    monthly = {}
    for sid, f in fr_past.by_sku.items():
        if f.level <= 0 or sid not in fr_now.sales.index:
            continue
        actual = fr_now.sales.loc[sid, cols].to_numpy(dtype=float)
        cens = fr_now.censored.loc[sid, cols].to_numpy(dtype=bool)
        ok = ~np.isnan(actual) & ~cens
        if not ok.any():
            continue
        fc = f.forecast[: len(cols)]
        past_cols = [c for c in fr_past.sales.columns if c >= start - 12]
        naive = np.full(len(cols), np.nanmean(fr_past.sales.loc[sid, past_cols].to_numpy(dtype=float)))
        cat = cats.get(sid)
        r = res.setdefault(cat, {"err": 0.0, "naive": 0.0, "act": 0.0, "n": 0})
        r["err"] += np.abs(fc - actual)[ok].sum()
        r["naive"] += np.abs(naive - actual)[ok].sum()
        r["act"] += actual[ok].sum()
        r["n"] += 1
        mm = monthly.setdefault(cat, {"fc": np.zeros(len(cols)), "act": np.zeros(len(cols)), "naive": np.zeros(len(cols))})
        mm["fc"] += np.where(ok, fc, 0)
        mm["act"] += np.where(ok, actual, 0)
        mm["naive"] += np.where(ok, naive, 0)

    out = {"available": True, "from": cut.isoformat(), "months": len(cols), "categories": []}
    tot = {"err": 0.0, "naive": 0.0, "act": 0.0}
    for cat, r in sorted(res.items(), key=lambda kv: -kv[1]["act"]):
        if r["act"] <= 0:
            continue
        for k in tot:
            tot[k] += r[k]
        mm = monthly[cat]
        out["categories"].append({
            "category": cat, "category_name": s.category_name(cat), "skus": r["n"],
            "model_error": round(r["err"] / r["act"], 3), "naive_error": round(r["naive"] / r["act"], 3),
            "monthly": {"actual": mm["act"].round().tolist(), "model": mm["fc"].round().tolist(),
                        "naive": mm["naive"].round().tolist()},
        })
    if tot["act"] > 0:
        out["model_error"] = round(tot["err"] / tot["act"], 3)
        out["naive_error"] = round(tot["naive"] / tot["act"], 3)
    return out
