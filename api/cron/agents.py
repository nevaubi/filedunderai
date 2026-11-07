"""Agents scheduler tick (etl/agents/tick.py): due monitors, due scheduled tasks, weekly "insights for you" runs and
email digests for the partner app's users. Every 10 minutes; ?uid= limits a run to one user (manual checks)."""
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import serve  # noqa: E402


def run(sb, query):
    from etl.agents import tick
    return tick.tick(sb, time_budget=int(query.get("budget", "700")), only_uid=query.get("uid") or None,
                     force_insights=query.get("insights") == "1")


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        serve(self, run)
