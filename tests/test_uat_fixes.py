"""Исправления по итогам приёмки: файл поставщику, дата выгрузки, расписание, «Что изменилось». Запуск: python -m pytest -q"""
from __future__ import annotations

import os
import time
from datetime import date

import openpyxl
import pandas as pd

from procurement import history, schedule, sources
from procurement.export import supplier_file


def _group(brand="BRD-1"):
    line = {"sku_id": "SKU-1", "category_name": "Бетономешалки", "brand_id": brand, "qty": 100, "round_step": 10,
            "on_hand": 5, "pipeline": 0, "cover_until": "2026-09-01", "arrival_date": "2026-12-18", "abc": "A",
            "reason": "внутреннее пояснение", "deficit": True}
    return {"supplier_id": "SUP-1", "skus": 1, "qty": 100, "lead_days": 120, "holiday_days": 7,
            "holiday": "Праздник весны", "arrival_date": "2026-12-18", "deficit": 1, "lines": [line]}


def _cells(path):
    ws = openpyxl.load_workbook(path).active
    return [c for row in ws.iter_rows(values_only=True) for c in row if c is not None]


def test_supplier_file_has_no_internal_columns(tmp_path):
    vals = _cells(supplier_file(_group(), "2026-08-13", tmp_path / "z.xlsx"))
    text = " ".join(map(str, vals))
    for internal in ("На складе", "В пути", "Хватит до", "Важность", "Почему", "внутреннее пояснение"):
        assert internal not in text
    assert "Срочно" in vals and "да" in vals and "Бренд" in vals
    assert "120 дн. и 7 дн. праздников (Праздник весны)" in text  # срок в шапке сходится с датой прихода


def test_empty_brand_column_dropped(tmp_path):
    vals = _cells(supplier_file(_group(brand=""), "2026-08-13", tmp_path / "z.xlsx"))
    assert "Бренд" not in vals and "Количество, шт." in vals


def test_export_date_from_name_and_data(tmp_path):
    f = tmp_path / "остатки 13.08.2026.xlsx"
    f.write_bytes(b"x")
    assert sources._export_date([f], {}) == (date(2026, 8, 13), "по имени файла")
    # файл скопирован через три месяца после выгрузки: дата файла врёт, берём конец последнего месяца в данных
    g = tmp_path / "остатки.csv"
    g.write_bytes(b"x")
    old = time.mktime((2026, 11, 4, 12, 0, 0, 0, 0, -1))
    os.utime(g, (old, old))
    found = {"sales": [("остатки.csv", pd.DataFrame({"month": ["2026-07", "2026-08"]}))]}
    d, src = sources._export_date([g], found)
    assert d == date(2026, 8, 31) and "месяцу" in src


def test_turning_off_here_keeps_cloud_schedule(tmp_path):
    wf = tmp_path / "daily.yml"
    wf.write_text('on:\n  schedule:\n    - cron: "0 4 * * *"\n  workflow_dispatch:\n', encoding="utf-8")
    before = wf.read_text(encoding="utf-8")
    assert schedule.write_workflow({"включено": False, "время": "07:00", "дни": ["пн"]}, wf) is False
    assert wf.read_text(encoding="utf-8") == before
    assert schedule.write_workflow({"включено": True, "время": "09:00", "дни": ["пн"], "часовой_пояс": 3}, wf)
    assert '"0 6 * * 1"' in wf.read_text(encoding="utf-8")


def test_changes_explain_sent_orders():
    prev = {"as_of": "2026-07-13", "generated_at": "x", "kpi": {},
            "skus": {"SKU-1": {"status": "ORDER_NOW", "status_label": "Заказать сейчас", "qty": 10, "category_name": "К"}}}
    cur = {"as_of": "2026-08-13", "generated_at": "y", "kpi": {},
           "skus": {"SKU-1": {"status": "OK", "status_label": "Хватает", "qty": 0, "category_name": "К"}}}
    d = history.diff(prev, cur, [{"supplier_id": "SUP-1", "when": "2026-08-13T10:00:00+03:00", "sku_ids": ["SKU-1"]}])
    assert d["sent_skus"] == 1 and "отправлен" in d["left_order_now"][0]["why"]
