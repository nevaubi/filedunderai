import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import serve  # noqa: E402


def run(sb, query):
    """Insiders & influence pipeline (gov/ and flows/ bundles). step = daily | hearings | company | holders | reads
    | form144 (proposed insider sales)
    | registry (company registry: GLEIF LEIs and children, FIGIs, CUSIPs; ~500 companies per run at GLEIF's rate)."""
    from etl import gov_pipeline
    step = query.get("step", "daily")
    if step == "form144":
        from etl import form144
        return form144.form144_job(sb, days=int(query.get("days", 365)), time_budget=640)
    if step == "registry":
        from etl import entities, supply
        out = entities.registry_job(sb, workers=2, time_budget=560)
        out["supply"] = supply.normalize_graph_job(sb)
        return out
    if step not in ("daily", "hearings", "company", "holders", "reads"):
        raise ValueError("step must be daily, hearings, company, holders, reads, form144 or registry")
    return gov_pipeline.scheduled(sb, step, time_budget=680)


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        serve(self, run)
