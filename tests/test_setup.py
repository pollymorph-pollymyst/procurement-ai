"""Первая настройка: проверка папки ничего не переносит, отметка «пройдена» сохраняется. Запуск: python -m pytest -q"""
from __future__ import annotations

import dataclasses
import shutil

from procurement import control, sources
from procurement.config import ROOT, load_settings


def _sb(tmp_path, with_raw: bool):
    s = load_settings()
    t = dataclasses.replace(s, root=tmp_path, raw_dir=tmp_path / "raw", inbox_dir=tmp_path / "in", out_dir=tmp_path / "out")
    t.inbox_dir.mkdir()
    if with_raw:
        shutil.copytree(s.raw_dir, t.raw_dir)
    return s, t


def test_preview_reads_without_moving(tmp_path):
    s, t = _sb(tmp_path, with_raw=False)
    for name in ("sales_monthly.csv", "stock_monthly.csv", "sku_dim.csv"):
        shutil.copy(s.raw_dir / name, t.inbox_dir / name)
    before = sorted(p.name for p in t.inbox_dir.iterdir())
    p = sources.preview(t)
    assert sorted(p["files"]) == before
    assert sorted(p.name for p in t.inbox_dir.iterdir()) == before  # ничего не перенесено
    assert not (t.inbox_dir / "обработано").exists() and not t.raw_dir.exists()
    st = {r["key"]: r for r in p["tables"]}
    assert st["sales"]["state"] == "new" and st["sales"]["rows"] > 0
    assert p["ready"] and p["ready_new"]


def test_preview_empty_folder(tmp_path):
    _, t = _sb(tmp_path, with_raw=False)
    p = sources.preview(t)
    assert p["files"] == [] and not p["ready"]
    assert all(r["state"] == "none" for r in p["tables"])


def test_preview_falls_back_to_old_export(tmp_path):
    _, t = _sb(tmp_path, with_raw=True)
    p = sources.preview(t)
    assert p["ready"] and not p["ready_new"]
    assert all(r["state"] == "old" for r in p["tables"] if r["required"])


def test_setup_flag(tmp_path):
    cfg = tmp_path / "config.toml"
    shutil.copy(ROOT / "config.toml", cfg)
    text = cfg.read_text(encoding="utf-8")
    cfg.write_text(text.replace('"пройдена" = "да"', '"пройдена" = "нет"'), encoding="utf-8")
    assert control.setup_done(cfg) is False
    control.finish_setup(cfg)
    assert control.setup_done(cfg) is True
    assert "#" in cfg.read_text(encoding="utf-8")  # комментарии на месте
    old = tmp_path / "old.toml"
    old.write_text('["прогноз"]\n', encoding="utf-8")
    assert control.setup_done(old) is True  # старые установки без раздела не спрашиваем
