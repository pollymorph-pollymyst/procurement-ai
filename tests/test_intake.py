"""Приём выгрузок: исходный архив и «как из 1С» (русские колонки, cp1251, «;», запятые, шапка отчёта, xlsx)
должны давать тот же план, что и эталонные данные в data/raw. Запуск: python -m pytest -q"""
from __future__ import annotations

import dataclasses
import json
import os
import zipfile
from datetime import datetime

import pandas as pd
import pytest

from procurement import sources
from procurement.config import load_settings
from procurement.data import load
from procurement.pipeline import compute

EXPORT = datetime(2026, 8, 13, 9, 0).timestamp()


@pytest.fixture(scope="module")
def base():
    s = load_settings()
    return s, _kpi(compute(load(s), s, with_backtest=False))


def _kpi(r):
    lines = [ln for g in r["order_now"] for ln in g["lines"]]
    return {"as_of": r["meta"]["as_of"], "lines": len(lines), "qty": sum(ln["qty"] for ln in lines),
            "skus": len(r["skus"]), "groups": len(r["order_now"])}


def _sandbox(s, tmp_path):
    return dataclasses.replace(s, root=tmp_path, raw_dir=tmp_path / "raw", inbox_dir=tmp_path / "in", out_dir=tmp_path / "out")


def _plan(t):
    return _kpi(compute(load(t), t, with_backtest=False))


def test_original_zip_roundtrip(base, tmp_path):
    s, want = base
    t = _sandbox(s, tmp_path)
    t.inbox_dir.mkdir()
    z = t.inbox_dir / "выгрузка.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for f in s.raw_dir.glob("*.csv"):
            zf.write(f, f"export/{f.name}")
    os.utime(z, (EXPORT, EXPORT))
    assert sources.intake(t)
    assert _plan(t) == want
    assert not z.exists() and list((t.inbox_dir / "обработано").iterdir())
    assert sources.intake(t) is None  # второй раз брать нечего


def _ru(df, cols):
    return df[list(cols)].rename(columns=cols)


def _d(sr):
    return pd.to_datetime(sr).dt.strftime("%d.%m.%Y")


def _num(sr):
    return sr.map(lambda v: "" if pd.isna(v) else f"{v:,.1f}".replace(",", " ").replace(".", ","))


def _csv_1c(df, path, title):
    text = f"{title}\nСформирован: 13.08.2026\n\n" + df.to_csv(sep=";", index=False) + "Итого;;;\n"
    path.write_bytes(text.encode("cp1251"))


def test_1c_style_export(base, tmp_path):
    s, want = base
    t = _sandbox(s, tmp_path)
    src = tmp_path / "1c"
    src.mkdir()
    raw = {f.stem: pd.read_csv(f, dtype={"sku_id": str, "supplier_id": str}) for f in s.raw_dir.glob("*.csv")}

    sku = raw["sku_dim"].copy()
    sku["supply_type"] = sku["supply_type"].map({"import": "Импорт", "local": "Местный", "unknown": ""})
    sku["avg_markup_pct_12m"] = _num(sku["avg_markup_pct_12m"])
    _csv_1c(_ru(sku, {"sku_id": "Артикул", "category": "Группа товаров", "supplier_id": "Основной поставщик",
                      "supply_type": "Тип поставки", "avg_markup_pct_12m": "Наценка, %"}),
            src / "Номенклатура.csv", "Справочник номенклатуры")

    sales = raw["sales_monthly"].copy()
    sales["month"] = pd.to_datetime(sales["month"] + "-01").dt.strftime("%d.%m.%Y")
    sales_x = _ru(sales, {"sku_id": "Артикул", "month": "Период", "qty_sold": "Количество продано",
                          "qty_returned": "Возвраты"})
    stock = raw["stock_monthly"].copy()
    stock["month"] = pd.to_datetime(stock["month"] + "-01").dt.strftime("%d.%m.%Y")
    stock_x = _ru(stock, {"sku_id": "Артикул", "month": "Дата", "stock_qty": "Остаток на складе"})
    with pd.ExcelWriter(src / "Отчёт для закупок.xlsx") as xw:
        pd.DataFrame([["Валовая прибыль и продажи"], ["Период: 01.01.2024 - 13.08.2026"], [""]]).to_excel(
            xw, sheet_name="Продажи", header=False, index=False)
        sales_x.to_excel(xw, sheet_name="Продажи", startrow=3, index=False)
        stock_x.to_excel(xw, sheet_name="Остатки", index=False)

    po = raw["purchase_orders"].copy()
    po["status"] = po["status"].map({"open": "Подтвержден", "closed": "Закрыт", "completed": "Выполнен"})
    for c in ("order_date", "promised_date"):
        po[c] = _d(po[c])
    _csv_1c(_ru(po, {"po_id": "Номер заказа", "sku_id": "Артикул", "supplier_id": "Поставщик", "order_date": "Дата заказа",
                     "promised_date": "Дата поступления", "planned_lead_days": "Срок поставки, дн", "status": "Состояние",
                     "qty_ordered": "Заказано", "qty_confirmed": "Подтверждено", "qty_received": "Поступило",
                     "qty_pending": "Осталось поставить"}),
            src / "Заказы поставщикам.csv", "Заказы поставщикам")

    rc = raw["receipts"].copy()
    rc["receipt_date"] = _d(rc["receipt_date"])
    _csv_1c(_ru(rc, {"sku_id": "Артикул", "receipt_date": "Дата поступления", "qty": "Количество", "supplier_id": "Поставщик"}),
            src / "Поступление товаров.csv", "Поступления товаров и услуг")

    sup = raw["supplier_dim"].copy()
    sup["supply_type"] = sup["supply_type"].map({"import": "Импорт", "local": "Местный", "unknown": ""})
    _csv_1c(_ru(sup, {"supplier_id": "Поставщик", "supply_type": "Тип поставки",
                      "median_planned_lead_days": "Срок поставки", "median_days_between_receipts": "Цикл заказа"}),
            src / "Поставщики.csv", "Контрагенты")
    for f in src.iterdir():
        os.utime(f, (EXPORT, EXPORT))

    t2 = dataclasses.replace(t, inbox_dir=src)
    assert sources.intake(t2)
    rep = sources.read_report(t2)
    assert rep["ok"] and not rep["unknown"], rep
    assert _plan(t2) == want


def test_unknown_file_then_mapping(base, tmp_path):
    s, _ = base
    t = _sandbox(s, tmp_path)
    # прошлая выгрузка уже есть — новый файл только с продажами под странными заголовками
    t.raw_dir.mkdir(parents=True)
    for f in s.raw_dir.glob("*.csv"):
        (t.raw_dir / f.name).write_bytes(f.read_bytes())
    t.inbox_dir.mkdir()
    sales = pd.read_csv(s.raw_dir / "sales_monthly.csv", dtype={"sku_id": str})
    odd = sales[["sku_id", "month", "qty_sold"]].rename(columns={"sku_id": "Позиция", "month": "Мес", "qty_sold": "Шт"})
    f = t.inbox_dir / "выгрузка.csv"
    odd.to_csv(f, sep=";", index=False)
    os.utime(f, (EXPORT, EXPORT))
    sources.intake(t)
    rep = sources.read_report(t)
    assert len(rep["unknown"]) == 1 and f.exists()  # не распознанный файл не трогаем
    u = rep["unknown"][0]
    with pytest.raises(sources.IntakeError):
        sources.save_mapping(t, u["sig"], "sales", {"Позиция": "sku_id"})
    sources.save_mapping(t, u["sig"], "sales", {"Позиция": "sku_id", "Мес": "month", "Шт": "qty_sold"})
    assert sources.intake(t)
    rep = sources.read_report(t)
    assert rep["ok"] and not rep["unknown"]
    assert next(x for x in rep["tables"] if x["key"] == "sales")["file"] == "выгрузка.csv"
    assert next(x for x in rep["tables"] if x["key"] == "sku")["file"] == "из прошлой выгрузки"


def test_missing_required_is_plain_error(base, tmp_path):
    s, _ = base
    t = _sandbox(s, tmp_path)
    t.inbox_dir.mkdir()
    (t.inbox_dir / "остатки.csv").write_text("Артикул;Дата;Остаток\nSKU-1;01.08.2026;5\n", encoding="utf-8")
    with pytest.raises(sources.IntakeError, match="Не хватает данных"):
        sources.intake(t)
    assert json.loads((t.out_dir / "intake.json").read_text(encoding="utf-8"))["error"]


def _with_old_export(s, tmp_path):
    t = _sandbox(s, tmp_path)
    t.raw_dir.mkdir(parents=True)
    for f in s.raw_dir.glob("*"):
        (t.raw_dir / f.name).write_bytes(f.read_bytes())
    t.inbox_dir.mkdir()
    return t


def test_partial_file_stops_update(base, tmp_path):
    s, want = base
    t = _with_old_export(s, tmp_path)
    before = (t.raw_dir / "stock_monthly.csv").read_bytes()
    f = t.inbox_dir / "остатки.csv"
    f.write_text("Артикул;Дата;Остаток\nSKU-1;01.08.2026;5\n", encoding="utf-8")
    with pytest.raises(sources.IntakeError, match="неполная"):
        sources.intake(t)
    assert f.exists() and (t.raw_dir / "stock_monthly.csv").read_bytes() == before  # прошлые данные целы
    rep = sources.read_report(t)
    assert rep["suspect"] and "нет в справочнике" in rep["error"]
    with pytest.raises(sources.IntakeError):  # без подтверждения — снова стоп
        sources.intake(t)
    assert sources.accept_suspect(t)
    assert sources.intake(t)  # «Взять как есть»
    assert not f.exists()


def test_last_month_only_keeps_history(base, tmp_path):
    s, want = base
    t = _with_old_export(s, tmp_path)
    stock = pd.read_csv(s.raw_dir / "stock_monthly.csv", dtype={"sku_id": str})
    last = stock[stock["month"] == stock["month"].max()]
    f = t.inbox_dir / "остатки на дату.csv"
    last.rename(columns={"sku_id": "Артикул", "month": "Дата", "stock_qty": "Остаток"}).to_csv(f, sep=";", index=False)
    os.utime(f, (EXPORT, EXPORT))
    assert sources.intake(t)
    merged = pd.read_csv(t.raw_dir / "stock_monthly.csv", dtype={"sku_id": str})
    assert len(merged) == len(stock) and merged["month"].nunique() == stock["month"].nunique()
    assert _plan(t) == want
