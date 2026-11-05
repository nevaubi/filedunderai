# SEC IPO period and publication correction

## Scope

This forward fix builds on `3e3c056`. It keeps the existing `SEC_IPO:` IDs,
48 series, 3,080 observations, source display precision, and raw XML tokens
in `snap.cells`. It changes no numeric observation, source cell, scheduled
job, generic chart, or release-calendar behavior.

The annual and quarterly tables now use their respective `COUNT_TOTAL`
latest periods. All 24 measures remain visible per frequency. An unavailable
value appears as a dash at that period; an older average is never carried
forward. Where a measure's latest date differs, the table checks history for
an exact match. Numeric buttons open that measure's exact series and period.
Normal tables with matching dates still render directly from the catalog.

## Official release evidence

The [SEC IPO page](https://www.sec.gov/data-research/statistics-data-visualizations/initial-public-offerings-ipos)
links the reviewed workbook and explicitly dates its Citation to September
22, 2026. June 30 is data coverage. The guide defines U.S.-market IPOs by
pricing date, issuer geography by primary offices at offering, and underlying
data as LSEG SDC Platinum and Dealogic ECM. SEC remains the publisher.

The collector stores structured publication evidence in metadata and the
source snapshot. It applies `published=2026-09-22` only to the reviewed URL
and SHA-256 `8499e48223bb72d93cd5919a4f3b2f84776d020b120e3133d992841da5ba5450`.
Other bytes or URLs retain an unknown publication date. A review cutoff
before the proven publication is rejected. Existing `mstore` therefore uses
September 22 as this release's observation vintage; retrieval remains a
separate timestamp.

## Publication handoff

No production write or sealed-stage edit is part of this change. The previous
stage contains `published=null` and retrieval-date vintages in several
artifacts. It must be regenerated from this collector, reviewed, and resealed
before publication. Changing a JSON field in the old stage would leave its
database rows, bundle vintages, statistics, snapshots, and source manifest
inconsistent. Old stage and review candidates remain preserved.

The separate offline review artifact compares every observation and every
raw source-cell record against the base collector. That comparison must show
identical IDs, dates, values, source snapshot rows, keys, and cell records;
only release and descriptive metadata differ.

## Verification

Focused DOM regressions reproduce the old missing-row and mismatched-period
behavior before the fix, then verify visible missing rows, exact-date history
lookup, zero values, and exact source-panel selection after it. Collector
tests verify the official date, URL/hash binding, future-publication cutoff,
and existing parsing safeguards. The actual workbook remains unchanged.

Final local verification: 247 JavaScript tests and 501 Python financial tests
passed. The offline comparison confirmed all 48 IDs, 3,080 observations, and
3,168 original source-cell records and snapshot rows were identical to the
base collector. The five focused DOM tests and eleven collector tests passed.
