# Reference UI verification — November 4, 2026

## Release and current status

The reference visual-system change was merged in PR #9 at `6dca42bf70f0280086cc6716690bca0d783e3208`. Final feature commit: `a2bacfcc617dba10a8a94ce4df978b63b50ec01b`; implementation base: `068037cf5290071c038fe695cca151c3c0e1cdc8`.

Feature [CI run 41923000423](https://github.com/nevaubi/filedunderai/actions/runs/41923000423) and its Vercel check passed. Main [CI run 41923327790](https://github.com/nevaubi/filedunderai/actions/runs/41923327790) and [production Vercel deployment](https://vercel.com/me-3054/filedunderai/JDZ3LxR1Fji8PYqjpSnpTGdzWJeG) also passed. **Production desktop visual and interaction QA is complete for the checks below.**

## Implemented scope

- Shared warm-paper surfaces, ink/border tokens, Figtree UI text, Instrument Serif application titles, JetBrains Mono metadata, compact controls and restrained shadows.
- A 240px desktop rail with a 48px brand/top-bar baseline; Research, Markets and Workspace navigation groups; a scrolling rail body; compact watchlist rows and actual stored closing-price tape with as-of context.
- A compact company identity, quote and filing/action area; accessible company-details disclosure; original metrics on Overview; preserved company tabs, registered extensions and exact source callbacks.
- Matching analyst page headings, cards, tables, forms and disclosures; research assistant composer styling and shorter introductory copy.
- Matching workspace chrome while retaining document paper/body styles, spreadsheet cell behavior, formulas, editing history, proposal review, persistence and assistant visibility logic. Desktop workspace height consumes the shared top-bar variable.
- A final desktop-only footer correction removes the extra global footer from workspace screens above 860px, preventing its additional outer-page scroll. Mobile, print, other routes and the workspace minimum height are unchanged.

The change includes `public/professional.css`, `public/index.html`, presentation/shell functions in `public/app.js`, new `public/company.css`, `public/analyst.css`, `public/workspace.css`, focused shell/company interaction tests, and design/plan documentation. It introduces no provider, API, database, financial-model or editor-engine changes. Reference prices and transcripts were not copied into application data.

Design source: [Reference visual system](../../design/lovable-reference/DESIGN.md). Implementation plan: [Lovable reference UI plan](../../superpowers/plans/2026-11-04-lovable-reference-ui.md).

## Completed automated verification and review

| Check | Result |
|---|---|
| Full JavaScript suite | 118 passed; 0 failed, skipped or cancelled |
| Production build | Passed; 23 fingerprinted static assets |
| Footer-fix validation | Production build passed with 23 assets; diff check clean |
| Independent shared-shell review | Approved after correcting company-only watchlist route selection and guarding null quote timestamps |
| Independent company/analyst/workspace review | Spec PASS; quality APPROVE |
| Final whole-branch integration review | APPROVE; no remaining actionable or parked findings |
| Final footer fix-wave review | Addressed; APPROVE; no new breakage identified |
| Feature CI / Vercel | Passed for final feature commit |
| Main CI / Vercel | Passed for merge commit `6dca42bf70f0280086cc6716690bca0d783e3208` |

The interaction coverage includes grouped/mobile navigation, watchlist route/rerender consistency, quote values and missing dates, company actions/profile metadata, extension-tab state, exact source-record callbacks, and metric restoration when returning to Overview. Existing financial and editor regressions remain covered by the full suite. The CSS-only footer fix was followed by a build and diff check; no redundant test run was added.

The final review checked stylesheet ordering/specificity, responsive containment, event/data/rendering contracts and scope. Its two shell findings were corrected: watchlist selection now applies only to company routes, and a null closing-price timestamp cannot display an epoch date. A focused final review approved the desktop-only workspace footer fix. The temporary review workspace was removed after these conclusions and the production evidence were recorded here.

## Production desktop QA

Tested in the cloud Chrome browser at 1363 × 936 CSS pixels. Content width was 1348px on vertically scrolling pages and 1363px in the workbench. The production app loaded the extracted warm-paper token, Figtree body text, Instrument Serif app titles, 240px rail and 48px top bar. The captures are actual deployed UI, not mockups.

| Verification | Status / evidence |
|---|---|
| Deployed URL and tested commit | [Alphabet company page](https://filedunderai.vercel.app/#company-googl), merge commit `6dca42bf70f0280086cc6716690bca0d783e3208` |
| Company composition and filing actions | Serif company title, logo/ticker, closing-price date, filing/IR/EDGAR links and actions rendered. Company details expanded to show registrant/address/CIK and collapsed again. External filing actions were inspected, not submitted or modified. |
| Overview / Financials switching | Overview metrics disappeared from Financials, its table rendered, and the original metrics returned when Overview was selected again. No desktop page-width overflow. |
| Company metric evidence | Gross margin 59.7% opened the calculated-value drawer with the original period, `(Revenue − Cost of revenue) / Revenue` formula and two filed inputs. |
| Morning brief | Warm table and summary strip rendered. Revenue source and AAPL Review disclosure remained independent. Review exposed quarterly changes, filing links and research actions. Missing comparable values stayed explicit. |
| Brief evidence | Displayed AAPL revenue `$114.5B` opened raw value `114,477,000,000 USD`, accession `0000320193-26-000026`, quarter dates, and the cumulative subtraction `478,834 − 364,357 = 114,477`. This verifies the displayed-value/source relationship, not independent financial reconciliation. |
| Spreadsheet source | Model B5 retained `=FU("AAPL","Revenue","FY2024")/1000000`, displayed `391,035`, and opened the original `391,035,000,000 USD` evidence record. |
| Spreadsheet editing | Empty J40 accepted `=1+2` and displayed `3`. One Undo restored the empty value. Selection was returned to B5. |
| Document editing | White paper and Georgia body text remained. Applying 18pt to the selected word `Research` created the expected formatting span; one Undo restored the exact original document HTML. |
| Assistant | Workspace assistant hid, remained hidden when switching to Document, and reopened. Header Assistant navigation opened the dedicated styled research page. No prompt was submitted and no model output was tested. |
| Desktop workspace geometry | Workspace top48 / height888 / bottom936. Body and document scroll height both936 at viewport height936. Global footer display was none; paper/grid retained their own scroll containers. |
| Narrow screens and long names | Responsive containment and missing/long content were covered by code review and targeted DOM tests. No mobile-device screenshot or physical-device interaction test was available in this pass. |

### Screenshots

- [Company Overview](company-overview.jpg)
- [Morning brief](morning-brief.jpg)
- [Spreadsheet and assistant](spreadsheet.jpg)
- [Document editor](document-editor.jpg)

All screenshots were visually inspected and their exact captured bytes were saved. Document/spreadsheet QA edits were undone; no user-authored content was replaced.

## Remaining limitations

- The existing browser session reported that its evidence ledger could not be saved to browser storage. Source records remained available and inspectable during the session. Cross-session citation persistence is not certified by this UI pass; storage was not cleared to conceal the warning.
- This work verifies the selected visual direction and representative existing workflows. It does not add the reference demo's earnings-call reader or establish full Microsoft Office feature parity.
- API/provider availability, financial-data freshness, independent reconciliation of every figure, live AI answers, email delivery, print output and additional browser/device coverage were not tested. No API credentials, provider settings, database schema, ingestion dataset or model call changed.
- All live checks used the normal production URL. Protected-preview access and additional account grants were not requested.
