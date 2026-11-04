"""Чтение config.toml (ключи на русском) в простой объект с английскими полями."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# русский ключ в config.toml -> поле Settings
_MAP = {
    ("анализ", "дата_анализа"): "as_of",
    ("анализ", "день_выгрузки"): "export_day",
    ("анализ", "горизонт_месяцев"): "horizon_months",
    ("закупка", "минимальная_наценка"): "min_markup_pct",
    ("закупка", "срок_поставки_по_умолчанию"): "default_lead_days",
    ("закупка", "срок_поставки_местный"): "local_lead_days",
    ("закупка", "срок_поставки_мин"): "lead_min",
    ("закупка", "срок_поставки_макс"): "lead_max",
    ("закупка", "цикл_заказа_мин"): "cycle_min",
    ("закупка", "цикл_заказа_макс"): "cycle_max",
    ("закупка", "просрочка_сомнительного_заказа"): "doubtful_overdue_days",
    ("закупка", "ожидание_просроченного"): "late_arrival_shift_days",
    ("закупка", "учитывать_недовоз"): "reliability_mode",
    ("закупка", "недовоз_не_ниже"): "reliability_floor",
    ("праздники", "учитывать"): "holidays_mode",
    ("закупка", "избыток_месяцев"): "excess_months",
    ("закупка", "шаг_округления_по_умолчанию"): "default_round_step",
    ("закупка", "редкий_спрос_шт_в_год"): "rare_per_year",
    ("сервис", "A"): "service_A",
    ("сервис", "B"): "service_B",
    ("сервис", "C"): "service_C",
    ("сервис", "граница_A"): "abc_A",
    ("сервис", "граница_B"): "abc_B",
    ("сервис", "неуверенность_уровня"): "level_uncertainty",
    ("сервис", "страховой_запас_макс_доля"): "ss_max_share",
    ("прогноз", "окно_уровня_месяцев"): "level_window",
    ("прогноз", "сила_тренда"): "trend_strength",
    ("прогноз", "тренд_мин"): "trend_min",
    ("прогноз", "тренд_макс"): "trend_max",
    ("прогноз", "мало_истории_месяцев"): "min_history_months",
    ("комментарий", "режим"): "commentary_mode",
    ("комментарий", "модель"): "model",
}


@dataclass
class Settings:
    as_of: str = "авто"
    export_day: int = 13
    horizon_months: int = 18
    min_markup_pct: float = 25
    default_lead_days: int = 123
    local_lead_days: int = 30
    lead_min: int = 30
    lead_max: int = 180
    cycle_min: int = 30
    cycle_max: int = 90
    doubtful_overdue_days: int = 60
    late_arrival_shift_days: int = 21
    reliability_mode: str = "да"  # заказы в пути ждём не целиком, а в той доле, что поставщик обычно довозит
    reliability_floor: float = 0.3
    holidays_mode: str = "да"
    holidays: list = field(default_factory=list)  # holidays.Holiday — из разделов «праздник …»
    supplier_country: dict = field(default_factory=dict)
    excess_months: int = 12
    default_round_step: int = 10
    rare_per_year: float = 12
    service_A: float = 0.95
    service_B: float = 0.90
    service_C: float = 0.85
    abc_A: float = 0.80
    abc_B: float = 0.95
    level_uncertainty: float = 0.10
    ss_max_share: float = 0.5
    level_window: int = 12
    trend_strength: float = 0.5
    trend_min: float = 0.6
    trend_max: float = 1.5
    min_history_months: int = 6
    commentary_mode: str = "авто"
    model: str = ""  # пусто — версия по умолчанию для выбранного сервиса (writers.SERVICES)
    categories: dict = field(default_factory=dict)

    # пути
    root: Path = ROOT
    raw_dir: Path = ROOT / "data" / "raw"
    inbox_dir: Path = ROOT / "Входящие"
    out_dir: Path = ROOT / "outputs"
    docs_dir: Path = ROOT / "docs"

    def category_name(self, code: str) -> str:
        return self.categories.get(code, code)


def load_settings(path: Path | None = None) -> Settings:
    path = path or ROOT / "config.toml"
    s = Settings()
    if not path.exists():
        return s
    with open(path, "rb") as f:
        data = tomllib.load(f)
    for (section, key), attr in _MAP.items():
        if section in data and key in data[section]:
            setattr(s, attr, data[section][key])
    s.categories = dict(data.get("категории", {}))
    from .holidays import read as read_holidays
    s.holidays, s.supplier_country = read_holidays(data)
    return s
