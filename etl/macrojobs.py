"""Jobs for the macro series store: one per source, each ending with a catalog rebuild.

python -m etl.run mdata --source frb_weekly [--full] [--force]
Sources: frb_daily, frb_weekly, frb_monthly, nyfed, treasury, bls, oecd, cftc, fdic, calendar, legacy, catalog.
"""
import datetime as dt

from . import bls, cftc, fdic, frb, mcal, mstore, nyfed, oecd, treasury
from .common import Run

SOURCES = ["frb_daily", "frb_weekly", "frb_monthly", "nyfed", "treasury", "bls", "oecd", "cftc", "fdic", "calendar", "legacy", "catalog"]


def _ago(days):
    return (dt.date.today() - dt.timedelta(days=days)).isoformat()


def _state(sb):
    try:
        return sb.get_json("macro/state.json") or {}
    except Exception:
        return {}


def _publish(sb, touched, log=print):
    reg = mstore.load_registry(sb)
    reg.update(touched)
    return mstore.publish_catalog(sb, reg, log=log)


def mdata_job(sb, source, full=False, force=False, log=print):
    with Run(sb, f"mdata:{source}") as run:
        touched, saved = {}, {}
        base = mstore.load_registry(sb)
        if source.startswith("frb_"):
            state = _state(sb)
            for key, url, lm, series in frb.collect(frb.GROUPS[source[4:]], state, force=force or full, log=log):
                saved.update(mstore.save(sb, series, touched, prev_reg=base, log=log))
                if lm:
                    state[url] = lm
                    sb.put_json("macro/state.json", state, cache="no-store")
        elif source == "nyfed":
            start = "2008-01-01" if full else _ago(45)
            saved.update(mstore.save(sb, nyfed.rates(start, dt.date.today().isoformat()), touched, prev_reg=base, partial=not full, log=log))
            saved.update(mstore.save(sb, nyfed.operations("2013-09-01" if full else _ago(45), dt.date.today().isoformat()), touched, prev_reg=base, partial=not full, log=log))
            saved.update(mstore.save(sb, nyfed.soma(), touched, prev_reg=base, log=log))
            saved.update(mstore.save(sb, nyfed.dealers(), touched, prev_reg=base, log=log))
        elif source == "treasury":
            y = dt.date.today().year
            years = treasury.years_since(2000) if full else ([y - 1, y] if dt.date.today().month == 1 else [y])
            saved.update(mstore.save(sb, treasury.collect(years, log=log), touched, prev_reg=base, partial=not full, log=log))
        elif source == "bls":
            y = dt.date.today().year
            saved.update(mstore.save(sb, bls.collect(2007 if full else y - 1, log=log), touched, prev_reg=base, partial=not full, log=log))
        elif source == "oecd":
            for code in oecd.INDICATORS:
                try:
                    saved.update(mstore.save(sb, oecd.indicator_series(code), touched, prev_reg=base, log=log))
                except Exception as e:
                    log(f"oecd {code} failed: {str(e)[:200]}")
                    run.stats.setdefault("failed", []).append(code)
        elif source == "cftc":
            saved.update(mstore.save(sb, cftc.collect("2008-01-01" if full else _ago(70), log=log), touched, prev_reg=base, partial=not full, log=log))
        elif source == "fdic":
            saved.update(mstore.save(sb, fdic.collect(log=log), touched, prev_reg=base, log=log))
        elif source == "calendar":
            rows = mcal.collect(log=log)
            run.stats["calendar"] = mstore.save_releases(sb, rows, log=log)
        elif source == "legacy":
            run.stats["legacy"] = migrate_legacy(sb, log=log)
        elif source != "catalog":
            raise ValueError(f"unknown source {source}")
        run.stats.update({"series": len(saved), "new_obs": sum(saved.values()), "catalog": _publish(sb, touched, log=log)})
        return run.stats


# ---------------------------------------------------------------- legacy macro.json series (etl/macro.py)
LEGACY = {
    "FRED:CPIAUCSL": ("CPI-U, all items (FRED copy of BLS CUSR0000SA0)", "inflation", "Index 1982-84=100", "M", "SA", "https://fred.stlouisfed.org/series/CPIAUCSL", "Federal Reserve Bank of St. Louis (FRED), from BLS"),
    "FRED:CPILFESL": ("CPI-U less food and energy (FRED copy of BLS CUSR0000SA0L1E)", "inflation", "Index 1982-84=100", "M", "SA", "https://fred.stlouisfed.org/series/CPILFESL", "Federal Reserve Bank of St. Louis (FRED), from BLS"),
    "FRED:UNRATE": ("Unemployment rate (FRED copy of BLS LNS14000000)", "labor", "Percent", "M", "SA", "https://fred.stlouisfed.org/series/UNRATE", "Federal Reserve Bank of St. Louis (FRED), from BLS"),
    "FRED:PAYEMS": ("Nonfarm payrolls level (FRED copy of BLS CES0000000001)", "labor", "Thousands of employees", "M", "SA", "https://fred.stlouisfed.org/series/PAYEMS", "Federal Reserve Bank of St. Louis (FRED), from BLS"),
    "FRED:DFF": ("Federal funds effective rate, daily (FRED)", "rates", "Percent", "D", None, "https://fred.stlouisfed.org/series/DFF", "Federal Reserve Bank of St. Louis (FRED), from the Federal Reserve Board H.15"),
    "FRED:T10Y2Y": ("10-year minus 2-year Treasury constant maturity (FRED)", "rates", "Percentage points", "D", None, "https://fred.stlouisfed.org/series/T10Y2Y", "Federal Reserve Bank of St. Louis (FRED)"),
    "FED:EFFR": ("Federal funds effective rate, monthly average (H.15 via DBnomics)", "rates", "Percent", "M", None, "https://www.federalreserve.gov/releases/h15/", "Federal Reserve Board via DBnomics"),
    "TSY:10Y": ("Treasury 10-year par yield (legacy series)", "rates", "Percent", "D", None, "https://home.treasury.gov/resource-center/data-chart-center/interest-rates", "U.S. Department of the Treasury"),
    "TSY:2Y": ("Treasury 2-year par yield (legacy series)", "rates", "Percent", "D", None, "https://home.treasury.gov/resource-center/data-chart-center/interest-rates", "U.S. Department of the Treasury"),
}


def legacy_meta(sid):
    title, section, units, freq, sa, url, provider = LEGACY[sid]
    return {"id": sid, "source": "macro.json (legacy macro job)", "provider": provider, "title": title, "units": units, "frequency": freq,
            "seasonal_adj": sa, "geography": "United States", "source_url": url, "data_url": url, "source_key": sid.split(":", 1)[1],
            "section": section, "release": None, "legacy": True, "hidden": True, "no_changes": True,
            "notes": "Series kept from the earlier macro job (table macro_observations); the Macro page uses the official-source series instead."}


def legacy_series_from_rows(rows):
    by = {}
    for r in rows:
        if r["series"] in LEGACY:
            by.setdefault(r["series"], []).append((str(r["d"])[:10], str(r["value"])))
    return [{"meta": legacy_meta(sid), "obs": sorted(obs)} for sid, obs in by.items()]


def migrate_legacy(sb, log=print):
    """Copies macro_observations into the series store. Requires the store tables."""
    if not mstore.db_ready(sb):
        log("legacy migration needs the macro_series tables; skipped")
        return 0
    rows = sb.select("macro_observations", "select=series,d,value&order=series,d")
    series = legacy_series_from_rows(rows)
    touched = {}
    out = mstore.save(sb, series, touched, log=log)
    reg = mstore.load_registry(sb)
    reg.update(touched)
    sb.put_json("macro/registry.json", reg, cache="no-store")
    return sum(out.values())


def legacy_store(sb, obs_rows, log=print):
    """Called by jobs.macro_job: mirrors the rows it writes to macro_observations into the series store."""
    touched = {}
    out = mstore.save(sb, legacy_series_from_rows(obs_rows), touched, log=log)
    _publish(sb, touched, log=log)
    return sum(out.values())
