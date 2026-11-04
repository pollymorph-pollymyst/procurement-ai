"""Проверки расчёта на реальной выгрузке (data/raw). Запуск: python -m pytest -q"""
from __future__ import annotations

import dataclasses
import json
from datetime import date, timedelta

import pandas as pd
import pytest

from procurement import commentary, export
from procurement.backtest import run_backtest
from procurement.config import load_settings
from procurement.data import load, truncate
from procurement.pipeline import compute


@pytest.fixture(scope="module")
def s():
    return load_settings()


@pytest.fixture(scope="module")
def ds(s):
    return load(s)


@pytest.fixture(scope="module")
def result(ds, s):
    return compute(ds, s, with_backtest=False)


def _sku(result, sku_id):
    return next(x for x in result["skus"] if x["sku_id"] == sku_id)


def order_lines(result):
    return [ln for g in result["order_now"] for ln in g["lines"]]


# ---------------------------------------------------------------- что попадает в заказ

def test_order_lines_are_positive_and_rounded(result):
    lines = order_lines(result)
    assert lines, "на дату выгрузки должны быть заказы"
    for ln in lines:
        assert ln["qty"] > 0
        assert ln["qty"] % ln["round_step"] == 0, ln["sku_id"]


def test_low_markup_rare_and_no_supplier_not_in_orders(result, s):
    ordered = {ln["sku_id"] for ln in order_lines(result)}
    for x in result["skus"]:
        if x["status"] in ("REVIEW_PRICE", "RARE", "NEED_SUPPLIER"):
            assert x["sku_id"] not in ordered, x["sku_id"]
    for ln in order_lines(result):
        assert ln["markup"] is None or ln["markup"] >= s.min_markup_pct, ln["sku_id"]


def test_order_groups_match_kpi(result):
    k = result["kpi"]
    assert len(result["order_now"]) == k["order_now_suppliers"]
    assert sum(g["skus"] for g in result["order_now"]) == k["order_now_skus"]
    assert sum(g["qty"] for g in result["order_now"]) == k["order_now_qty"]
    for g in result["order_now"]:
        assert all(_sku(result, ln["sku_id"])["supplier_id"] == g["supplier_id"] for ln in g["lines"])


def test_order_arrives_before_stock_runs_out_or_is_flagged(result):
    """Если товар кончится до прихода сегодняшнего заказа — он обязан быть в списке «не успеем довезти»."""
    for ln in order_lines(result):
        if ln["cover_until"] and ln["cover_until"] < ln["arrival_date"]:
            assert ln["deficit"], ln["sku_id"]


# ---------------------------------------------------------------- заказы в пути не заказываем повторно

def test_goods_in_transit_are_not_ordered_twice(ds, s, result):
    """Добавляем заказ в пути на всё рекомендованное количество — повторный заказ должен уменьшиться."""
    ln = max((ln for ln in order_lines(result) if not ln["deficit"]), key=lambda x: x["qty"])
    x = _sku(result, ln["sku_id"])
    arrive = pd.Timestamp(ds.as_of + timedelta(days=20))
    extra = pd.DataFrame([{
        "po_id": "PO-TEST", "sku_id": x["sku_id"], "supplier_id": x["supplier_id"], "supply_type": "import",
        "order_date": pd.Timestamp(ds.as_of - timedelta(days=100)), "promised_date": arrive,
        "planned_lead_days": 120, "status": "open", "qty_ordered": ln["qty"], "qty_confirmed": 0,
        "qty_received": 0, "qty_pending": ln["qty"]}])
    ds2 = dataclasses.replace(ds, po=pd.concat([ds.po, extra], ignore_index=True))
    r2 = compute(ds2, s, with_backtest=False)
    y = _sku(r2, x["sku_id"])
    assert y["pipeline"] >= x["pipeline"] + ln["qty"]
    assert y["status"] != "ORDER_NOW" or y["qty"] < ln["qty"] * 0.6, (y["status"], y["qty"], ln["qty"])


def test_doubtful_orders_not_counted(result, s):
    for p in result["open_po"]:
        if p.get("overdue_days", 0) > s.doubtful_overdue_days:
            assert p["state"] != "в пути"


# ---------------------------------------------------------------- сезон

def test_heaters_ordered_ahead_of_season(ds, s):
    """Весной обогреватели должны заказываться так, чтобы товар пришёл к осеннему пику (октябрь)."""
    r = compute(truncate(ds, date(2026, 4, 13)), s, with_backtest=False)
    heaters = [x for x in r["skus"] if x["category"] == "space_heaters" and x["demand_12m"] >= 100
               and x["status"] not in ("REVIEW_PRICE", "NEED_SUPPLIER", "RARE", "DEAD", "INACTIVE")]
    assert heaters
    ok = 0
    for x in heaters:
        first = min((p for p in x["planned_orders"]), key=lambda p: p["date"], default=None)
        if first and first["date"] <= "2026-07-31" and first["arrival"] <= "2026-11-15":
            ok += 1
    assert ok / len(heaters) >= 0.7, f"к сезону заказано {ok} из {len(heaters)}"


# ---------------------------------------------------------------- проверка на прошлом

def test_truncate_hides_future(ds):
    t = truncate(ds, date(2025, 8, 13))
    assert t.as_of == date(2025, 8, 13)
    assert t.sales["month"].max() <= "2025-08"
    assert (t.po["order_date"] <= pd.Timestamp("2025-08-13")).all()


def test_backtest_beats_naive(ds, s):
    bt = run_backtest(ds, s)
    assert bt["available"]
    assert bt["model_error"] < bt["naive_error"]


# ---------------------------------------------------------------- комментарий

def test_rule_comment_uses_only_known_numbers(result):
    f = commentary.facts(result, None)
    c = commentary.rule_based(f)
    assert commentary.check_numbers(c, f) == []


def test_invented_number_is_rejected(result, s):
    f = commentary.facts(result, None)
    fake = {"headline": "Сегодня нужно заказать 987654 шт.", "summary": [], "actions": [], "warnings": []}
    assert commentary.check_numbers(fake, f)
    out = commentary.make(result, s, None, override=fake)
    shown = {k: out[k] for k in ("headline", "summary", "actions", "warnings")}
    assert "987654" not in json.dumps(shown, ensure_ascii=False)
    assert "отброшен" in out.get("note", "")


# ---------------------------------------------------------------- файлы

def test_export_writes_file_per_supplier(result, tmp_path):
    files = export.export_all(result, tmp_path)
    for g in result["order_now"]:
        assert (tmp_path / files[g["supplier_id"]].split("/")[-1]).exists()
    assert (tmp_path / files["_all"].split("/")[-1]).exists()
