"""Hourly AI insight loops, alternating by hour:
- even hours: filing insights (etl/filing_insights.py): companies whose disclosure redline is newer than their last
  insight run, most recent filings first; drafts, verifies, checks and ranks, then writes insights/{T}.json;
- odd hours: KPI insights (etl/kpi_insights.py): detectors over IR KPIs, guidance, federal contracts, lobbying, trading
  and congressional records, then verified insights, written to kpi_insights/{T}.json and kpi_insights/index.json.
?step=filing or ?step=kpi forces one."""
import datetime as dt
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import serve  # noqa: E402


def run(sb, query):
    step = query.get("step") or ("filing" if dt.datetime.now(dt.timezone.utc).hour % 2 == 0 else "kpi")
    if step == "kpi":
        from etl import kpi_insights
        return {"step": "kpi", **kpi_insights.run(sb, time_budget=700, per_run=int(query.get("n", "30")), workers=4)}
    from etl import filing_insights
    return {"step": "filing", "result": filing_insights.run(sb, time_budget=700, max_companies=int(query.get("n", "20")))}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        serve(self, run)
