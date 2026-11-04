"""Карточки поставщиков: хранение, проверка, файл заказа на языке поставщика, контакты не публикуются."""
import json
from dataclasses import replace

import pytest
from openpyxl import load_workbook

from procurement import export, suppliers as sup
from procurement.config import load_settings


@pytest.fixture
def s(tmp_path):
    base = load_settings()
    return replace(base, root=tmp_path, out_dir=tmp_path / "outputs", docs_dir=tmp_path / "docs")


GROUP = {"supplier_id": "SUP-1", "skus": 1, "qty": 50, "deficit": 1, "arrival_date": "2026-12-18",
         "lead_days": 120, "lines": [{"sku_id": "A1", "category_name": "Дрель", "qty": 50, "round_step": 10,
                                       "deficit": True, "arrival_date": "2026-12-18"}]}


def test_save_splits_public_and_private(s):
    sup.save(s, {"supplier_id": "SUP-1", "name": "Ningbo Tools", "lang": "zh",
                 "contacts": [{"kind": "wechat", "value": "ningbo_li", "note": "Ли"},
                              {"kind": "email", "value": "li@ningbo.cn"}, {"kind": "phone", "value": ""}]}, {"SUP-1"})
    info = json.loads(sup.info_path(s).read_text(encoding="utf-8"))
    assert info == {"SUP-1": {"name": "Ningbo Tools", "lang": "zh"}}
    assert "li@ningbo.cn" not in sup.info_path(s).read_text(encoding="utf-8")
    assert sup.contacts_path(s).parent == s.out_dir
    assert [c["kind"] for c in sup.read_contacts(s)["SUP-1"]] == ["wechat", "email"]


@pytest.mark.parametrize("body", [
    {"supplier_id": "NOPE"},
    {"supplier_id": "SUP-1", "lang": "de"},
    {"supplier_id": "SUP-1", "contacts": [{"kind": "fax", "value": "1"}]},
    {"supplier_id": "SUP-1", "contacts": [{"kind": "phone", "value": "12"}]},
    {"supplier_id": "SUP-1", "contacts": [{"kind": "email", "value": "not-an-email"}]},
    {"supplier_id": "SUP-1", "name": "x" * 81},
    {"supplier_id": "SUP-1", "contacts": [{"kind": "email", "value": "a@b.cn"}] * 13},
])
def test_bad_input_rejected(s, body):
    with pytest.raises(sup.SupplierError):
        sup.save(s, body, {"SUP-1"})


def test_clearing_removes_entries(s):
    sup.save(s, {"supplier_id": "SUP-1", "name": "X", "contacts": [{"kind": "site", "value": "x.cn"}]}, {"SUP-1"})
    sup.save(s, {"supplier_id": "SUP-1", "name": "", "lang": "ru", "contacts": []}, {"SUP-1"})
    assert sup.read_info(s) == {} and sup.read_contacts(s) == {}


@pytest.mark.parametrize("lang,title,head,date", [
    ("ru", "Заказ поставщику Ningbo (SUP-1)", "Количество, шт.", "18 декабря 2026"),
    ("en", "Purchase order to Ningbo (SUP-1)", "Quantity, pcs", "18 December 2026"),
    ("zh", "采购订单：Ningbo (SUP-1)", "数量（件）", "2026年12月18日"),
])
def test_order_file_language(tmp_path, lang, title, head, date):
    p = export.supplier_file(GROUP, "2026-08-13", tmp_path / "o.xlsx", lang, "Ningbo")
    cells = [c for row in load_workbook(p).active.iter_rows(values_only=True) for c in row if c]
    assert any(str(c).startswith(title) for c in cells)
    assert head in cells and date in cells
    assert sup.T[lang]["yes"] in cells  # пометка «Срочно»


def test_contacts_only_in_local_file(tmp_path, s):
    sup.save(s, {"supplier_id": "SUP-1", "name": "Ningbo", "lang": "en",
                 "contacts": [{"kind": "telegram", "value": "@ningbo_li"}]}, {"SUP-1"})
    result = {"meta": {"as_of": "2026-08-13", "settings": {"min_markup_pct": 25, "rare_per_year": 3, "excess_months": 12}},
              "order_now": [GROUP], "order_soon": [], "kpi": {k: 0 for k in (
                  "order_now_skus", "order_now_qty", "order_now_suppliers", "deficit_skus", "out_of_stock_skus",
                  "excess_skus", "excess_qty", "review_price_skus", "need_supplier_skus", "rare_skus")},
              "lists": {k: [] for k in ("review_price", "need_supplier", "rare", "excess", "dead")}}
    files = export.export_all(result, tmp_path / "orders", sup.read_info(s))
    pub = [c for row in load_workbook(tmp_path / "orders" / files["SUP-1"]).active.iter_rows(values_only=True) for c in row if c]
    assert not any("ningbo_li" in str(c) for c in pub)
    assert any(str(c).startswith("Purchase order to Ningbo") for c in pub)
    local = export.supplier_file(GROUP, "2026-08-13", tmp_path / "l.xlsx", "en", "Ningbo", sup.read_contacts(s)["SUP-1"])
    cells = [c for row in load_workbook(local).active.iter_rows(values_only=True) for c in row if c]
    assert "Contacts: Telegram: @ningbo_li" in cells
    assert sup.contact_line({"kind": "email", "value": "a@b.cn"}, "zh") == "邮箱: a@b.cn"


def test_contacts_not_in_dashboard(s):
    from procurement import build
    (s.root / "dashboard").mkdir()
    (s.root / "dashboard" / "index.html").write_text(f"<script>{build.MARK}</script>", encoding="utf-8")
    sup.save(s, {"supplier_id": "SUP-1", "name": "Ningbo", "contacts": [{"kind": "email", "value": "secret@ningbo.cn"}]},
             {"SUP-1"})
    html = build.build_dashboard({"meta": {"as_of": "2026-08-13"}}, s).read_text(encoding="utf-8")
    assert "Ningbo" in html and "secret@ningbo.cn" not in html
