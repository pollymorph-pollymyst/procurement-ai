"""Заказ, отмеченный «Отправлено», до следующей выгрузки считается заказом в пути. Запуск: python -m pytest -q"""
from __future__ import annotations

import dataclasses
import json

from procurement.config import load_settings
from procurement.control import add_sent_orders, sent_orders_path
from procurement.data import load
from procurement.pipeline import compute


def _mark(s, key, sup, when, lines):
    p = sent_orders_path(s)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({key: {sup: {"when": when, "lines": lines}}}, ensure_ascii=False), encoding="utf-8")


def test_sent_order_counts_as_in_transit(tmp_path):
    s = dataclasses.replace(load_settings(), out_dir=tmp_path)
    before = compute(load(s), s, with_backtest=False)
    g = before["order_now"][0]
    lines = [[ln["sku_id"], int(ln["qty"]), ln.get("arrival_date")] for ln in g["lines"]]
    as_of = before["meta"]["as_of"]
    _mark(s, as_of, g["supplier_id"], as_of + "T10:00:00+03:00", lines)

    ds, used = add_sent_orders(s, load(s))
    assert used and used[0]["supplier_id"] == g["supplier_id"] and used[0]["skus"] == len(lines)
    after = compute(ds, s, with_backtest=False)
    assert g["supplier_id"] not in {x["supplier_id"] for x in after["order_now"]}  # второй раз не заказываем
    assert after["kpi"]["order_now_skus"] <= before["kpi"]["order_now_skus"] - len(lines)
    # отправка не спасает от дефицита до прихода: «Не успеем довезти» и упущенные продажи не прячутся
    assert after["kpi"]["deficit_skus"] >= before["kpi"]["deficit_skus"]
    assert after["kpi"]["deficit_lost_units"] >= before["kpi"]["deficit_lost_units"] * 0.95
    was = {ln["sku_id"] for ln in g["lines"] if ln["deficit"]}
    sk = {r["sku_id"]: r for r in after["skus"]}
    gap = [sk[x] for x in was if sk[x]["gap_until"]]
    assert gap and all(r["deficit"] and r["lost_units"] > 0 for r in gap)
    assert all("заказа в пути" in r["reason"] for r in gap)


def test_old_mark_ignored_after_new_export(tmp_path):
    s = dataclasses.replace(load_settings(), out_dir=tmp_path)
    ds = load(s)
    _mark(s, "2026-01-10", "SUP-X", "2026-01-10T10:00:00+03:00", [["SKU-1", 10, None]])  # отправлен до выгрузки
    _, used = add_sent_orders(s, ds)
    assert used == []
