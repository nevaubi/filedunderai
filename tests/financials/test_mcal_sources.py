import unittest
from unittest.mock import patch

from etl import mcal

FOMC = '''<div class="panel-heading"><h4><a id="1">2026 FOMC Meetings</a></h4></div>
<div class="fomc-meeting__month col-xs-5"><strong>October</strong></div>
<div class="fomc-meeting__date col-xs-4">27-28</div>
<a href="/monetarypolicy/fomcminutes20261028.htm">HTML</a> (Released November 18, 2026)
<div class="fomc-meeting__month col-xs-5"><strong>Dec</strong></div>
<div class="fomc-meeting__date col-xs-4">8-9*</div>
<div class="panel-heading"><h4><a id="2">2025 FOMC Meetings</a></h4></div>
<div class="fomc-meeting__month col-xs-5"><strong>August</strong></div>
<div class="fomc-meeting__date col-xs-4">22 (notation vote)</div>
'''

BEA = '''<th>Year 2026</th>
<div class="release-date">December 23</div>
<small class="text-muted">8:30 AM</small></td><td></td><td class="release-title x">GDP (Third Estimate), Industries, 3rd Quarter 2026 </td>
<div class="release-date">December 23</div>
<small class="text-muted">10:00 AM</small></td><td></td><td class="release-title x">Outdoor Recreation, 2025 </td>
<div class="release-date">January 9</div>
<small class="text-muted">8:30 AM</small></td><td></td><td class="release-title x">Personal Income and Outlays, November 2026 </td>
'''

CENSUS = '''<tr height="20">
<td height="20"><a href="/retail">Advance Monthly Sales for Retail and Food Services</a></td>
<td sorttable_customkey="202701150830">January 15, 2027</td>
<td>8:30 AM</td>
<td>December 2026</td>
</tr>
<tr height="20">
<td height="20"><a href="/x">Quarterly Services Survey</a></td>
<td sorttable_customkey="202701151000">January 15, 2027</td>
<td>10:00 AM</td>
<td>Q3 2026</td>
</tr>'''


class McalSources(unittest.TestCase):
    def test_fomc_decisions_minutes_and_sep(self):
        with patch.object(mcal, "request", return_value=FOMC):
            rows = mcal.fomc()
        got = {(r["release"], r["release_date"]): r for r in rows}
        self.assertIn(("FOMC:DECISION", "2026-10-28"), got)
        self.assertEqual(got[("FOMC:MINUTES", "2026-11-18")]["title"], "Minutes of the FOMC meeting of October 28")
        self.assertIn("Summary of Economic Projections", got[("FOMC:DECISION", "2026-12-09")]["title"])
        # minutes not yet scheduled on the page fall back to three weeks after the decision
        self.assertIn(("FOMC:MINUTES", "2026-12-30"), got)
        self.assertFalse(any(r["release_date"].startswith("2025-08") for r in rows))

    def test_bea_rolls_year_and_filters(self):
        with patch.object(mcal, "request", return_value=BEA):
            rows = mcal.bea()
        self.assertEqual([(r["release"], r["release_date"], r["release_time"]) for r in rows],
                         [("BEA:GDP", "2026-12-23", "8:30 a.m. ET"), ("BEA:PIO", "2027-01-09", "8:30 a.m. ET")])

    def test_census_parses_and_merges_pages(self):
        with patch.object(mcal, "request", return_value=CENSUS):
            rows = mcal.census()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["release"], "CEN:RETAIL")
        self.assertEqual(rows[0]["release_date"], "2027-01-15")
        self.assertTrue(rows[0]["title"].endswith("December 2026"))


if __name__ == "__main__":
    unittest.main()
