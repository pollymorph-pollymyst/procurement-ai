"""Прогноз спроса по каждому товару.

Идея простая и проверяемая:
1. Продажи ≠ спрос. Месяц, который начался с нулевым остатком, считается «упущенным спросом»
   и не участвует в оценке (иначе модель решит, что товар никому не нужен).
2. Сезонность — индекс по месяцам года для категории (отношение продаж к скользящему
   среднему за 12 мес.), с поправкой на собственную сезонность товара, если истории много.
3. Уровень — сколько товар продаётся «в средний месяц»: сумма продаж / сумма сезонных
   индексов за последние 12 месяцев (свежие месяцы весят больше). Так сезонный товар
   с пиком в октябре не путается со «спадом» летом.
4. Тренд год к году — учитывается наполовину и в пределах.
5. Разброс — насколько факт отклонялся от модели; нужен для страхового запаса.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import Settings
from .data import Dataset


def month_index(ym: str) -> int:
    y, m = ym.split("-")
    return int(y) * 12 + int(m) - 1


def index_month(i: int) -> str:
    return f"{i // 12:04d}-{i % 12 + 1:02d}"


@dataclass
class SkuForecast:
    sku_id: str
    level: float  # спрос «в средний месяц», шт.
    trend: float  # годовой множитель (1.0 = без тренда)
    season: np.ndarray  # 12 индексов, среднее = 1
    dispersion: float  # φ: дисперсия ошибки = φ × прогноз
    months_history: int
    censored_recent: int  # месяцев с упущенным спросом за 12 мес.
    last_sale_month: str | None
    forecast: np.ndarray  # помесячный прогноз, [0] = месяц даты анализа
    flags: list = field(default_factory=list)


@dataclass
class ForecastResult:
    start_month: int  # индекс месяца даты анализа
    by_sku: dict
    cat_season: dict  # category -> np.ndarray(12)
    history_months: list  # 'YYYY-MM' полных месяцев истории
    sales: pd.DataFrame  # sku × месяц, продажи (полные месяцы)
    censored: pd.DataFrame  # sku × месяц, True = упущенный спрос


def _matrices(ds: Dataset, last_full: int):
    sales = ds.sales.copy()
    sales["mi"] = sales["month"].map(month_index)
    full = sales[(sales["mi"] <= last_full) & (sales["partial_month"] == 0)]
    q = full.pivot_table(index="sku_id", columns="mi", values="qty_sold", aggfunc="sum")
    if q.empty:
        return q, q
    cols = list(range(int(q.columns.min()), last_full + 1))
    q = q.reindex(columns=cols)
    # плотный календарь: после первой продажи пропуск = 0
    first = q.notna().idxmax(axis=1)
    arr = q.to_numpy(dtype=float, copy=True)
    for r, f in enumerate(first.to_numpy()):
        j = cols.index(f)
        row = arr[r, j:]
        row[np.isnan(row)] = 0.0
        arr[r, j:] = row
    q = pd.DataFrame(arr, index=q.index, columns=cols)

    # остатки на 1-е: строки нет = 0 (с месяца, когда начались снимки)
    st = ds.stock.copy()
    st["mi"] = st["month"].map(month_index)
    cens = pd.DataFrame(False, index=q.index, columns=cols)
    if not st.empty:
        first_snap = int(st["mi"].min())
        snap = st.pivot_table(index="sku_id", columns="mi", values="stock_qty", aggfunc="sum")
        snap_cols = list(range(first_snap, last_full + 2))
        snap = snap.reindex(index=q.index, columns=snap_cols).fillna(0.0)
        for mi in cols:
            if mi < first_snap:
                continue
            start0 = snap[mi] <= 0
            cens[mi] = start0.to_numpy()
    cens = cens & q.notna()
    return q, cens


def category_seasonality(q: pd.DataFrame, cats: pd.Series) -> dict:
    """Индекс сезонности категории методом «отношение к скользящему среднему»."""
    out = {}
    for cat, skus in cats.groupby(cats).groups.items():
        tot = q.reindex(skus).sum(axis=0, min_count=1).fillna(0.0)
        vals = tot.to_numpy()
        if len(vals) < 25 or vals.sum() <= 0:
            out[cat] = np.ones(12)
            continue
        # центрированное скользящее среднее 2×12
        ma = pd.Series(vals).rolling(12).mean().rolling(2).mean().shift(-6).to_numpy()
        ratios = {m: [] for m in range(12)}
        for k, mi in enumerate(tot.index):
            if np.isfinite(ma[k]) and ma[k] > 0:
                ratios[mi % 12].append(vals[k] / ma[k])
        idx = np.array([np.median(ratios[m]) if ratios[m] else 1.0 for m in range(12)])
        idx = np.clip(idx, 0.005, None)
        out[cat] = idx / idx.mean()
    return out


def fit(ds: Dataset, s: Settings, horizon_months: int | None = None) -> ForecastResult:
    horizon = (horizon_months or s.horizon_months) + 1
    as_of_mi = ds.as_of.year * 12 + ds.as_of.month - 1
    last_full = as_of_mi - 1
    q, cens = _matrices(ds, last_full)
    cats = ds.sku.set_index("sku_id")["category"].reindex(q.index)
    cat_season = category_seasonality(q, cats)
    cols = np.array(q.columns)

    by_sku = {}
    for sku_id in q.index:
        row = q.loc[sku_id].to_numpy()
        crow = cens.loc[sku_id].to_numpy()
        valid = ~np.isnan(row)
        n_hist = int(valid.sum())
        cat = cats.get(sku_id)
        cs = cat_season.get(cat, np.ones(12))
        season = cs.copy()
        flags = []

        sold_idx = np.where(valid & (row > 0))[0]
        last_sale = index_month(int(cols[sold_idx[-1]])) if len(sold_idx) else None

        # собственная сезонность товара, если истории ≥ 3 лет
        use = valid & ~crow
        if n_hist >= 36 and row[use].sum() >= 120:
            cal = cols % 12
            means = np.array([row[use & (cal == m)].mean() if (use & (cal == m)).sum() >= 2 else np.nan
                              for m in range(12)])
            if np.isfinite(means).all() and means.mean() > 0:
                own = means / means.mean()
                w = min(0.5, n_hist / 12 / 10)
                season = w * own + (1 - w) * cs
                season = np.clip(season, 0.005, None)
                season = season / season.mean()

        s_hist = season[cols % 12]
        age = last_full - cols  # 0 = прошлый месяц

        # если упущенного спроса слишком много — не выбрасываем его (иначе нечего оценивать)
        recent = valid & (age < 24)
        if (recent & ~crow).sum() < 6 and recent.sum() >= 6:
            use = valid.copy()
            flags.append("часто нет на складе — прогноз может быть занижен")
        censored_recent = int((valid & crow & (age < 12)).sum())

        def level_over(mask):
            num, den = row[mask].sum(), s_hist[mask].sum()
            return (num / den if den > 0 else np.nan), den

        level = 0.0
        trend = 1.0
        if last_sale is None or month_index(last_sale) < last_full - 11:
            level = 0.0  # продаж не было 12 месяцев
        else:
            win = use & (age < s.level_window)
            wts = 0.5 ** (age / 6.0)
            den = (wts * s_hist)[win].sum()
            if s_hist[win].sum() < 3:  # мало «сезонной массы» — берём 2 года
                win = use & (age < 24)
                den = (wts * s_hist)[win].sum()
            num = (wts * row)[win].sum()
            level = float(num / den) if den > 0 else float(np.nanmean(row[valid]))
            la, da = level_over(use & (age < 12))
            lb, db = level_over(use & (age >= 12) & (age < 24))
            if n_hist >= 24 and da >= 4 and db >= 4 and lb > 0 and np.isfinite(la):
                g = la / lb
                trend = float(np.clip(1 + s.trend_strength * (g - 1), s.trend_min, s.trend_max))
        if n_hist < s.min_history_months:
            flags.append("мало истории продаж")
            trend = 1.0

        # разброс: дисперсия ошибки ≈ φ × прогноз
        resid_mask = use & (age < 24)
        fitted = level * s_hist[resid_mask]
        phi = 1.0
        if fitted.sum() > 0:
            phi = float(max(1.0, ((row[resid_mask] - fitted) ** 2).sum() / fitted.sum()))

        fc = np.zeros(horizon)
        for h in range(horizon):
            mi = as_of_mi + h
            fc[h] = level * trend ** (min(h, 12) / 12) * season[mi % 12]

        by_sku[sku_id] = SkuForecast(sku_id, level, trend, season, phi, n_hist, censored_recent,
                                     last_sale, fc, flags)

    return ForecastResult(as_of_mi, by_sku, cat_season, [index_month(c) for c in cols], q, cens)
