"""Праздники у поставщиков и недовоз. Запуск: python -m pytest -q"""
from __future__ import annotations

import dataclasses
from datetime import date

import pytest

from procurement.config import load_settings
from procurement.data import load
from procurement.holidays import Calendar, Holiday, country_of
from procurement.pipeline import compute

CNY = Holiday("Китайский Новый год", True, ["Китай"], 7, 21, [date(2027, 2, 6)])  # стоят 30 янв – 26 фев


def test_holiday_inside_window_extends_lead():
    cal = Calendar(date(2026, 8, 13), 400, [CNY], {})
    A, delay = cal.arrivals("Китай", 180)  # заказ сегодня пришёл бы 9 фев — простой с 30 янв
    assert delay[0] == 10 and A[0] == 190
    assert cal.reason("Китай", 0, 180) == "Китайский Новый год"
    assert delay[200] == 0  # заказ в марте — праздник уже позади
    assert (A[1:] >= A[:-1]).all()  # заказ позже не приходит раньше


def test_holiday_only_for_its_country_and_switch():
    assert Calendar(date(2026, 8, 13), 400, [CNY], {}).arrivals("Индия", 180)[1][0] == 0
    assert Calendar(date(2026, 8, 13), 400, [CNY], {}, mode="нет").arrivals("Китай", 180)[1][0] == 0


def test_country_by_type_and_override():
    c = {"импорт": "Китай", "местный": "Россия", "SUP-1": "Индия"}
    assert country_of("SUP-1", "import", c) == "Индия"
    assert country_of("SUP-2", "import", c) == "Китай"
    assert country_of(None, "local", c) == "Россия"


@pytest.fixture(scope="module")
def runs():
    s = load_settings()
    ds = load(s)
    on = compute(ds, s, with_backtest=False)
    off = compute(load(s), dataclasses.replace(s, holidays_mode="нет", reliability_mode="нет"), with_backtest=False)
    return on, off


def test_holidays_make_orders_earlier(runs):
    on, off = runs
    assert on["kpi"]["order_now_skus"] >= off["kpi"]["order_now_skus"]
    late = [r for r in on["skus"] if r.get("holiday_days")]
    assert late and all(r["holiday"] for r in late)
    assert not any(r.get("holiday_days") for r in off["skus"])


def test_unreliable_supplier_transit_discounted(runs):
    on, off = runs
    assert all(r["pipeline_expected"] <= r["pipeline"] for r in on["skus"])
    assert any(r["pipeline_expected"] < r["pipeline"] for r in on["skus"])
    assert all(r["pipeline_expected"] == r["pipeline"] for r in off["skus"])
