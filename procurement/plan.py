"""План закупок: сколько есть, на сколько хватит, когда и сколько заказать.

Всё считается по дням от даты анализа:
  запас(день) = остаток + приходы по заказам в пути − прогноз спроса,
  если товар кончился, спрос в этот день теряется (не «уходит в минус»).

Заказ размещается в «дату заказа» — последний день, когда заказ ещё успевает прийти
до того, как запас опустится ниже страхового. Количество рассчитано так, чтобы хватило
до следующего прихода (цикл заказа поставщика) плюс страховой запас.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import NormalDist

import numpy as np
import pandas as pd

from .config import Settings
from .data import Dataset
from .forecast import ForecastResult, index_month, month_index
from .holidays import Calendar, country_of

STATUS = {
    "ORDER_NOW": ("Заказать сейчас", 1),
    "ORDER_SOON": ("Заказать в ближайшие 30 дней", 2),
    "NEED_SUPPLIER": ("Нужен поставщик", 3),
    "REVIEW_PRICE": ("Пересмотреть цену", 4),
    "RARE": ("Редкий спрос", 5),
    "PLANNED": ("Заказ позже", 5),
    "OK": ("Запас в норме", 6),
    "EXCESS": ("Избыток", 7),
    "DEAD": ("Мёртвый остаток", 8),
    "INACTIVE": ("Не продаётся", 9),
}


@dataclass
class SupplierInfo:
    supplier_id: str
    supply_type: str
    lead_days: int
    cycle_days: int
    fill_rate: float | None  # доля довезённого по закрытым заказам
    closed_orders: int
    open_orders: int = 0
    reliability: float = 1.0  # какую долю заказа в пути ждём (1 — целиком)


@dataclass
class Plan:
    as_of: date
    skus: list  # list[dict] — по одному на товар
    suppliers: dict  # supplier_id -> SupplierInfo
    calendar: list  # плановые заказы на горизонте: dict(date, sku_id, supplier, qty, category)
    open_po: list  # заказы в пути с пометками
    meta: dict = field(default_factory=dict)


MIN_CLOSED_FOR_RELIABILITY = 3  # меньше закрытых заказов — о надёжности поставщика судить рано
HOLIDAY_FLAG_DAYS = 10  # короткие праздники видны в пояснении, но не выносятся в пометки
SENT_PREFIX = "ОТПР-"  # заказы, отмеченные в дашборде как отправленные, но ещё не попавшие в выгрузку


# ---------------------------------------------------------------- поставщики
def supplier_table(ds: Dataset, s: Settings) -> dict:
    po = ds.po
    sup = ds.suppliers.set_index("supplier_id")
    out = {}
    ids = set(sup.index) | set(po["supplier_id"].dropna())
    for sid in ids:
        st = sup["supply_type"].get(sid, "import") if sid in sup.index else "import"
        mine = po[po["supplier_id"] == sid]
        lead = sup["median_planned_lead_days"].get(sid) if sid in sup.index else np.nan
        if not np.isfinite(lead if lead is not None else np.nan) and len(mine):
            lead = mine["planned_lead_days"].median()
        if lead is None or not np.isfinite(lead):
            lead = s.local_lead_days if st == "local" else s.default_lead_days
        lead = int(np.clip(lead, s.lead_min, s.lead_max))
        cyc = sup["median_days_between_receipts"].get(sid) if sid in sup.index else np.nan
        cyc = int(np.clip(cyc if cyc is not None and np.isfinite(cyc) else s.cycle_max, s.cycle_min, s.cycle_max))
        closed = mine[mine["status"].isin(["completed", "closed"])]
        fill = float(closed["qty_received"].sum() / closed["qty_ordered"].sum()) if closed["qty_ordered"].sum() > 0 else None
        rel = 1.0
        if str(s.reliability_mode) == "да" and fill is not None and len(closed) >= MIN_CLOSED_FOR_RELIABILITY:
            rel = float(np.clip(fill, s.reliability_floor, 1.0))
        out[sid] = SupplierInfo(sid, st, lead, cyc, fill, int(len(closed)),
                                int(((mine["status"] == "open") & (mine["qty_pending"] > 0)).sum()), rel)
    return out


# ---------------------------------------------------------------- остаток на дату
def on_hand_at(ds: Dataset) -> pd.Series:
    """Остаток на дату анализа: снимок на 1-е + приходы − продажи + возвраты с 1-го числа."""
    as_of_m = f"{ds.as_of.year:04d}-{ds.as_of.month:02d}"
    st = ds.stock[ds.stock["month"] <= as_of_m]
    if st.empty:
        return pd.Series(dtype=float)
    snap_m = st["month"].max()
    snap = st[st["month"] == snap_m].groupby("sku_id")["stock_qty"].sum()
    start = pd.Timestamp(snap_m + "-01")
    rec = ds.receipts[(ds.receipts["receipt_date"] >= start) &
                      (ds.receipts["receipt_date"] < pd.Timestamp(ds.as_of))].groupby("sku_id")["qty"].sum()
    sl = ds.sales[(ds.sales["month"] >= snap_m) & (ds.sales["month"] <= as_of_m)]
    sold = sl.groupby("sku_id")["qty_sold"].sum()
    ret = sl.groupby("sku_id")["qty_returned"].sum()
    idx = ds.sku["sku_id"]
    oh = (snap.reindex(idx).fillna(0) + rec.reindex(idx).fillna(0)
          - sold.reindex(idx).fillna(0) + ret.reindex(idx).fillna(0)).clip(lower=0)
    return oh.rename("on_hand")


# ---------------------------------------------------------------- заказы в пути
def open_orders(ds: Dataset, s: Settings, suppliers: dict) -> pd.DataFrame:
    as_of = pd.Timestamp(ds.as_of)
    po = ds.po[(ds.po["status"] == "open") & (ds.po["qty_pending"] > 0)].copy()
    if po.empty:
        return po.assign(arrival=pd.NaT, state="", overdue_days=0, pending=0, expected=0)
    po = po.sort_values("promised_date")
    # приходы этого месяца уже в остатке — не считаем их ещё и «в пути»
    as_of_m = f"{ds.as_of.year:04d}-{ds.as_of.month:02d}"
    month_start = pd.Timestamp(as_of_m + "-01")
    recent = ds.receipts[(ds.receipts["receipt_date"] >= month_start) &
                         (ds.receipts["receipt_date"] < as_of)].groupby("sku_id")["qty"].sum().to_dict()
    pend = []
    for _, r in po.iterrows():
        p = float(r["qty_pending"])
        take = min(p, recent.get(r["sku_id"], 0.0))
        recent[r["sku_id"]] = recent.get(r["sku_id"], 0.0) - take
        pend.append(p - take)
    po["pending"] = pend
    po = po[po["pending"] > 0]
    arrival, state, overdue = [], [], []
    for _, r in po.iterrows():
        prom = r["promised_date"]
        if pd.isna(prom):
            lead = suppliers.get(r["supplier_id"]).lead_days if r["supplier_id"] in suppliers else s.default_lead_days
            prom = r["order_date"] + pd.Timedelta(days=lead)
        od = (as_of - prom).days
        overdue.append(max(0, od))
        if od <= 0:
            arrival.append(prom); state.append("в пути")
        elif od <= s.doubtful_overdue_days:
            arrival.append(as_of + pd.Timedelta(days=s.late_arrival_shift_days)); state.append("опаздывает")
        else:
            arrival.append(pd.NaT); state.append("сомнительный")
    po["arrival"] = arrival
    po["state"] = state
    # ждём не весь заказ, а долю, которую поставщик обычно довозит; свои отправленные заказы — целиком
    rel = po["supplier_id"].map(lambda x: suppliers[x].reliability if x in suppliers else 1.0).astype(float)
    po["expected"] = np.where(po["po_id"].astype(str).str.startswith(SENT_PREFIX), 1.0, rel) * po["pending"]
    po["overdue_days"] = overdue
    return po


# ---------------------------------------------------------------- симуляция
def simulate(start: float, arrivals: np.ndarray, demand: np.ndarray):
    """Запас по дням с потерей спроса при нуле. Возвращает (запас на конец дня, потерянный спрос)."""
    n = len(demand)
    stock = np.empty(n)
    lost = np.zeros(n)
    cur = start
    for t in range(n):
        cur += arrivals[t]
        need = demand[t]
        if cur >= need:
            cur -= need
        else:
            lost[t] = need - cur
            cur = 0.0
        stock[t] = cur
    return stock, lost


def first_true(mask: np.ndarray) -> int | None:
    idx = np.flatnonzero(mask)
    return int(idx[0]) if len(idx) else None


def round_step(hist_qty: list[float], default: int) -> int:
    q = [x for x in hist_qty if x and x > 0]
    if not q:
        return default
    med = float(np.median(q))
    for c in (1000, 500, 100, 50, 10, 5):
        if c <= med and np.mean([x % c == 0 for x in q]) >= 0.8:
            return c
    return 1


def round_up(x: float, step: int) -> int:
    if x <= 0:
        return 0
    return int(math.ceil(x / step) * step)


def _fmt(d: date | None) -> str | None:
    return d.isoformat() if d else None


# ---------------------------------------------------------------- главный расчёт
def build_plan(ds: Dataset, s: Settings, fr: ForecastResult) -> Plan:
    as_of = ds.as_of
    suppliers = supplier_table(ds, s)
    oh = on_hand_at(ds)
    po = open_orders(ds, s, suppliers)
    sku = ds.sku.set_index("sku_id")

    # дневная сетка
    end = pd.Timestamp(as_of) + pd.DateOffset(months=s.horizon_months)
    days = pd.date_range(pd.Timestamp(as_of), end - pd.Timedelta(days=1), freq="D")
    H = len(days)
    day_h = ((days.year * 12 + days.month - 1) - fr.start_month).to_numpy()
    day_dim = days.days_in_month.to_numpy()

    # ABC внутри категории по продажам за 12 мес.
    last12 = [c for c in fr.sales.columns if c > fr.start_month - 13]
    vol12 = fr.sales[last12].sum(axis=1)
    abc = {}
    for cat, grp in sku.reindex(vol12.index).groupby("category"):
        v = vol12.reindex(grp.index).sort_values(ascending=False)
        tot = v.sum()
        cum = v.cumsum() / tot if tot > 0 else v * 0
        for sid, c in cum.items():
            abc[sid] = "A" if c <= s.abc_A or c == cum.iloc[0] else ("B" if c <= s.abc_B else "C")
    z = {k: NormalDist().inv_cdf(getattr(s, f"service_{k}")) for k in "ABC"}

    hist_qty = ds.po.groupby("sku_id")["qty_ordered"].apply(list).to_dict()
    po_by_sku = {k: g for k, g in po.groupby("sku_id")} if len(po) else {}

    cal = Calendar(as_of, H, s.holidays, s.supplier_country, str(s.holidays_mode))
    rows, calendar = [], []
    hist_cols = [c for c in fr.sales.columns if c > fr.start_month - 37]

    for sid in sku.index:
        info = sku.loc[sid]
        fcast = fr.by_sku.get(sid)
        cat = info["category"]
        sup_id = info["supplier_id"] if isinstance(info["supplier_id"], str) else None
        sup = suppliers.get(sup_id) if sup_id else None
        lead = sup.lead_days if sup else (s.local_lead_days if info["supply_type"] == "local" else s.default_lead_days)
        cycle = sup.cycle_days if sup else s.cycle_max
        country = country_of(sup_id, sup.supply_type if sup else info["supply_type"], s.supplier_country)
        A, hdelay = cal.arrivals(country, lead)  # A[t] — день прихода заказа, размещённого в день t
        A0 = int(A[0])

        def order_for(arrival_day: int) -> int:
            """Последний день, когда можно заказать, чтобы товар пришёл не позже arrival_day."""
            return max(0, int(np.searchsorted(A, arrival_day, side="right")) - 1)
        on_hand = float(oh.get(sid, 0.0))
        markup = info["avg_markup_pct_12m"]
        markup = float(markup) if pd.notna(markup) else None

        monthly = fcast.forecast if fcast else np.zeros(s.horizon_months + 1)
        d = monthly[np.clip(day_h, 0, len(monthly) - 1)] / day_dim
        # месяц даты анализа: прогноз на оставшиеся дни
        arr = np.zeros(H)
        pipeline = pipeline_exp = doubtful = 0.0
        my_po = []
        for _, r in po_by_sku.get(sid, pd.DataFrame()).iterrows():
            item = {"po_id": r["po_id"], "qty": float(r["pending"]), "state": r["state"],
                    "promised": _fmt(r["promised_date"].date()) if pd.notna(r["promised_date"]) else None,
                    "arrival": _fmt(r["arrival"].date()) if pd.notna(r["arrival"]) else None,
                    "overdue_days": int(r["overdue_days"]), "expected": round(float(r["expected"]))}
            my_po.append(item)
            if r["state"] == "сомнительный":
                doubtful += r["pending"]
                continue
            k = (r["arrival"] - pd.Timestamp(as_of)).days
            if 0 <= k < H:
                arr[k] += r["expected"]
            pipeline += r["pending"]
            pipeline_exp += r["expected"]

        stock, lost = simulate(on_hand, arr, d)
        level = fcast.level if fcast else 0.0
        flags = list(fcast.flags) if fcast else []
        cls = abc.get(sid, "C")

        # страховой запас на окно «срок поставки + цикл»
        # для прихода в день t неопределённость копится с момента заказа (t − срок) до следующего
        # прихода (t + цикл), поэтому страховой запас меняется по сезону
        phi = fcast.dispersion if fcast else 1.0
        cum = np.concatenate([[0.0], np.cumsum(d)])
        t_all = np.arange(H)
        lo = np.clip(t_all - lead, 0, H)
        hi = np.clip(t_all + cycle, 0, H)
        dW_arr = cum[hi] - cum[lo]
        ss_arr = z[cls] * np.sqrt(phi * dW_arr + (s.level_uncertainty * dW_arr) ** 2)
        # потолок: не больше доли спроса на ближайший цикл вперёд. Иначе после сезонного пика
        # (или при короткой неровной истории) страховой запас остаётся огромным и товар залёживается
        fwd = cum[np.clip(t_all + max(cycle, 60), 0, H)] - cum[t_all]
        ss_arr = np.minimum(ss_arr, s.ss_max_share * fwd)
        ss = float(ss_arr[min(H - 1, A0)])  # страховой запас к моменту прихода заказа «сегодня»

        has_demand = d > 1e-9
        so = first_true((stock <= 1e-9) & has_demand)  # день, когда кончится
        t_idx = np.arange(H)
        # на запас раньше, чем придёт заказ, размещённый сегодня, повлиять уже нельзя —
        # поэтому дату заказа ищем по запасу начиная с «сегодня + срок поставки»
        low = first_true((stock < np.maximum(ss_arr, 1e-9)) & has_demand & (t_idx >= A0))
        step = round_step(hist_qty.get(sid, []), s.default_round_step)

        def qty_for(order_day: int, stock_arr: np.ndarray, arr_arr: np.ndarray) -> float:
            a = int(A[order_day])
            if a >= H:
                return 0.0
            b = min(H, a + cycle)
            before = stock_arr[a - 1] if a > 0 else on_hand
            return max(0.0, ss_arr[b - 1] + d[a:b].sum() - before - arr_arr[a:b].sum())

        # первый заказ
        order_day = None if low is None else order_for(low)
        q_first = 0
        if order_day is not None and A[order_day] < H:
            q_first = round_up(qty_for(order_day, stock, arr), step)
        deficit = so is not None and so < A0 and level > 0
        lost_before_arrival = float(lost[:min(H, A0)].sum()) if deficit else 0.0
        if lost_before_arrival < 0.5:  # пара дней без товара с редким спросом — не дефицит, «упустим 0 шт.» не пишем
            deficit, lost_before_arrival = False, 0.0
        # товар кончится (или уже кончился), но заказ в пути придёт раньше нового: дефицит до его прихода
        gap = None
        if deficit:
            back = first_true((stock > 1e-9) & (t_idx > so))
            if back is not None and back <= A0:
                again = first_true((stock <= 1e-9) & has_demand & (t_idx > back))
                if again is None or again >= A0:
                    gap = back

        # календарь плановых заказов на горизонте
        plan_orders = []
        arr2 = arr.copy()
        st2 = stock
        od = order_day
        while od is not None and A[od] < H and len(plan_orders) < 12 and level > 0:
            q = round_up(qty_for(od, st2, arr2), step)
            if q <= 0:
                break
            plan_orders.append((od, q))
            arr2[A[od]] += q
            st2, _ = simulate(on_hand, arr2, d)
            if od + 1 >= H:
                break
            nxt = first_true((st2 < np.maximum(ss_arr, 1e-9)) & has_demand & (t_idx >= A[od + 1]))
            if nxt is None:
                break
            od = max(order_for(nxt), od + 1)
        # дата и количество первого заказа = первый реальный заказ из плана
        if plan_orders:
            order_day, q_first = plan_orders[0]
        else:
            order_day, q_first = None, 0
        if plan_orders:
            st_with, _ = simulate(on_hand, arr.copy() + _first_only(H, plan_orders[0], A), d)
            a0 = int(A[plan_orders[0][0]])
            so_after = first_true((st_with <= 1e-9) & has_demand & (np.arange(H) >= a0))
        else:
            so_after = None

        # избыток
        n_ex = min(H, int(s.excess_months * 30.4))
        excess_qty = on_hand + pipeline_exp - d[:n_ex].sum() - ss_arr[n_ex - 1]
        is_excess = level > 0 and excess_qty > 0 and (so is None or so > n_ex)

        # статус
        if level <= 0:
            status = "DEAD" if on_hand > 0 else "INACTIVE"
        elif markup is not None and markup < s.min_markup_pct:
            status = "REVIEW_PRICE"
        elif level * 12 < s.rare_per_year:
            status = "RARE"
        elif order_day is not None and order_day <= 30 and sup_id is None:
            status = "NEED_SUPPLIER"
        elif is_excess:
            status = "EXCESS"
        elif order_day is not None and order_day == 0:
            status = "ORDER_NOW"
        elif order_day is not None and order_day <= 30:
            status = "ORDER_SOON"
        elif order_day is not None:
            status = "PLANNED"
        else:
            status = "OK"

        if doubtful > 0:
            flags.append(f"заказ на {int(doubtful)} шт. сильно просрочен — уточните у поставщика")
        if any(p["state"] == "опаздывает" for p in my_po):
            flags.append("поставка опаздывает")
        if sup and sup.fill_rate is not None and 0.05 <= sup.fill_rate < 0.6 and status in ("ORDER_NOW", "ORDER_SOON"):
            flags.append(f"поставщик довозит {round(sup.fill_rate * 100)}% заказанного")
        od_h = order_day if order_day is not None else 0
        hol_reason = cal.reason(country, od_h, lead) if hdelay[od_h] > 0 else ""
        if hol_reason and hdelay[od_h] >= HOLIDAY_FLAG_DAYS and status in ("ORDER_NOW", "ORDER_SOON", "PLANNED", "NEED_SUPPLIER"):
            flags.append(f"срок длиннее на {int(hdelay[od_h])} дн.: {hol_reason}")
        if deficit and gap is not None:
            flags.insert(0, f"товара не будет до прихода заказа в пути (к {ru_date(_fmt(as_of + timedelta(days=int(gap))))}): "
                            f"упустим около {n(lost_before_arrival)} шт.")
        elif deficit:
            flags.insert(0, "не успеем довезти: товар кончится до прихода нового заказа")

        orderable = status in ("ORDER_NOW", "ORDER_SOON", "PLANNED")
        if orderable:
            for od_, q_ in plan_orders:
                calendar.append({"date": _fmt(as_of + timedelta(days=od_)), "sku_id": sid, "category": cat,
                                 "supplier_id": sup_id, "qty": int(q_),
                                 "arrival": _fmt(as_of + timedelta(days=int(A[od_])))})

        def dt(k):
            return as_of + timedelta(days=int(k)) if k is not None else None

        demand_3m = float(d[:91].sum())
        m_idx = fr.start_month
        hist = fr.sales.loc[sid, hist_cols].tolist() if sid in fr.sales.index else []
        hcens = fr.censored.loc[sid, hist_cols].tolist() if sid in fr.censored.index else []
        # запас на конец каждого месяца: без новых заказов и по плану
        month_end_idx = [i for i in range(H - 1) if days[i + 1].day == 1] + [H - 1]
        st_plan = simulate(on_hand, arr2, d)[0] if plan_orders else stock

        row = {
            "sku_id": sid, "category": cat, "category_name": s.category_name(cat),
            "brand_id": info.get("brand_id"), "supplier_id": sup_id, "abc": cls,
            "status": status, "status_label": STATUS[status][0], "priority": STATUS[status][1],
            "markup": markup, "on_hand": round(on_hand), "pipeline": round(pipeline), "pipeline_expected": round(pipeline_exp),
            "doubtful": round(doubtful), "level": round(level, 1),
            "trend": round(fcast.trend, 2) if fcast else 1.0,
            "demand_3m": round(demand_3m), "demand_12m": round(float(d[:365].sum())),
            "lead_days": lead, "cycle_days": cycle, "safety_stock": round(ss),
            "cover_until": _fmt(dt(so)), "cover_days": so if so is not None else None,
            "order_date": _fmt(dt(order_day)) if order_day is not None else None,
            "order_in_days": order_day,
            "qty": int(q_first) if orderable or status in ("REVIEW_PRICE", "NEED_SUPPLIER") else 0,
            "round_step": step,
            "arrival_date": _fmt(dt(A[order_day])) if order_day is not None else None,
            "holiday_days": int(hdelay[order_day]) if order_day is not None else int(hdelay[0]),
            "holiday": hol_reason, "country": country,
            "cover_after": _fmt(dt(so_after)) if plan_orders else None,
            # дефицит показываем у всех товаров, которые держим на складе, — и тогда, когда заказ уже в пути
            "deficit": bool(deficit) and status not in ("DEAD", "INACTIVE", "REVIEW_PRICE", "RARE"),
            "gap_until": (_fmt(as_of + timedelta(days=int(gap))) if deficit and gap is not None
                          and status not in ("DEAD", "INACTIVE", "REVIEW_PRICE", "RARE") else None),
            "out_of_stock": on_hand <= 0 and level > 0, "lost_units": round(lost_before_arrival),
            "excess_qty": round(excess_qty) if is_excess else 0,
            "flags": flags, "po": my_po,
            "season_peak": int(np.argmax(fcast.season)) + 1 if fcast else None,
            "history": {"months": [index_month(c) for c in hist_cols], "sales": hist, "stockout": hcens},
            "forecast": {"months": [index_month(m_idx + h) for h in range(len(monthly))],
                         "qty": [round(float(x), 1) for x in monthly]},
            "stock_path": {"dates": [_fmt(days[i].date()) for i in month_end_idx],
                           "no_order": [round(float(stock[i])) for i in month_end_idx],
                           "with_plan": [round(float(st_plan[i])) for i in month_end_idx]},
            "planned_orders": [{"date": _fmt(dt(o)), "qty": int(q), "arrival": _fmt(dt(A[o]))}
                               for o, q in plan_orders] if orderable else [],
            "months_history": fcast.months_history if fcast else 0,
            "last_sale_month": fcast.last_sale_month if fcast else None,
        }
        row["reason"] = explain(row, s)
        rows.append(row)

    open_po = []
    for _, r in po.iterrows():
        open_po.append({"po_id": r["po_id"], "sku_id": r["sku_id"], "supplier_id": r["supplier_id"],
                        "order_date": _fmt(r["order_date"].date()) if pd.notna(r["order_date"]) else None,
                        "promised": _fmt(r["promised_date"].date()) if pd.notna(r["promised_date"]) else None,
                        "qty": round(float(r["pending"])), "expected": round(float(r["expected"])), "state": r["state"],
                        "overdue_days": int(r["overdue_days"])})
    return Plan(as_of, rows, suppliers, sorted(calendar, key=lambda x: x["date"]), open_po,
                meta={"horizon_end": _fmt(days[-1].date())})


def _first_only(H, order, A):
    a = np.zeros(H)
    od, q = order
    if A[od] < H:
        a[A[od]] = q
    return a


MONTHS_RU = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
             "сентября", "октября", "ноября", "декабря"]
MONTHS_NOM = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август",
              "сентябрь", "октябрь", "ноябрь", "декабрь"]


def ru_date(iso: str | None) -> str:
    if not iso:
        return "—"
    d = date.fromisoformat(iso)
    return f"{d.day} {MONTHS_RU[d.month - 1]} {d.year}"


def n(x) -> str:
    return f"{int(round(x)):,}".replace(",", " ")


def explain(r: dict, s: Settings) -> str:
    """Почему такая рекомендация — человеческим языком, одной-тремя фразами."""
    st = r["status"]
    have = f"Сейчас на складе {n(r['on_hand'])} шт."
    if r["pipeline"]:
        have += f" и {n(r['pipeline'])} шт. в пути."
        exp = r.get("pipeline_expected", r["pipeline"])
        if exp < r["pipeline"] - 0.5:
            have += (f" Поставщик обычно довозит только часть заказа, поэтому рассчитываем на {n(exp)} шт."
                     if exp > 0 else " Поставщик обычно не довозит такие заказы — на них не рассчитываем.")
    per_month = r["demand_3m"] / 3
    dem = f" Ожидаемый спрос — около {n(per_month)} шт. в месяц"
    if r["season_peak"] and r["level"] > 0:
        dem += f", пик — {MONTHS_NOM[r['season_peak'] - 1]}"
    dem += "."
    until = ("" if r.get("cover_days") == 0
             else f" Запаса хватит до {ru_date(r['cover_until'])}." if r["cover_until"]
             else " Запаса хватит больше чем на полтора года.")
    gap = ""
    if r["deficit"] and r.get("gap_until"):
        gap = (f" Товара не будет до прихода заказа в пути (к {ru_date(r['gap_until'])}): упустим около {n(r['lost_units'])} шт."
               " Если можно — попросите поставщика ускорить отгрузку или поищите быструю поставку.")
    if st == "DEAD":
        return f"Продаж не было больше 12 месяцев, а на складе {n(r['on_hand'])} шт. Стоит распродать или вернуть поставщику."
    if st == "INACTIVE":
        return "Товар не продаётся и его нет на складе."
    if st == "REVIEW_PRICE":
        return (f"Наценка {r['markup']:.0f}% — ниже порога {s.min_markup_pct:.0f}%, поэтому в заказ автоматически не попадает."
                + have + until + (f" Если всё же заказывать — {n(r['qty'])} шт." if r["qty"] else ""))
    if st == "RARE":
        return (f"Продаётся редко — около {n(r['demand_12m'])} шт. в год. Держать запас невыгодно: "
                "заказывайте под конкретного клиента." + (" Сейчас на складе нет." if not r["on_hand"] else f" На складе {n(r['on_hand'])} шт."))
    if st == "NEED_SUPPLIER":
        return have + dem + until + " У товара не указан поставщик — назначьте поставщика, чтобы разместить заказ."
    if st == "EXCESS":
        return have + dem + until + f" Это больше чем на {s.excess_months} мес.: лишние около {n(r['excess_qty'])} шт. Новых заказов не нужно."
    lead = f" Поставка идёт {r['lead_days']} дн."
    if r.get("holiday_days"):
        lead = (f" Поставка идёт {r['lead_days']} дн. и ещё {r['holiday_days']} дн. простоя фабрик"
                f" ({r['holiday']}) — заказ нужен раньше.")
    if st == "ORDER_NOW":
        tail = f" Заказать нужно сейчас: {n(r['qty'])} шт., придут к {ru_date(r['arrival_date'])}"
        if r["cover_after"]:
            tail += f" и закроют потребность до {ru_date(r['cover_after'])}"
        tail += "."
        if r["deficit"] and not gap:
            tail += f" Даже срочный заказ не успеет: товара не будет до прихода, упустим около {n(r['lost_units'])} шт. — ищите быструю поставку."
        return have + dem + until + gap + lead + tail
    if st in ("ORDER_SOON", "PLANNED"):
        return have + dem + until + gap + lead + f" Следующий заказ — до {ru_date(r['order_date'])}, около {n(r['qty'])} шт."
    return have + dem + until + gap
