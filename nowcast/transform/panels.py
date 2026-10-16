"""Task 2.8: ``build_panels(as_of)``, the two model-ready panels as the data stood on a date.

    python -m nowcast.transform.panels --store <data branch checkout> [--as-of YYYY-MM-DD] --out <folder>

writes ``panel_yoy.parquet``, ``panel_sa.parquet``, ``panel_meta.csv`` and ``panel_flags.csv``.

Panels are long tables, one row per variable and period:

* ``panel_yoy``  (bridges, MIDAS): ``variable, freq, block, period, value, z``. ``value`` is the
  percent change on the same month (quarter) a year earlier (pp change for interest rates);
  ``z`` is standardised on the as-of sample. Quarterly rows also hold the 0/1 dummies
  ``d_2020Q2`` and ``d_2020Q3`` (block ``dummy``, z = value).
* ``panel_sa``   (factor model): ``variable, freq, block, period, value, outlier, value_clean, z``.
  ``value`` is 100 x log change of the seasonally adjusted real level on the previous period
  (pp change for interest rates); ``value_clean`` is missing where ``outlier`` (more than 4
  robust standard deviations from the median); ``z`` standardises ``value_clean``.

``Panels.wide(...)`` pivots either panel to periods x variables. ``Panels.levels`` keeps the real
(and SA) levels behind every variable, for the transformation report and for Phase 3, which turns
the factor model's q/q nowcast back into y/y growth through the GDP level.
"""

from __future__ import annotations

import argparse
import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import yaml

from nowcast.transform import growth as gr
from nowcast.transform.decumulate import decumulate, decumulate_average, discrete_growth, regular
from nowcast.transform.inputs import Inputs, load_inputs
from nowcast.transform.levels import (
    chain_mm,
    deflate,
    first_full_year,
    levels_from_yoy,
    to_quarterly_mean,
)
from nowcast.transform.seasonal import stl_adjust
from nowcast.transform.target import ITEMS, build_target

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "config" / "transforms.yaml"
# Panels start here (levels and seasonal factors still use all history). 2013 is where the
# central bank series begin; the gold price alone would otherwise reach back to 1960 and dominate
# its own standardisation.
PANEL_START = {"M": pd.Period("2013-01", freq="M"), "Q": pd.Period("2013Q1", freq="Q")}


def load_spec(path: Path = SPEC) -> list[dict]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    out = []
    for freq, key in (("M", "monthly"), ("Q", "quarterly")):
        for v in raw[key]:
            out.append({**v, "freq": freq})
    return out


@dataclass
class Var:
    id: str
    freq: str
    block: str
    recipe: str
    status: str = "core"
    yoy: pd.Series | None = None  # % change y/y (pp for rates)
    level: pd.Series | None = None  # real (or price) level, unadjusted
    sa_level: pd.Series | None = None
    sa_growth: pd.Series | None = None  # 100 x dlog SA level (pp change for rates)
    info: dict = field(default_factory=dict)
    flags: list[tuple[str, str]] = field(default_factory=list)  # (period, flag)

    def flag(self, periods, text: str) -> None:
        for p in periods:
            self.flags.append((str(p), text))


class Builder:
    def __init__(self, inputs: Inputs):
        self.inp = inputs
        self._cpi: pd.Series | None = None
        self._target: pd.DataFrame | None = None

    # ----------------------------------------------------------------- helpers
    def discrete(self, sid: str, item: str, scale: float = 1.0) -> pd.Series:
        s = self.inp.raw(sid, item)
        form = self.inp.form(sid)
        if form == "cumulative_ytd":
            s = decumulate(s)
        elif form == "average_ytd":
            s = decumulate_average(s)
        elif form not in ("discrete", "level"):
            raise ValueError(f"{sid}: form {form} is not a value series")
        return s * scale

    def cpi(self) -> pd.Series:
        if self._cpi is None:
            self._cpi = chain_mm(self.inp.raw("cpi_mm_old", "1"), self.inp.raw("cpi_mm", "1"))
        return self._cpi

    def target(self) -> pd.DataFrame:
        if self._target is None:
            d = self.inp.data[self.inp.data["series_id"] == "gdp_nag"]
            nag = {}
            for k in ("N", "R", "G"):
                sub = d[d["item"].str.endswith(f".{k}")].copy()
                sub["it"] = sub["item"].str[:-2]
                wide = sub.pivot(index="it", columns="ref_period", values="value")
                nag[k] = wide.reindex([i for i, _ in ITEMS])[sorted(wide.columns)]
            self._target = build_target(nag)
        return self._target

    def status_of(self, sid: str) -> str:
        return self.inp.catalogue.get(sid, {}).get("status", "core")

    def finish(self, v: Var, spec: dict) -> Var:
        """Seasonal adjustment and SA growth from v.level (not for rates or y/y-only variables)."""
        if v.level is None or spec.get("panel_sa") is False:
            return v
        lvl = v.level.where(v.level > 0)
        if spec.get("sa", True):
            sa, note = stl_adjust(lvl)
            v.info["sa"] = note
            if sa is None:
                return v
        else:
            sa = lvl
            v.info["sa"] = "not adjusted (no seasonal pattern expected)"
        v.sa_level = sa
        v.sa_growth = gr.log_growth(sa)
        return v

    # ----------------------------------------------------------------- recipes
    def real_growth(self, spec: dict) -> Var:
        sid, item = spec["input"]
        v = Var(spec["id"], spec["freq"], spec["block"], "real_growth", self.status_of(sid))
        G = self.inp.raw(sid, item)
        if G.empty:
            v.info["missing"] = f"{sid}/{item} has no data"
            return v
        weights = []
        for i, (wsid, witem) in enumerate(spec.get("weights") or []):
            w = self.inp.raw(wsid, witem)
            if self.inp.form(wsid) != "cumulative_ytd":
                raise ValueError(f"weights {wsid} must be cumulative_ytd")
            weights.append(("own" if i == 0 else f"{wsid}/{witem}", w if not w.empty else None))
        if self.inp.form(sid) == "growth_yoy":
            g = G
            v.info["weights"] = "none needed (discrete y/y as published)"
        else:
            dg = discrete_growth(G, weights)
            g = dg["g"]
            counts = dg.loc[G.notna(), "basis"].value_counts().to_dict()
            v.info["weights"] = ", ".join(f"{k} {n}" for k, n in counts.items())
            for basis in counts:
                if basis != "prev_year":
                    v.flag(dg.index[(dg["basis"] == basis) & G.notna()], f"weights: {basis}")
            v.flag(dg.index[G.notna() & dg["g"].isna()],
                   "no discrete growth: previous period missing or last year's nominal value not positive")
        v.yoy = g - 100.0
        if spec.get("level") is False:
            return v
        base = None
        for _name, w in weights:
            if w is None:
                continue
            disc = decumulate(w)
            y0 = first_full_year(disc[disc.index.year >= int(G.index.year.min()) - 1])
            if y0 is not None:
                base = disc[disc.index.year == y0]
                v.info["base"] = f"{y0} nominal ({_name})"
                break
        if base is None:
            v.info["sa"] = "no level: no nominal values to start it from"
            return v
        v.level, bridged = levels_from_yoy(g, base)
        v.flag(bridged, "level link bridged by interpolation")
        return self.finish(v, spec)

    def nominal(self, spec: dict) -> Var:
        sid, item, *sc = spec["input"]
        v = Var(spec["id"], spec["freq"], spec["block"], "nominal", self.status_of(sid))
        x = self.discrete(sid, item, sc[0] if sc else 1.0)
        if x.empty:
            v.info["missing"] = f"{sid}/{item} has no data"
            return v
        steps = [f"{sid} ({self.inp.form(sid)})"]
        if "minus" in spec:
            msid, mitem, *msc = spec["minus"]
            m = self.discrete(msid, mitem, msc[0] if msc else 1.0)
            x = (x - m.reindex(x.index)).pipe(regular)
            steps.append(f"minus {msid}")
        if spec.get("to_uzs"):
            rate = self.inp.raw("usd_rate", "USD")
            if spec["freq"] == "Q":
                rate = to_quarterly_mean(rate)
            x = (x * rate.reindex(x.index)).pipe(regular)
            steps.append("x UZS/USD (period mean)")
        if spec.get("deflate") == "cpi":
            x = deflate(x, self.cpi()).pipe(regular)
            steps.append("/ CPI (2021 = 100)")
        bad = x.index[x <= 0]
        if len(bad):
            v.flag(bad, "non-positive discrete value (cumulative value fell): set to missing")
            x = x.where(x > 0)
        v.info["steps"] = "; ".join(steps)
        v.level = x
        v.yoy = gr.yoy_pct(x)
        return self.finish(v, spec)

    def price_index(self, spec: dict) -> Var:
        ins = spec["inputs"]
        v = Var(spec["id"], spec["freq"], spec["block"], "price_index", self.status_of(ins[-1][0]))
        parts = [self.inp.raw(s, i) for s, i in ins]
        v.level = chain_mm(*parts)
        v.info["steps"] = " + ".join(f"{s}" for s, _ in ins) + ", chained, 2021 = 100"
        v.yoy = gr.yoy_pct(v.level)
        return self.finish(v, spec)

    def rate(self, spec: dict) -> Var:
        sid, item = spec["input"]
        v = Var(spec["id"], spec["freq"], spec["block"], "rate", self.status_of(sid))
        r = self.inp.raw(sid, item)
        v.level = r
        v.yoy = gr.yoy_diff(r)
        v.sa_level = r
        v.sa_growth = gr.diff(r)
        v.info["sa"] = "not adjusted (rate); SA panel holds the change in pp"
        return v

    def nag(self, spec: dict) -> Var:
        v = Var(spec["id"], spec["freq"], spec["block"], "nag", self.status_of("gdp_nag"))
        t = self.target()
        t = t[t["item"] == spec["item"]].set_index("quarter")
        if t.empty or t["growth_disc"].isna().all():
            v.info["missing"] = "no NAG data"
            return v
        idx = pd.PeriodIndex(t.index, freq="Q")
        g = pd.Series(t["growth_disc"].to_numpy(), index=idx)
        n_disc = decumulate(pd.Series(t["nominal_cum"].to_numpy(), index=idx))
        base = n_disc[n_disc.index.year == 2018]
        v.yoy = (g - 100.0).pipe(regular)
        v.level, bridged = levels_from_yoy(g, base)
        v.flag(bridged, "level link bridged by interpolation")
        v.info["base"] = "2018 nominal quarters (2018 prices)"
        v.info["weights"] = "last year's nominal quarters (NAG; 2017 backed out of published growth)"
        return self.finish(v, spec)

    def build(self, spec: dict) -> Var:
        return getattr(self, spec["recipe"])(spec)


def _long(series: pd.Series, var: Var, name: str = "value") -> pd.DataFrame:
    s = series.dropna()
    s = s[s.index >= PANEL_START[var.freq]]
    return pd.DataFrame({"variable": var.id, "freq": var.freq, "block": var.block,
                         "period": [str(p) for p in s.index], name: s.to_numpy(dtype=float)})


@dataclass
class Panels:
    as_of: dt.date
    yoy: pd.DataFrame
    sa: pd.DataFrame
    meta: pd.DataFrame
    flags: pd.DataFrame
    levels: dict[str, dict[str, pd.Series]]
    modes: dict[str, str]

    def wide(self, panel: str = "yoy", freq: str = "M", column: str = "z") -> pd.DataFrame:
        df = getattr(self, panel)
        df = df[df["freq"] == freq]
        w = df.pivot(index="period", columns="variable", values=column)
        w.index = pd.PeriodIndex(w.index, freq=freq)
        return w.sort_index()

    def save(self, out: str | Path) -> list[Path]:
        out = Path(out)
        out.mkdir(parents=True, exist_ok=True)
        paths = [out / "panel_yoy.parquet", out / "panel_sa.parquet", out / "panel_meta.csv",
                 out / "panel_flags.csv"]
        self.yoy.to_parquet(paths[0], index=False)
        self.sa.to_parquet(paths[1], index=False)
        self.meta.to_csv(paths[2], index=False)
        self.flags.to_csv(paths[3], index=False)
        return paths


def build_panels(as_of: dt.date | str | None = None, store=None, spec: list[dict] | None = None,
                 inputs: Inputs | None = None) -> Panels:
    """Both panels exactly as the data stood on ``as_of`` (default: today)."""
    as_of = dt.date.fromisoformat(str(as_of)) if as_of else dt.date.today()
    if inputs is None:
        if store is None:
            raise ValueError("pass store= (a nowcast.store.vintages.Store) or inputs=")
        inputs = load_inputs(store, as_of)
    b = Builder(inputs)
    spec = spec or load_spec()
    yoy_parts, sa_parts, meta, flags, levels = [], [], [], [], {}
    for s in spec:
        v = b.build(s)
        levels[v.id] = {"level": v.level, "sa_level": v.sa_level}
        row = {"variable": v.id, "freq": v.freq, "block": v.block, "recipe": v.recipe, "status": v.status,
               "inputs": ", ".join(str(x) for x in (s.get("input") or s.get("inputs") or [s.get("item")])),
               "weights": v.info.get("weights", ""), "base": v.info.get("base", ""),
               "steps": v.info.get("steps", ""), "sa": v.info.get("sa", ""),
               "missing": v.info.get("missing", "")}
        if v.yoy is not None and len(_long(v.yoy, v)):
            d = _long(v.yoy, v)
            d["z"], mu, sd = gr.standardise(d["value"])
            yoy_parts.append(d)
            row.update(yoy_first=d["period"].iloc[0], yoy_last=d["period"].iloc[-1], yoy_n=len(d),
                       yoy_mean=mu, yoy_sd=sd)
        if v.sa_growth is not None and len(_long(v.sa_growth, v)):
            d = _long(v.sa_growth, v)
            out = gr.robust_outliers(d["value"])
            d["outlier"] = out.to_numpy()
            d["value_clean"] = d["value"].where(~out)
            d["z"], mu, sd = gr.standardise(d["value_clean"])
            sa_parts.append(d)
            v.flag(d.loc[out, "period"], "outlier in SA panel (> 4 robust SD): value_clean missing")
            row.update(sa_first=d["period"].iloc[0], sa_last=d["period"].iloc[-1], sa_n=len(d),
                       sa_outliers=int(out.sum()), sa_mean=mu, sa_sd=sd)
        row["n_flags"] = len(v.flags)
        meta.append(row)
        flags += [{"variable": v.id, "period": p, "flag": f} for p, f in v.flags]
    yoy = pd.concat(yoy_parts, ignore_index=True)
    q = sorted(set(yoy.loc[yoy["freq"] == "Q", "period"]))
    if q:
        dm = gr.covid_dummies(pd.PeriodIndex(q, freq="Q"))
        for c in dm:
            yoy = pd.concat([yoy, pd.DataFrame({"variable": c, "freq": "Q", "block": "dummy", "period": q,
                                                "value": dm[c].to_numpy(), "z": dm[c].to_numpy()})],
                            ignore_index=True)
    sa = pd.concat(sa_parts, ignore_index=True)
    return Panels(as_of=as_of, yoy=yoy, sa=sa, meta=pd.DataFrame(meta),
                  flags=pd.DataFrame(flags, columns=["variable", "period", "flag"]),
                  levels=levels, modes=inputs.modes)


def main(argv: list[str] | None = None) -> int:
    from nowcast.store.vintages import Store

    ap = argparse.ArgumentParser(description="Build the y/y and SA panels as of a date.")
    ap.add_argument("--store", required=True)
    ap.add_argument("--as-of", default=None, help="YYYY-MM-DD (default: today)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    p = build_panels(a.as_of, store=Store(a.store))
    for path in p.save(a.out):
        print(f"wrote {path}")
    m = p.meta
    print(f"as of {p.as_of}: {m['yoy_n'].notna().sum()} variables in the y/y panel, "
          f"{m['sa_n'].notna().sum()} in the SA panel, {len(p.flags)} flags")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
