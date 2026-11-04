"""Загрузка и проверка выгрузки из ERP.

Все ошибки формулируются для закупщика: что не так и что сделать.
"""
from __future__ import annotations

import json
import shutil
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from .config import Settings

REQUIRED = {
    "sku_dim.csv": ["sku_id", "category", "supplier_id", "supply_type", "avg_markup_pct_12m"],
    "sales_monthly.csv": ["sku_id", "category", "month", "qty_sold", "qty_returned", "partial_month"],
    "stock_monthly.csv": ["sku_id", "month", "stock_qty"],
    "purchase_orders.csv": ["po_id", "sku_id", "supplier_id", "order_date", "promised_date",
                            "planned_lead_days", "status", "qty_ordered", "qty_received", "qty_pending"],
    "receipts.csv": ["sku_id", "receipt_date", "qty", "supplier_id"],
    "supplier_dim.csv": ["supplier_id", "supply_type", "median_days_between_receipts",
                         "median_planned_lead_days"],
}


class DataError(Exception):
    """Понятная пользователю ошибка во входных данных."""


@dataclass
class Dataset:
    sku: pd.DataFrame
    sales: pd.DataFrame
    stock: pd.DataFrame
    po: pd.DataFrame
    receipts: pd.DataFrame
    suppliers: pd.DataFrame
    as_of: date
    partial_month: str | None  # 'YYYY-MM' неполного месяца (если есть)
    source: str  # откуда взяты данные (для дашборда)


def take_from_inbox(s: Settings) -> str | None:
    """Если во «Входящих» лежит архив или CSV — переносим их в data/raw.

    Возвращает имя взятого файла или None, если входящих нет.
    """
    s.inbox_dir.mkdir(exist_ok=True)
    zips = sorted(s.inbox_dir.glob("*.zip"), key=lambda p: p.stat().st_mtime)
    csvs = [p for p in s.inbox_dir.glob("*.csv") if p.name in REQUIRED]
    if not zips and not csvs:
        return None
    done = s.inbox_dir / "обработано"
    done.mkdir(exist_ok=True)
    s.raw_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    if zips:
        newest = zips[-1]
        with zipfile.ZipFile(newest) as z:
            names = {Path(n).name: n for n in z.namelist() if n.lower().endswith((".csv", ".md"))}
            missing = [f for f in REQUIRED if f not in names]
            if missing:
                raise DataError(
                    f"В архиве «{newest.name}» не хватает файлов: {', '.join(missing)}. "
                    "Выгрузите из ERP полный комплект и положите архив во «Входящие» снова."
                )
            for short, full in names.items():
                target = s.raw_dir / short
                if not target.resolve().is_relative_to(s.raw_dir.resolve()):
                    continue  # защита от путей вида ../
                with z.open(full) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        for p in zips:
            shutil.move(str(p), done / f"{stamp}_{p.name}")
        return newest.name
    for p in csvs:
        shutil.copy2(p, s.raw_dir / p.name)
        shutil.move(str(p), done / f"{stamp}_{p.name}")
    return ", ".join(p.name for p in csvs)


def _read(s: Settings, name: str) -> pd.DataFrame:
    path = s.raw_dir / name
    if not path.exists():
        raise DataError(
            f"Нет файла {name}. Положите выгрузку из ERP (zip-архив) в папку «Входящие» "
            "и запустите «Обновить анализ» ещё раз."
        )
    try:
        df = pd.read_csv(path, encoding="utf-8")
    except Exception as e:  # noqa: BLE001 — показываем пользователю причину
        raise DataError(f"Не получается прочитать {name}: {e}. Проверьте, что это CSV в кодировке UTF-8.")
    missing = [c for c in REQUIRED[name] if c not in df.columns]
    if missing:
        raise DataError(f"В файле {name} нет колонок: {', '.join(missing)}. Выгрузка не в том формате.")
    return df


def load(s: Settings, as_of_override: str | None = None) -> Dataset:
    sku = _read(s, "sku_dim.csv")
    sales = _read(s, "sales_monthly.csv")
    stock = _read(s, "stock_monthly.csv")
    po = _read(s, "purchase_orders.csv")
    receipts = _read(s, "receipts.csv")
    suppliers = _read(s, "supplier_dim.csv")

    if sales.empty or sku.empty:
        raise DataError("Файлы продаж или справочник товаров пустые — анализировать нечего.")

    sales["qty_sold"] = sales["qty_sold"].fillna(0).clip(lower=0)
    sales["qty_returned"] = sales["qty_returned"].fillna(0).clip(lower=0)
    stock["stock_qty"] = stock["stock_qty"].fillna(0).clip(lower=0)
    for c in ["order_date", "promised_date"]:
        po[c] = pd.to_datetime(po[c], errors="coerce")
    receipts["receipt_date"] = pd.to_datetime(receipts["receipt_date"], errors="coerce")
    for c in ["qty_ordered", "qty_received", "qty_pending", "qty_confirmed"]:
        if c in po:
            po[c] = po[c].fillna(0)

    partial = sales.loc[sales["partial_month"] == 1, "month"]
    partial_month = partial.max() if len(partial) else None

    if as_of_override:
        as_of = date.fromisoformat(as_of_override)
    elif s.as_of not in ("авто", "", None):
        as_of = date.fromisoformat(str(s.as_of))
    elif partial_month:
        y, m = map(int, partial_month.split("-"))
        day = int(s.export_day)
        info = s.raw_dir / "export_info.json"
        if info.exists():  # реальная дата выгрузки, если её записал приём файлов
            try:
                d = date.fromisoformat(json.loads(info.read_text(encoding="utf-8"))["export_date"])
                if (d.year, d.month) == (y, m):
                    day = d.day
            except (ValueError, KeyError, json.JSONDecodeError):
                pass
        as_of = date(y, m, day)
    else:
        # нет неполного месяца — анализ на первое число следующего месяца
        y, m = map(int, sales["month"].max().split("-"))
        as_of = date(y + (m == 12), m % 12 + 1, 1)

    return Dataset(sku, sales, stock, po, receipts, suppliers, as_of, partial_month,
                   source=str(s.raw_dir.relative_to(s.root)) if s.raw_dir.is_relative_to(s.root) else str(s.raw_dir))


def truncate(ds: Dataset, as_of: date) -> Dataset:
    """Данные «как будто анализ делали в дату as_of» — для бэктеста и сравнения запусков."""
    month = f"{as_of.year:04d}-{as_of.month:02d}"
    ts = pd.Timestamp(as_of)
    sales = ds.sales[ds.sales["month"] <= month].copy()
    # месяц as_of становится неполным: оцениваем продажи пропорционально прошедшим дням
    frac = (as_of.day - 1) / pd.Timestamp(as_of).days_in_month
    cur = sales["month"] == month
    if month != ds.partial_month:
        sales.loc[cur, "qty_sold"] = (sales.loc[cur, "qty_sold"] * frac).round()
        sales.loc[cur, "qty_returned"] = (sales.loc[cur, "qty_returned"] * frac).round()
    sales["partial_month"] = cur.astype(int)
    stock = ds.stock[ds.stock["month"] <= month].copy()
    receipts = ds.receipts[ds.receipts["receipt_date"] < ts].copy()
    po = ds.po[ds.po["order_date"] <= ts].copy()
    # на дату as_of заказ ещё был в пути, если по нему что-то пришло позже as_of
    later = ds.receipts[ds.receipts["receipt_date"] >= ts].groupby("sku_id")["qty"].sum()
    reopen = (po["status"] != "open") & (po["promised_date"] > ts - pd.Timedelta(days=30))
    po.loc[reopen, "qty_pending"] = po.loc[reopen, ["qty_received"]].assign(
        lim=po.loc[reopen, "sku_id"].map(later).fillna(0)
    ).min(axis=1)
    po.loc[reopen & (po["qty_pending"] > 0), "status"] = "open"
    return Dataset(ds.sku, sales, stock, po, receipts, ds.suppliers, as_of, month, ds.source)
