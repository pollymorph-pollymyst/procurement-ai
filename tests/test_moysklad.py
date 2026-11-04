"""Загрузчик МойСклад: сеть подменяется, проверяем, что данные ложатся в выгрузку, которую понимает приём.
Запуск: python -m pytest -q"""
from __future__ import annotations

import dataclasses
import json
import shutil
import zipfile
from datetime import date
from urllib.parse import parse_qs, urlparse

import pytest

from procurement import moysklad, sources
from procurement import settings as st
from procurement.config import ROOT, load_settings
from procurement.data import load

B = moysklad.BASE
TODAY = date(2026, 8, 13)


def ref(kind, uid, extra=""):
    return {"meta": {"href": f"{B}/entity/{kind}/{uid}{extra}", "type": kind}}


P1 = {**ref("product", "p1"), "article": "A-1", "name": "Дрель", "pathName": "Инструмент/Дрели",
      "supplier": ref("counterparty", "c1"), "buyPrice": {"value": 10000}, "salePrices": [{"value": 20000}]}
P2 = {**ref("product", "p2"), "code": "A-2", "name": "Шланг", "pathName": "Сад",
      "buyPrice": {"value": 0}, "salePrices": []}

DATA = {
    "/entity/counterparty": [{**ref("counterparty", "c1"), "code": "SUP-A", "name": "Фабрика"},
                             {**ref("counterparty", "c2"), "name": "Местный склад"}],
    "/entity/product": [P1, P2],
    "/report/profit/byproduct": [
        {"assortment": {**ref("product", "p1"), "article": "A-1"}, "sellQuantity": 10, "returnQuantity": 1,
         "sellSum": 30000, "sellCostSum": 15000},
        {"assortment": {**ref("product", "p2"), "code": "A-2"}, "sellQuantity": 3, "returnQuantity": 0,
         "sellSum": 900, "sellCostSum": 600},
        {"assortment": {**ref("variant", "v9"), "article": "A-1-RED", "name": "Дрель красная"}, "sellQuantity": 2}],
    "/report/stock/all": [{**ref("product", "p1", "?expand=supplier"), "article": "A-1", "stock": 50},
                          {**ref("product", "p2", "?expand=supplier"), "code": "A-2", "stock": -2}],
    "/entity/purchaseorder": [{**ref("purchaseorder", "o1"), "name": "00012", "agent": ref("counterparty", "c1"),
                               "moment": "2026-07-01 10:00:00.000", "deliveryPlannedMoment": "2026-12-01 00:00:00.000",
                               "positions": {"rows": [{"assortment": ref("product", "p1"), "quantity": 100, "shipped": 20}]}}],
    "/entity/supply": [{**ref("supply", "s1"), "agent": ref("counterparty", "c2"), "moment": "2026-06-15 12:00:00.000",
                        "positions": {"rows": [{"assortment": ref("product", "p1"), "quantity": 40}]}}],
    "/entity/organization": [{"name": "ООО Тест"}],
}


@pytest.fixture()
def fake(monkeypatch):
    calls = []

    def http(url, token):
        assert token == "test-token"
        u = urlparse(url)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        calls.append((u.path.removeprefix(urlparse(B).path), q))
        rows = DATA[calls[-1][0]]
        off, lim = int(q.get("offset", 0)), int(q.get("limit", 1000))
        return {"meta": {"size": len(rows)}, "rows": rows[off:off + lim]}

    monkeypatch.setattr(moysklad, "HTTP", http)
    monkeypatch.setattr(moysklad, "PAGE", 1)  # постраничная загрузка: по одной строке
    return calls


@pytest.fixture()
def sb(tmp_path, monkeypatch):
    shutil.copy(ROOT / "config.toml", tmp_path / "config.toml")
    monkeypatch.setenv(moysklad.TOKEN_ENV, "")
    monkeypatch.delenv(moysklad.TOKEN_ENV)
    s = load_settings()
    return dataclasses.replace(s, root=tmp_path, raw_dir=tmp_path / "raw", inbox_dir=tmp_path / "in", out_dir=tmp_path / "out")


def _on(sb, months=6):
    st.save({(moysklad.SECTION, "включено"): True, (moysklad.SECTION, "месяцев_истории"): months}, sb.root / "config.toml")


def test_fetch_maps_to_intake_tables(fake):
    t = moysklad.fetch("test-token", TODAY, months=6)
    sku = {r["sku_id"]: r for r in t["sku_dim.csv"]}
    assert sku["A-1"] == {"sku_id": "A-1", "category": "Дрели", "supplier_id": "SUP-A", "avg_markup_pct_12m": 100.0}
    assert sku["A-2"]["category"] == "Сад" and sku["A-2"]["avg_markup_pct_12m"] == 50.0
    assert sku["A-1-RED"]["category"] == "без категории"  # модификация, которой нет в справочнике
    months = sorted({r["month"] for r in t["sales_monthly.csv"]})
    assert months == ["2026-03", "2026-04", "2026-05", "2026-06", "2026-07", "2026-08"]
    assert {r["month"] for r in t["stock_monthly.csv"]} == set(months)
    assert min(r["stock_qty"] for r in t["stock_monthly.csv"]) == 0  # отрицательный остаток — ноль
    po = t["purchase_orders.csv"][0]
    assert (po["po_id"], po["supplier_id"], po["qty_pending"], po["status"], po["planned_lead_days"]) == \
        ("00012", "SUP-A", 80, "open", 153)
    assert t["receipts.csv"][0] == {"sku_id": "A-1", "receipt_date": "2026-06-15", "qty": 40, "supplier_id": "Местный склад"}
    # остатки — на 1-е число; продажи текущего месяца — по сегодняшний день
    stock_q = [q for p, q in fake if p == "/report/stock/all"]
    assert stock_q[-1]["moment"] == "2026-08-01 00:00:00"
    last_sales = [q for p, q in fake if p == "/report/profit/byproduct"][-1]
    assert (last_sales["momentFrom"], last_sales["momentTo"]) == ("2026-08-01 00:00:00", "2026-08-13 00:00:00")


def test_pull_feeds_regular_intake(fake, sb, monkeypatch):
    assert moysklad.pull(sb, TODAY) is None  # выключено — ничего не делает
    _on(sb)
    with pytest.raises(moysklad.MoySkladError):
        moysklad.pull(sb, TODAY)  # включено без токена — понятная ошибка
    monkeypatch.setenv(moysklad.TOKEN_ENV, "test-token")
    name = moysklad.pull(sb, TODAY)
    assert name == "moysklad_2026-08-13.zip"
    with zipfile.ZipFile(sb.inbox_dir / name) as z:
        assert set(z.namelist()) == set(moysklad.COLUMNS)
    assert sources.intake(sb)
    rep = json.loads((sb.out_dir / "intake.json").read_text(encoding="utf-8"))
    assert rep["ok"] and rep["export_date"] == "2026-08-13" and not rep["unknown"]
    ds = load(sb)
    assert ds.as_of == TODAY
    assert set(ds.sku["sku_id"]) == {"A-1", "A-2", "A-1-RED"}
    assert ds.po["qty_pending"].sum() == 80
    status = moysklad.status(sb)
    assert status["on"] and status["has_token"] and status["last"]["ok"] and status["last"]["rows"]["sku_dim"] == 3
    assert "test-token" not in json.dumps(status) + (sb.root / "config.toml").read_text(encoding="utf-8")


def test_error_is_recorded(sb, monkeypatch):
    _on(sb)
    monkeypatch.setenv(moysklad.TOKEN_ENV, "test-token")

    def denied(url, token):
        raise moysklad.MoySkladError("МойСклад не принял токен.")
    monkeypatch.setattr(moysklad, "HTTP", denied)
    with pytest.raises(moysklad.MoySkladError):
        moysklad.pull(sb, TODAY)
    last = moysklad.status(sb)["last"]
    assert last["ok"] is False and "токен" in last["error"]
    assert not list(sb.inbox_dir.glob("moysklad_*.zip")) if sb.inbox_dir.exists() else True


def test_check_token(fake):
    assert moysklad.test("test-token") == "ООО Тест"
