"""Праздники у поставщиков: пока фабрики стоят, заказ не производится — срок поставки удлиняется.

Как считается. У каждого праздника есть страны, даты и простой («до» и «после» праздника, в днях).
Каждому поставщику назначена страна: по типу поставки (импорт / местный) или отдельно.
Если между заказом и приходом попадает простой фабрик страны поставщика, срок поставки
удлиняется на число дней простоя внутри этого окна.

Всё хранится в config.toml и меняется в дашборде: «Управление» → «Праздники у поставщиков».
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np

from . import settings as st

COUNTRIES = ["Китай", "Вьетнам", "Индия", "Турция", "Россия", "без праздников"]
PREFIX = "праздник "
COUNTRY_SECTION = "страна поставщика"


@dataclass
class Holiday:
    name: str
    on: bool
    countries: list[str]
    before: int
    after: int
    dates: list[date]
    note: str = ""

    def closures(self) -> list[tuple[date, date]]:
        """Периоды простоя [начало, конец) по всем датам праздника."""
        return [(d - timedelta(days=self.before), d + timedelta(days=max(1, self.after))) for d in self.dates]


def _dates(v) -> list[date]:
    out = []
    for x in v or []:
        try:
            out.append(date.fromisoformat(str(x)))
        except ValueError:
            continue
    return sorted(out)


def read(raw: dict | None = None) -> tuple[list[Holiday], dict]:
    raw = raw if raw is not None else st.read_raw()
    hs = []
    for sec, v in raw.items():
        if not sec.startswith(PREFIX) or not isinstance(v, dict):
            continue
        hs.append(Holiday(sec[len(PREFIX):], str(v.get("учитывать", "да")) == "да", list(v.get("страны", [])),
                          int(v.get("до", 0)), int(v.get("после", 0)), _dates(v.get("даты")), str(v.get("пояснение", ""))))
    countries = {"импорт": "Китай", "местный": "Россия", **dict(raw.get(COUNTRY_SECTION, {}))}
    return hs, countries


def country_of(supplier_id: str | None, supply_type: str | None, countries: dict) -> str:
    if supplier_id and supplier_id in countries:
        return countries[supplier_id]
    return countries.get("местный" if supply_type == "local" else "импорт", "без праздников")


class Calendar:
    """Задержка из-за праздников для заказа, размещённого в день t (t — дни от даты анализа)."""

    def __init__(self, as_of: date, horizon: int, holidays: list[Holiday], countries: dict, mode: str = "да"):
        self.as_of, self.H = as_of, horizon
        self.hs = [h for h in holidays if h.on] if mode == "да" else []
        self.countries = countries
        self._cache: dict = {}

    def arrivals(self, country: str, lead: int) -> tuple[np.ndarray, np.ndarray]:
        """(A, delay): A[t] — день прихода заказа, размещённого в день t; delay[t] — сколько добавили праздники."""
        key = (country, lead)
        if key in self._cache:
            return self._cache[key]
        H = self.H
        closed = np.zeros(H + lead + 400, dtype=np.int32)
        for h in self.hs:
            if country not in h.countries:
                continue
            for a, b in h.closures():
                i, j = (a - self.as_of).days, (b - self.as_of).days
                i, j = max(i, 0), min(j, len(closed))
                if i < j:
                    closed[i:j] = 1
        cum = np.concatenate([[0], np.cumsum(closed)])
        t = np.arange(H)
        delay = cum[t + lead] - cum[t]  # дни простоя между заказом и плановым приходом
        A = np.maximum.accumulate(t + lead + delay)  # заказ позже не может прийти раньше
        self._cache[key] = (A, delay)
        return A, delay

    def reason(self, country: str, t: int, lead: int) -> str:
        """Какие праздники попали в окно заказа, размещённого в день t."""
        start, end = self.as_of + timedelta(days=t), self.as_of + timedelta(days=t + lead)
        names = []
        for h in self.hs:
            if country in h.countries and any(a < end and b > start for a, b in h.closures()):
                names.append(h.name)
        return ", ".join(dict.fromkeys(names))


def schema(raw: dict | None = None) -> dict:
    hs, countries = read(raw)
    return {"holidays": [{"name": h.name, "on": h.on, "countries": h.countries, "before": h.before, "after": h.after,
                          "dates": [d.isoformat() for d in h.dates], "note": h.note} for h in hs],
            "countries": countries, "country_list": COUNTRIES}


def save(body: dict) -> None:
    """{"holidays": [{name, on, before, after, countries?}], "countries": {"импорт": "Китай", "SUP-…": "Индия"}}"""
    raw = st.read_raw()
    vals = {}
    for h in body.get("holidays") or []:
        sec = PREFIX + str(h.get("name", ""))
        if sec not in raw:
            raise st.SettingsError(f"Неизвестный праздник: {h.get('name')}")
        for k, src, lo, hi in (("до", "before", 0, 60), ("после", "after", 0, 90)):
            if src in h:
                try:
                    v = int(h[src])
                except (TypeError, ValueError):
                    raise st.SettingsError(f"«{h['name']}»: дни простоя — целое число.") from None
                if not lo <= v <= hi:
                    raise st.SettingsError(f"«{h['name']}»: простой {'до' if k == 'до' else 'после'} праздника — от {lo} до {hi} дн.")
                vals[(sec, k)] = v
        if "on" in h:
            vals[(sec, "учитывать")] = "да" if h["on"] else "нет"
        if "countries" in h:
            bad = [c for c in h["countries"] if c not in COUNTRIES]
            if bad:
                raise st.SettingsError("Неизвестная страна: " + ", ".join(bad))
            vals[(sec, "страны")] = list(h["countries"])
    drop = []
    for k, c in (body.get("countries") or {}).items():
        if not c and k not in ("импорт", "местный"):
            drop.append(str(k))  # «как у всех» — убрать отдельную настройку поставщика
            continue
        if c not in COUNTRIES:
            raise st.SettingsError(f"Неизвестная страна: {c}")
        vals[(COUNTRY_SECTION, str(k))] = c
    if vals:
        st.save(vals)
    if drop:
        p = st.config_path()
        text = p.read_text(encoding="utf-8")
        for k in drop:
            text = re.sub(rf'(?m)^\s*"{re.escape(k)}"\s*=.*(?:\r?\n)?', "", text)
        p.write_text(text, encoding="utf-8")
