"""Tests for app/translate.py using hand-made fixtures (no network)."""

import re

import pytest

from app.translate import translate_system

BANNED = ["toxic", "poison", "dangerous", "met all", "meets all", "is safe", "are safe", "perfectly safe"]


def violation(**kw):
    base = {
        "violation_id": "1", "contaminant_code": "1040", "violation_code": "01",
        "category_code": "MCL", "rule_code": "331", "is_health_based": True,
        "status": "open", "begin_date": "2020-07-01", "end_date": None,
        "returned_to_compliance_date": None, "measure": 12.0, "unit": "mg/L",
        "state_mcl": 10.0, "notification_tier": 2,
    }
    base.update(kw)
    return base


def system(violations=(), **kw):
    base = {
        "pwsid": "XX0000001", "name": "TEST, CITY OF", "city": "TEST", "state": "MI",
        "population_served": 1000, "match_type": "city_served",
        "total_violation_count": len(violations), "violations": list(violations),
        "lead_90th": None,
    }
    base.update(kw)
    return base


def check_common(t):
    assert 1 <= len(t["sentences"]) <= 3
    text = " ".join(t["sentences"]).lower()
    for word in BANNED:
        assert word not in text
    # No raw numeric codes like 1040 / 5000 and no "None"/"null" leaking.
    no_years = re.sub(r"\b(19|20)\d\d\b", "", text)  # years like "July 2020" are fine
    assert not re.search(r"\b\d{4}\b", no_years)
    assert "none" not in text and "null" not in text
    return text


def test_green_no_violations():
    t = translate_system(system())
    assert t["status"] == "green"
    assert t["status_label"] == "No current violations"
    assert len(t["sentences"]) == 1
    assert "no open violations" in t["sentences"][0]
    check_common(t)


def test_green_with_resolved_history():
    old = violation(status="resolved", begin_date="2019-03-15", end_date="2019-09-01")
    older = violation(status="archived", begin_date="2015-01-01")
    t = translate_system(system([old, older], total_violation_count=4))
    assert t["status"] == "green"
    assert len(t["sentences"]) == 2
    assert "4 past violations" in t["sentences"][1]
    assert "March 2019" in t["sentences"][1]
    check_common(t)


def test_green_single_past_violation_grammar():
    t = translate_system(system([violation(status="resolved")]))
    assert "1 past violation on record that is no longer open" in t["sentences"][1]


def test_green_with_lead_sentence():
    t = translate_system(system(lead_90th={"value_mg_l": 0.01, "sample_date": "2023-12-31"}))
    assert len(t["sentences"]) == 2
    assert "10 ppb" in t["sentences"][1] and "ending 2023" in t["sentences"][1]
    assert "15 ppb" in t["sentences"][1]


def test_lead_above_action_level():
    t = translate_system(system(lead_90th={"value_mg_l": 0.02, "sample_date": None}))
    assert "20 ppb" in t["sentences"][1] and "above" in t["sentences"][1]


def test_red_single_mcl_tier2():
    t = translate_system(system([violation()]))
    assert t["status"] == "red"
    assert t["status_label"] == "Active health-based violation"
    s = t["sentences"]
    assert len(s) == 3
    assert "nitrate" in s[0] and "12 mg/L" in s[0] and "10 mg/L" in s[0]
    assert "July 2020" in s[0] and "open" in s[0]
    assert "30 days" in s[1] and "not treat" in s[1]
    assert "infants" in s[2].lower()
    check_common(t)


def test_red_tier1_mentions_24_hours_and_boil_notice():
    t = translate_system(system([violation(contaminant_code="3100", category_code="MCL",
                                            measure=None, unit=None, notification_tier=1)]))
    s = t["sentences"]
    assert "germs (coliform bacteria)" in s[0]
    assert "24 hours" in s[1] and "boil-water" in s[1]
    check_common(t)


def test_red_tier_none_has_generic_context():
    t = translate_system(system([violation(notification_tier=None)]))
    assert "required to tell its customers" in t["sentences"][1]


def test_mg_l_to_ppb_for_lead():
    v = violation(contaminant_code="1030", category_code="MCL", measure=0.041, unit="mg/L",
                  notification_tier=2)
    t = translate_system(system([v]))
    assert "41 ppb" in t["sentences"][0]
    assert "15 ppb" in t["sentences"][0]
    assert "mg/L" not in t["sentences"][0]


def test_mg_l_to_ppb_for_arsenic():
    v = violation(contaminant_code="1005", measure=0.015, unit="mg/L")
    assert "15 ppb" in translate_system(system([v]))["sentences"][0]


def test_measure_not_above_limit_is_omitted():
    # A reported value below the limit would contradict "above the limit".
    v = violation(measure=3.0)
    s0 = translate_system(system([v]))["sentences"][0]
    assert "3 mg/L" not in s0 and "above the federal limit" in s0


def test_missing_measure_unit_and_dates():
    v = violation(measure=None, unit=None, begin_date=None)
    s0 = translate_system(system([v]))["sentences"][0]
    assert "nitrate" in s0 and "(still open)" in s0 and "None" not in s0
    v = violation(measure=12.0, unit=None)
    assert "12" not in translate_system(system([v]))["sentences"][0]


def test_bad_date_string_does_not_crash():
    t = translate_system(system([violation(begin_date="garbage")]))
    assert "still open" in t["sentences"][0]


def test_red_treatment_technique():
    v = violation(contaminant_code="5000", category_code="TT", measure=None, unit=None,
                  rule_code="350")
    t = translate_system(system([v]))
    assert "has not completed a required treatment step for lead and copper" in t["sentences"][0]
    check_common(t)


def test_yellow_single_monitoring():
    v = violation(category_code="MR", is_health_based=False, measure=None, unit=None,
                  notification_tier=3)
    t = translate_system(system([v]))
    assert t["status"] == "yellow"
    assert t["status_label"] == "Open paperwork/monitoring issue"
    s = t["sentences"]
    assert "missed or late testing for nitrate" in s[0]
    assert "paperwork or monitoring problem" in s[1] and "some required results are missing" in s[1]
    check_common(t)


def test_yellow_ccr_uses_paperwork_wording():
    v = violation(contaminant_code="7000", category_code="RPT", is_health_based=False,
                  measure=None, unit=None)
    s0 = translate_system(system([v]))["sentences"][0]
    assert "annual water quality report" in s0


def test_yellow_multiple():
    vs = [violation(category_code="MON", is_health_based=False, violation_id=str(i),
                    contaminant_code=c) for i, c in enumerate(["1040", "1005", "2950", "1022"])]
    t = translate_system(system(vs))
    assert t["status"] == "yellow"
    assert "4 open issues" in t["sentences"][0] and "(and 2 more)" in t["sentences"][0]
    assert "These are paperwork" in t["sentences"][1]


def test_red_multiple_health_first_and_tier_from_worst():
    paper = violation(violation_id="a", category_code="RPT", is_health_based=False,
                      contaminant_code="7000", notification_tier=3)
    health = violation(violation_id="b", notification_tier=1, contaminant_code="1005",
                       measure=0.02, unit="mg/L")
    t = translate_system(system([paper, health]))
    assert t["status"] == "red"
    s = t["sentences"]
    assert s[0].startswith("This water system has 2 open issues:")
    assert s[0].index("arsenic") < s[0].index("annual water quality report")
    assert "24 hours" in s[1]
    check_common(t)


def test_duplicate_issues_are_grouped():
    vs = [violation(violation_id=str(i)) for i in range(3)]
    s0 = translate_system(system(vs))["sentences"][0]
    assert "3 open issues, all involving nitrate above the federal limit" in s0


def test_resolved_violations_do_not_change_status():
    vs = [violation(status="resolved"), violation(status="archived", is_health_based=False)]
    assert translate_system(system(vs))["status"] == "green"


def test_open_paperwork_plus_resolved_health_is_yellow():
    vs = [violation(status="resolved"),
          violation(category_code="MR", is_health_based=False, violation_id="2")]
    assert translate_system(system(vs))["status"] == "yellow"


@pytest.mark.parametrize("category", ["MCL", "MRDL", "TT", "MR", "MON", "RPT", "Other", "Weird", None])
@pytest.mark.parametrize("health", [True, False])
def test_unknown_contaminant_never_leaks_code(category, health):
    v = violation(contaminant_code="9876", rule_code="987", category_code=category,
                  is_health_based=health, violation_code="77")
    t = translate_system(system([v]))
    text = check_common(t)
    assert "9876" not in text and "987" not in text and "77" not in text


def test_unknown_with_known_rule_code_falls_back_to_rule():
    v = violation(contaminant_code="9999", rule_code="350", category_code="MR",
                  is_health_based=False, measure=None, unit=None)
    assert "lead and copper" in translate_system(system([v]))["sentences"][0]


def test_int_contaminant_code_works():
    v = violation(contaminant_code=1040)
    assert "nitrate" in translate_system(system([v]))["sentences"][0]


def test_lead_sentence_added_to_yellow_when_room():
    v = violation(category_code="MR", is_health_based=False)
    t = translate_system(system([v], lead_90th={"value_mg_l": 0.005, "sample_date": "2022-06-30"}))
    assert len(t["sentences"]) == 3 and "ending 2022" in t["sentences"][2]


def test_sentence_count_always_1_to_3_for_many_shapes():
    codes = ["1040", "3100", "1030", "5000", "7000", "0300", "4010", "2950", "x"]
    cats = ["MCL", "TT", "MR", "RPT", "Other"]
    for code in codes:
        for cat in cats:
            for health in (True, False):
                for tier in (1, 2, 3, None):
                    v = violation(contaminant_code=code, category_code=cat,
                                  is_health_based=health, notification_tier=tier)
                    t = translate_system(system([v], lead_90th={"value_mg_l": 0.01, "sample_date": "2023-01-01"}))
                    check_common(t)


def test_empty_or_missing_violations_key():
    assert translate_system({"name": "X"})["status"] == "green"
    assert translate_system({"name": "X", "violations": None})["status"] == "green"


# ---- review-fix regressions

def test_plural_tier2_does_not_say_at_least_one():
    vs = [violation(violation_id=str(i), notification_tier=2) for i in range(2)]
    s = translate_system(system(vs))["sentences"]
    assert any("does not treat these as emergencies" in x for x in s)
    assert not any("at least one" in x for x in s)


def test_ground_water_rule_140_gets_real_name():
    v = violation(contaminant_code="0700", rule_code="140", category_code="TT")
    s0 = translate_system(system([v]))["sentences"][0]
    assert "regulated contaminant" not in s0 and "well water" in s0


def test_unknown_treatment_uses_generic_requirement_wording():
    v = violation(contaminant_code="9876", rule_code="987", category_code="TT")
    s0 = translate_system(system([v]))["sentences"][0]
    assert "regulated contaminant" not in s0 and "requirement" in s0


def test_history_sentence_is_grammatical_for_unknown_code():
    past = violation(contaminant_code="9876", rule_code="987", category_code="Other",
                     is_health_based=False, status="resolved")
    s = " ".join(translate_system(system([past]))["sentences"])
    assert "the most recent was an unmet" in s and "was not met" not in s


def test_lead_inventory_is_described_as_record_keeping():
    v = violation(contaminant_code="5200", rule_code="350", category_code="TT")
    s = translate_system(system([v]))["sentences"]
    assert "inventory of lead pipes" in s[0] and "treatment step" not in s[0]
    assert any("record-keeping" in x for x in s)


def test_yellow_with_lead_exceedance_has_no_reassurance():
    v = violation(category_code="MR", is_health_based=False, measure=None, unit=None)
    sysd = system([v])
    sysd["lead_90th"] = {"value_mg_l": 0.017, "sample_date": "2026-06-30"}
    s = translate_system(sysd)["sentences"]
    assert not any("does not by itself" in x for x in s)
    assert any("above the federal action level" in x for x in s)
