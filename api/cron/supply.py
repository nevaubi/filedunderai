import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import serve  # noqa: E402


def run(sb, query):
    # step=rotate (default): read companies never processed, then those with a new 10-K/10-Q; step=intel: regions,
    # relationship news, sympathy moves and AI insights for companies whose links changed. Both stay inside 680 s.
    from etl import supply
    shards = int(query["shards"]) if str(query.get("shards", "")).isdigit() else 1
    shard = int(query["shard"]) if str(query.get("shard", "")).isdigit() else 0
    if not 0 <= shard < shards:
        raise ValueError("shard must be between 0 and shards-1")
    return supply.run(sb, step=query.get("step", "rotate"), shard=shard, shards=shards)


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        serve(self, run)
