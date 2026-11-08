"""Run a scheduled job on a long-running machine (GitHub Actions) instead of a Vercel function, for jobs that outgrow
Vercel's memory or time limits. Waits for the aggregate lease like the cron endpoints do.

    python -m etl.actions_job screener
    python -m etl.actions_job reap
"""
import argparse
import json
import time

from .common import LeaseBusy, Supabase


def _screener(sb, a):
    from . import jobs
    return jobs.screener_job(sb, full=a.full, time_budget=a.budget, workers=a.workers)


def _reap(sb, a):
    from .common import reap_stale_runs
    return reap_stale_runs(sb)


JOBS = {"screener": _screener, "reap": _reap}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("job", choices=sorted(JOBS))
    ap.add_argument("--budget", type=float, default=3000, help="seconds")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--lease-wait", type=float, default=900)
    a = ap.parse_args()
    sb = Supabase()
    t0 = time.time()
    while True:
        try:
            out = JOBS[a.job](sb, a)
            break
        except LeaseBusy:
            if time.time() - t0 > a.lease_wait:
                raise
            time.sleep(20)
    print(json.dumps(out, default=str)[:4000], flush=True)


if __name__ == "__main__":
    main()
