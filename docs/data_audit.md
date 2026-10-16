# Data audit (Phase 0, task 0.6)

Audit of the two seed workbooks (14 Oct 2026) and of the sources they came from. Full per-series
tables: `docs/audit_monthly.csv`, `docs/audit_quarterly.csv` (rebuild with `python tools/audit.py`).

## Findings that change the design

| # | Finding | Consequence |
| --- | --- | --- |
| 1 | Uzstat publishes **real** growth for industry (SIAT 562, 28 sub-sectors, from 2019), construction (2406), retail (2700) and wholesale (2696). | These replace deflating nominal series by PPI or CPI. Nominal tables stay as weights and cross-checks. |
| 2 | These real growth rates are **cumulative** (January to month, against the same period last year), like GDP. | Monthly real growth is recovered with the same previous-year-weights formula as the GDP target. Construction and market services forms are confirmed in Phase 2. |
| 3 | Eight cumulative nominal series **fall within a year** at least once, e.g. computers 2023-05 (2,153.6 to 1,333.2), wholesale 2023-11 and 2025-09, fabricated metals 2020-08. Revisions are not carried back to earlier months. | Decumulated months can be negative. Sub-sectors use the real index; a negative decumulated month is flagged and set to missing. |
| 4 | **Gold drives exports.** HS 71 was 45.5% of goods exports in Q1 2025 and 4.0% in Q1 2026 (USD 3.7 bn to 0.2 bn). That is the Q1 2026 drop of real exports to 58.0% of a year earlier. | Exports enter without gold; gold exports are their own series (SIAT 3080, from 2023-01). |
| 5 | **No monthly agriculture** series exists on SIAT or NSDP (only quarterly, from 2022 Q4). Freight turnover is quarterly from 2024 Q1 only; electricity output in kWh is annual only. | Agriculture is carried by the quarterly NAG sector GVA. Electricity uses the industry index sub-sector. Freight is a candidate only. |
| 6 | **Market services** start in 2024-01 in both the growth (3218) and level (3217) tables, with a method break from 2024-09 (informal economy included). | Services block gets its history from quarterly GVA of the service sectors (2018 Q1 on), as agreed on 16 Oct. |
| 7 | PPI "mining of metal ores" is **0 for all of 2019** and normal from 2020-01 (the plan wrongly said zero throughout). | 2019 zeros are treated as missing; the series is kept. |
| 8 | PPI January 2019 jumps (composite 108.8, metallurgy 171.9, coke and petroleum 134.4) after price liberalisation. | Outlier dummy for 2019-01. |
| 9 | The old CPI table (SIAT 1285, 2010-2020) labels periods with a Cyrillic "М" (2010-М01). The COICOP-2018 CPI (1286) starts in 2021-01. | Parser normalises the label; the two are spliced for 2019-2020. |
| 10 | The NSDP file labelled "Exchange rates" (cbu.uz/sdmx/EXR_Uzbekistan_STA.xml) actually holds **external debt** data. | The UZS/USD rate comes from the central bank's daily rate archive (JSON) instead. |
| 11 | Remittances were **revised back to 2020 Q1** on 29 Sep 2026 (new estimates of travellers' income), and H1 2025 GDP by use was revised the same day. | Confirms that every vintage must be kept. |

## Seed workbook against today's sources

| Seed series | Source checked | Result |
| --- | --- | --- |
| M2 | cbu.uz DCS SDMX, FDSB_XDC | identical (2019-01, 2024-06, 2026-08) |
| FX reserves, gold reserves | cbu.uz IR SDMX, RAFAFX_USD, RAFAGOLD_USD | identical |
| 4 interest rates | cbu.uz INR SDMX, FITD/FILR NC HH/B WA | identical |
| Remittances | cbu.uz BoP analytical xlsx, "Secondary income, credits" | identical for 2025 Q3 to 2026 Q2 |
| Average wage | SIAT 522 | identical |
| GDP growth | NAG file and SIAT 3698 | identical (seed rounded to 0.1) |

## Forms of the seed series

| Group | Series | Form |
| --- | --- | --- |
| Industry by activity, construction, retail, exports, imports, new small businesses | monthly | cumulative from January, nominal |
| CPI (17 groups), PPI (15 items) | monthly | % of previous month |
| Market services (14 types) | monthly | % of same period last year, from 2024 |
| M2, reserves, rates, gold price, operating enterprises | monthly | level |
| Remittances, average wage | quarterly | discrete |
| Investment growth, GDP growth | quarterly | % of same period last year, cumulative |
| FDI-financed investment, population income | quarterly | cumulative from Q1, nominal |

## GDP target (task 0.7)

`nowcast/transform/target.py` rebuilds discrete-quarter and cumulative real growth for GDP, GVA and
15 sub-items from the NAG file. Cumulative growth matches Uzstat's published figures to 0.00003 pp
for every item and quarter, 2018 Q1 to 2026 Q2. Discrete Q2 2026 GDP growth is 8.40%. Adding the
discrete quarters back up with last year's nominal weights reproduces the cumulative figure exactly.
For 2018, last year's nominal values are recovered from the published (unrounded) growth rates.
