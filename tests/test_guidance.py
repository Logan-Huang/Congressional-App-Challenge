"""Tests for app/guidance.py (no network)."""

import pytest

from app.guidance import guidance_for
from tests.test_translate import BANNED, system, violation


def titles(items):
    return [i["title"] for i in items]


def check(items):
    assert 0 <= len(items) <= 4
    for i in items:
        assert i["title"] and i["text"]
        assert i["link"] is None or i["link"].startswith("https://")
        low = i["text"].lower()
        for word in BANNED:
            assert word not in low
        assert "none" not in low and "null" not in low


def one(code, **kw):
    return guidance_for(system([violation(contaminant_code=code, **kw)]))


def test_green_gets_only_gentle_general_items():
    g = guidance_for(system())
    check(g)
    assert 1 <= len(g) <= 2
    assert any("water quality report" in t.lower() for t in titles(g))


def test_lead_branch():
    g = one("1030", category_code="TT")
    check(g)
    assert g[0]["title"] == "Use a certified filter for lead"
    assert "53" in g[0]["text"] and "58" in g[0]["text"] and "cold water" in g[0]["text"]


def test_high_lead_test_adds_lead_tip_without_violation():
    g = guidance_for(system(lead_90th={"value_mg_l": 0.02, "sample_date": "2024-01-01"}))
    assert g[0]["title"] == "Use a certified filter for lead"


def test_lsl_branch_mentions_quick_check_and_inventory():
    g = one("5200", violation_code="2E", category_code="TT", rule_code="351")
    check(g)
    assert g[0]["title"] == "Check your service line for lead"
    assert "Protect Your Tap" in g[0]["text"] and "inventory" in g[0]["text"]
    assert "protect-your-tap-quick-check-lead" in g[0]["link"]


def test_nitrate_branch_says_do_not_boil():
    for code in ("1040", "1041", "1038"):
        g = one(code)
        assert "boil" in g[0]["title"].lower()
        assert "58" in g[0]["text"] and "62" in g[0]["text"] and "formula" in g[0]["text"]


def test_arsenic_branch():
    g = one("1005")
    assert "arsenic" in g[0]["title"].lower() and "58" in g[0]["text"]


@pytest.mark.parametrize("code", ["3100", "3014", "8000"])
def test_coliform_branch(code):
    g = one(code, category_code="MCL")
    assert g[0]["title"] == "Follow any boil-water notice"
    assert "rolling boil for 1 minute" in g[0]["text"]


def test_tthm_branch_mentions_tthm_but_haa5_does_not():
    g = one("2950")
    assert "53" in g[0]["text"] and "TTHM" in g[0]["text"]
    # WHY: a TTHM filter claim says nothing about HAA5, so the tip must not imply one.
    h = one("2456")
    assert "TTHM" not in h[0]["text"] and "Check the label" in h[0]["text"]


def test_paperwork_only_violations_do_not_get_boil_water_or_filter_tips_first():
    vs = [violation(contaminant_code="3100", category_code="MR", is_health_based=False, violation_id="a"),
          violation(contaminant_code="2456", category_code="MON", is_health_based=False, violation_id="b")]
    g = guidance_for(system(vs))
    check(g)
    assert titles(g)[0] == "Contact your water utility"
    assert not any("boil" in t.lower() for t in titles(g))


def test_contact_tip_tidies_names_and_hides_personal_names():
    def tip(org):
        s = system([violation(contaminant_code="7000", category_code="RPT", is_health_based=False)],
                   contact={"org_name": org, "phone": "3058662446"})
        return guidance_for(s)[0]["text"]
    assert "City of Flint at (305) 866-2446" in tip("FLINT, CITY OF")
    assert "GUILLERMO" not in tip("GUILLERMO OLMEDILLO") and "(305) 866-2446" in tip("GUILLERMO OLMEDILLO")


@pytest.mark.parametrize("code", ["4010", "4006", "4000"])
def test_radionuclide_branch(code):
    g = one(code)
    assert "reverse osmosis" in g[0]["title"].lower() and "ion exchange" in g[0]["text"]


def test_rule_code_backup_when_contaminant_unknown():
    g = guidance_for(system([violation(contaminant_code="9876", rule_code="332")]))
    assert "arsenic" in g[0]["title"].lower()


def test_health_based_topic_comes_first_and_cap_is_four():
    vs = [violation(contaminant_code="3100", is_health_based=False, violation_id="a"),
          violation(contaminant_code="1040", violation_id="b"),
          violation(contaminant_code="1005", violation_id="c"),
          violation(contaminant_code="2950", violation_id="d")]
    g = guidance_for(system(vs))
    check(g)
    assert len(g) == 4
    assert "boil-water" not in g[0]["title"]


def test_paperwork_only_gets_general_items():
    g = one("7000", category_code="RPT", is_health_based=False)
    check(g)
    assert 2 <= len(g) <= 3 and g[0]["title"] == "Contact your water utility"


def test_contact_uses_utility_phone_and_has_hotline_somewhere():
    s = system([violation(contaminant_code="7000", category_code="RPT", is_health_based=False)],
               contact={"org_name": "City of Flint", "phone": "8107662000"})
    g = guidance_for(s)
    assert "City of Flint at (810) 766-2000" in g[0]["text"]
    assert any("hotline" in i["title"].lower() for i in g)


def test_resolved_and_old_known_violations_do_not_trigger_tips():
    g = guidance_for(system([violation(status="resolved"), violation(status="known", begin_date="2001-01-01")]))
    assert len(g) <= 2 and not any("nitrate" in i["title"].lower() for i in g)


def test_missing_data_never_crashes():
    for s in ({}, {"violations": None}, {"violations": [{}], "lead_90th": {"value_mg_l": "x"}},
              {"contact": {"phone": "12"}}):
        check(guidance_for(s))
