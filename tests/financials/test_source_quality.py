import copy
import unittest

from etl.source_quality import partition_supported_periods, retain_proved_filing_tables


class SourceQualityTests(unittest.TestCase):
    def point(self, start="2026-07-01", end="2026-09-30", filed="2026-10-30"):
        return {"start":start,"end":end,"filed":filed,"v":100,"c":"Revenues","accn":"0001660134-26-000001","form":"10-Q"}

    def statements(self, points):
        return {"income":[{"label":"Revenue","concept":"Revenues","unit":"USD","annual":[],"quarterly":points,
                           "filing_history":{"annual":[],"quarterly":copy.deepcopy(points)}}]}

    def test_future_period_is_explicitly_retained_in_quarantine(self):
        good = self.point()
        bad = self.point(end="2027-09-30")
        original = self.statements([good,bad])
        before = copy.deepcopy(original)
        clean, excluded = partition_supported_periods(original)
        self.assertEqual(original,before)
        self.assertEqual(clean["income"][0]["quarterly"],[good])
        self.assertEqual(len(excluded),2)
        self.assertTrue(all(x["reason"] == "period_ends_after_filing" and x["point"] == bad for x in excluded))
        self.assertTrue(all(x["label"] == "Revenue" and x["unit"] == "USD" for x in excluded))

    def test_transition_is_not_a_quarter_but_fourteen_weeks_is(self):
        transition = self.point("2026-02-01","2026-05-31","2026-06-15")
        fiscal = self.point("2026-01-01","2026-04-08","2026-05-01")
        clean, excluded = partition_supported_periods(self.statements([transition,fiscal]))
        self.assertEqual(clean["income"][0]["quarterly"],[fiscal])
        self.assertTrue(all(x["reason"] == "nonstandard_quarter_duration" for x in excluded))

    def test_sixteen_week_reported_quarter_is_not_quarantined(self):
        quarter=self.point('2025-12-29','2026-04-19','2026-05-20')
        clean,excluded=partition_supported_periods(self.statements([quarter]))
        self.assertEqual(excluded,[])
        self.assertEqual(clean['income'][0]['quarterly'],[quarter])

    def test_invalid_derived_leg_is_quarantined_without_changing_other_values(self):
        value = self.point()
        value["derived"] = {"from":self.point(end="2027-09-30"),"minus":self.point()}
        clean, excluded = partition_supported_periods(self.statements([value,self.point()]))
        self.assertEqual(len(clean["income"][0]["quarterly"]),1)
        self.assertTrue(all(x["reason"] == "period_ends_after_filing" for x in excluded))

    def test_other_validation_errors_are_preserved_for_strict_gate(self):
        invalid = self.point()
        invalid["v"] = float("nan")
        invalid["c"] = "Cash"
        clean, excluded = partition_supported_periods(self.statements([invalid]))
        self.assertEqual(excluded,[])
        self.assertEqual(len(clean["income"][0]["quarterly"]),1)

    def test_rebuilt_source_tables_cannot_reintroduce_quarantined_values(self):
        tables = {"acc": {"statements": {"income": {"cols":["2026-07-01|2026-09-30","2027-07-01|2027-09-30"],
            "rows":[{"label":"Revenue","concept":"Revenues","unit":"USD",
                     "vals":{"2026-07-01|2026-09-30":100,"2027-07-01|2027-09-30":200}}]}}}}
        proof = {"records": {"/facts/us-gaap/Revenues/units/USD/0": {"accn":"acc","concept":"Revenues","unit":"USD",
            "start":"2026-07-01","end":"2026-09-30","val":"100"}}}
        result, removed = retain_proved_filing_tables(tables,proof)
        row = result["acc"]["statements"]["income"]["rows"][0]
        self.assertEqual(row["vals"],{"2026-07-01|2026-09-30":100})
        self.assertEqual(removed,1)
        self.assertEqual(row["source_records"]["2026-07-01|2026-09-30"],["/facts/us-gaap/Revenues/units/USD/0"])
        self.assertEqual(len(tables["acc"]["statements"]["income"]["cols"]),2)


if __name__ == "__main__":
    unittest.main()
