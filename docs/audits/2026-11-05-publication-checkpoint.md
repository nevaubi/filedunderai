# Publication checkpoint — November 5, 2026

## Completed production repair

The bounded DIS/IDA KPI publication completed and passed full readback at **2026-11-05T19:31:49Z**.

- Removed exactly **22 DIS classifications** that incorrectly mapped expenses,
  operating profit/loss, or investee income to segment revenue.
- Preserved all **115 valid DIS mappings** and all **88 published DIS numeric
  observations**. Original source facts and documents were not changed.
- Added **one IDA revenue mapping and 16 source-verified historical points**:
  12 quarters and four annual periods covering 2009 through June 2013.
  Twelve archived original releases independently support values, periods,
  labels and the source's thousands-to-USD conversion. This is historical
  coverage; IDA's ordinary EPS options remain unresolved.
- All **11,184 unrelated database mappings** are unchanged.
- All **nine output objects** passed authenticated readback. All eight public
  objects also matched their reviewed bytes through public URLs.
- Existing company entries in the three canonical outputs are preserved:
  159 free-cash-flow entries, 10 occupancy entries, and 256 revenue entries;
  only IDA is added. These per-file counts overlap.

The repair used a renewable aggregate lease, exact before-state checks,
private immutable evidence, bounded mutation predicates, complete row
readbacks and a completion receipt. Mutation transport made one attempt per
write; an ambiguous failure required reconciliation instead of a blind retry.

## SEC IPO publication staged and independently reviewed

The original [SEC workbook](https://www.sec.gov/files/sec-stats-ipos-20260922.xlsx)
was re-downloaded and byte-checked against the reviewed fixture. The
[official landing page](https://www.sec.gov/data-research/statistics-data-visualizations/initial-public-offerings-ipos)
states a September 22, 2026 publication date; June 30 is the latest data
coverage date.

Independent openpyxl and raw-XML review passed for all **3,168 source cells**,
all **3,080 numeric observations**, and all **48 annual/quarterly series**.
The 87 dash markers and one blank remain missing. Annual coverage is
2000–2025; quarterly coverage is 2000 Q1–2026 Q2. Publication dates and
vintages consistently use September 22.

The sealed plan preserves all 330 prior registry/catalog entries, 63 calendar
entries, 12 change entries and seven sections. It adds 96 series/snapshot
files and updates only registry and catalog. All 108 staged artifacts and
98 planned output objects passed hash and length checks. Four SPAC snapshots
trim 34 leading missing display rows; their complete cell evidence remains.

**The IPO data has not been published.** Its exact-period frontend correction
must be live first, followed by a fresh under-lease preflight. The previous
production deployment attempt failed during Vercel resource provisioning,
without build logs, at 19:21:48 UTC. At 19:22:35 UTC production still served
`32d913b064226515f0048865ab9cb235c08507ce`; tested main was
`e877cf8d5bf4cbe807e2c1196cf53c8e534ed35b`. This checkpoint is being committed
for the requested production release; deployment status is verified separately.

## Missing PDF text: complete native-text classification

All **34 archived PDFs**, totaling **155,074,245 bytes**, match the recorded
length and SHA-256. PyMuPDF and Poppler independently found zero native text
across all **447 pages**, and each page contains images. All files opened
without parser repair or opening passwords. One UTHR document has owner
permissions; the other 33 are unencrypted.

| Company | PDFs | Pages |
|---|---:|---:|
| ATI | 23 | 342 |
| ROKU | 1 | 1 |
| SNX | 1 | 15 |
| UTHR | 9 | 89 |
| Total | 34 | 447 |

There is no native-text repair to publish. OCR derivatives and their visual
verification are a separate remaining step. No OCR or production document
mutation was performed. The 20 previously held CW XLS files also remain
without extracted text, as verified at 19:31:45 UTC.

## Verification and scope

The exact pre-checkpoint main passed **251 JavaScript tests under Node 22**,
**517 Python financial tests**, and the Node 22 production build, which emitted
35 fingerprinted assets. This checkpoint adds audit records only. It does not
certify every company in the broader backfill or clear existing source holds.
Production source snapshots and credentials are excluded from this repository.
