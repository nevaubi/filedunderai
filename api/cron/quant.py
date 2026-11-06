"""Technicals & Quant lab precompute (etl/quant.py). ?part=K&parts=N computes one slice of the symbols; ?step=cross
refreshes the factor and macro-calendar caches, recomputes skipped symbols and publishes the cross-section."""
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import serve  # noqa: E402


def run(sb, query):
    from etl import quant
    parts = int(query.get("parts", 2))
    if query.get("step") == "cross":
        return quant.quant_job(sb, parts=parts, step="cross", time_budget=600)
    return quant.quant_job(sb, part=int(query.get("part", 0)), parts=parts, time_budget=640)


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        serve(self, run)
