# Transformations (Phase 2)

How the raw vintages become the two model-ready panels. Code: `nowcast/transform/`; variable list:
`config/transforms.yaml`; report with one chart per variable: `notebooks/transform_report.ipynb`
(rebuild with `python tools/make_transform_report.py --store <data checkout>`).

```
store (vintages) --load_inputs(as_of)--> inputs --recipes--> y/y growth, real level --STL--> SA level
                                                                 |                          |
                                                      panel_yoy (bridges, MIDAS)   panel_sa (factor model)
```

## Panels

| Panel | Rows | `value` | Also |
| --- | --- | --- | --- |
| `panel_yoy` | 63 variables, monthly and quarterly, from 2013 | % change on the same period a year earlier (pp for interest rates) | `z` (standardised on the as-of sample); dummies `d_2020Q2`, `d_2020Q3` |
| `panel_sa` | 47 variables | 100 x log change of the seasonally adjusted level on the previous month (quarter); pp change for rates | `outlier`, `value_clean` (missing where outlier), `z` of `value_clean` |

Long format: `variable, freq, block, period, value, ...`; `Panels.wide("yoy", "M")` pivots.
`Panels.levels[var]` keeps the real and SA levels (Phase 3 turns the factor model's q/q GDP
nowcast back into y/y growth through the GDP level). The monthly workflow writes the current
panels to `panels/` on the `data` branch after every run.

## Steps

**2.1 Decumulation** (`decumulate.py`). Three cumulative forms reach the platform:

| Form | Series | Discrete period |
| --- | --- | --- |
| `cumulative_ytd` (sum from January) | nominal output and trade, exports, imports, new firms, income, FDI investment, NAG | this period minus the previous one; January as published |
| `average_ytd` (average from January) | average wages | k A_k - (k-1) A_(k-1) |
| `growth_yoy_cum` (cumulative real growth) | industry and sub-sectors, construction, retail, wholesale, market services, investment, agriculture, freight | formula below |

A missing period leaves the next one missing. A cumulative value that falls within the year
(Uzstat revises the current month without carrying the revision back; eight series do this)
gives a negative discrete value, which is set to missing and flagged.

Cumulative real growth G_k (period k of year t, 100 = no change) becomes discrete growth with last
year's nominal values as weights, the same formula as the GDP target:
R_k = G_k C_k / 100 and g_k = 100 (R_k - R_(k-1)) / (C_k - C_(k-1)), where C_k is last year's
cumulative nominal value to period k. Re-cumulating g with the same weights gives Uzstat's G back
exactly (tested for seven series). Weights in order of preference: last year's nominal values of
the series (`prev_year`); in the first year of the nominal series, this year's shares
(`same_year`); a related series (`fallback:`; e.g. total industry for a sub-sector); equal weights
(`equal`; freight only, which has no nominal series). Every period records which one it used.

**2.2 Price indices** (`levels.py`). CPI: the old-basket m/m index (SIAT 1285, 2010-2020) joined to
the COICOP-2018 index (SIAT 1286, from 2021), chained, 2021 = 100. PPI composite chained the same
way (from 2013). The plan pointed to the NSDP CPI file for 2019-2020; SIAT 1285 is the same
official series and already in the store.

**2.3 Real terms.** Uzstat's own real growth is used wherever it exists, so almost nothing is
deflated: industry (total, mining, manufacturing, electricity), construction, retail, wholesale,
14 market services and investment all come from Uzstat's cumulative real growth. Deflated by CPI
(quarterly mean for quarterly series): M2, wages, population income, FDI-financed investment, and
remittances after conversion to UZS at the period-mean official rate. Exports, imports and gold
exports stay in USD, not deflated; exports enter without gold (HS 71, from 2023) and as a total
(from 2021); the backtest checks whether UZS helps. Check: CPI-deflated wages give real
growth for 2025 (9.35%) and January-June 2026 (10.88%), against Uzstat's 9.3% and 10.9%.

**2.4 Real levels.** Each month (quarter) is chained to the same period a year earlier,
L(t,k) = L(t-1,k) g(t,k) / 100, starting from the nominal values of the first full year (2018 for
GDP and its 16 production items, so GDP is in 2018 prices). Turning the level back into y/y growth
gives g exactly (tested). A missing g would break that calendar month for all later years, so the
link across it uses the log-mean of the neighbouring periods; the period itself stays missing.
Annual-overlap chain-linking was tried for the monthly series and rejected: it made SA m/m growth
about twice as volatile and did not track the sector GVA in the GDP accounts any better.

**2.5 Seasonal adjustment** (`seasonal.py`). Robust STL on the log of the real level; seasonal
window 13 years (monthly) or 7 (quarterly), which with 5-8 years of data is close to a fixed
pattern. Series under three years are not adjusted. Not adjusted at all (no seasonal pattern
expected): exchange rate, reserves, gold price, interest rates. Market services (2024 on),
agricultural output and freight are left out of the SA panel and have no level. Moving holidays
are not modelled; the report shows a test for leftover seasonality per series, and X-13 with
holiday regressors is tried later only if it is needed.

**2.6 Growth and standardisation.** See the panel table. Standardisation uses the panel's own
sample; since `build_panels(as_of)` sees only data available on `as_of`, no future value enters it.

**2.7 Outliers.** Factor model: a value more than 4 robust standard deviations (1.4826 x MAD) from
the median is missing in `value_clean`. Bridges: dummies for 2020Q2 and 2020Q3.

**2.8 As-of builder** (`inputs.py`, `panels.py`). For each series: on or after its first vintage,
the values the store knew on that day; before it (every backtest date), the latest values cut back
by the release lag (a period is used from the 1st of the month `months_after` months after it
ends). The test `build_panels("2025-11-01")` checks that nothing released later is in the panels.

## Known limits

* **Monthly y/y growth from cumulative rates is noisy.** Uzstat used last year's nominal values as
  they stood then; we use them as published now. The re-cumulated figures match Uzstat exactly, but
  single months can be off (industry January 2023 -9.9%, March 2023 +15.4%). The level chains each
  calendar month to itself, so this noise also reaches SA m/m growth (18 outliers in total
  industry, 13 of them in 2019-2021). Quarterly averages, which the bridges use, are much smoother (since 2022, the
  standard deviation of the change in industry y/y growth is 6.4 pp monthly, 1.8 pp for quarterly means).
* **The 4-SD rule removes real shocks in smooth series**: 13 months of the exchange rate since 2018,
  including the 2019, 2020 and 2022 depreciation episodes. Phase 3 should compare the factor model
  with and without the rule for such variables.
* **Short series**: exports without gold have 3.7 years of history, so their seasonal factors are
  rough; market services have no SA version until 2027.
* **Retail in 2026**: Uzstat's cumulative real growth jumps to 125.4% in January-March 2026 and
  nominal growth to 137.2%, while the implied deflator stays at 104-109%. Both move together, so
  this looks like a coverage or method change (the SIAT note on the real table reads "for
  2021-2025"), not an error in the formula. To confirm with Uzstat before Phase 3 relies on 2026.
