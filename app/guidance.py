"""Practical "what can I do" tips for a water system (see docs/DATA_CONTRACT.md, Layer 2).

WHY this is its own file: the tips are facts we looked up (EPA, NSF), not
something we compute, so keeping them in one place makes them easy to check
and update. Every tip is phrased as an option ("you can"), never an order,
and we avoid medical advice beyond what EPA and CDC say.

Sources: EPA lead pages and "Protect Your Tap: A Quick Check for Lead",
EPA Emergency Disinfection of Drinking Water (boil-water steps),
EPA Table of Regulated Drinking Water Contaminants, EPA Consumer Confidence
Report page, NSF certified drinking water treatment units database.
"""

from app.codes import CONTAMINANTS
from app.names import display_name
from app.rules import counts_as_health_based, is_current

MAX_ITEMS = 4

# Which contaminant codes belong to which topic (codes checked against
# data/sdwa_ref_codes.csv). A rule code is a backup when the contaminant code is odd.
TOPICS = {
    "lead": {"contaminants": {"1030", "5000"}, "rules": {"350"}},
    "lsl": {"contaminants": {"5200"}, "rules": {"351", "352"}},
    "nitrate": {"contaminants": {"1038", "1040", "1041"}, "rules": {"331"}},
    "arsenic": {"contaminants": {"1005"}, "rules": {"332"}},
    "coliform": {"contaminants": {"3100", "3014", "3013", "3000", "8000"}, "rules": {"110", "111"}},
    "dbp": {"contaminants": {"2950", "2456", "0400", "0600"}, "rules": {"210", "220"}},
    "radio": {"contaminants": {"4000", "4006", "4010"}, "rules": {"340"}},
}

LEAD_ACTION_LEVEL_MG_L = 0.015

LINKS = {
    "lead": "https://www.epa.gov/ground-water-and-drinking-water/basic-information-about-lead-drinking-water",
    "quick_check": "https://www.epa.gov/ground-water-and-drinking-water/protect-your-tap-quick-check-lead",
    "regulated": "https://www.epa.gov/ground-water-and-drinking-water/table-regulated-drinking-water-contaminants",
    "rules": "https://www.epa.gov/ground-water-and-drinking-water/national-primary-drinking-water-regulations",
    "boil": "https://www.epa.gov/ground-water-and-drinking-water/emergency-disinfection-drinking-water",
    "dbp": "https://www.epa.gov/dwreginfo/stage-1-and-stage-2-disinfectants-and-disinfection-byproducts-rules",
    "ccr": "https://www.epa.gov/ccr/ccr-information-consumers",
    "hotline": "https://www.epa.gov/ground-water-and-drinking-water/safe-drinking-water-hotline",
    "nsf": "https://info.nsf.org/Certified/DWTU/",
}

# One tip per topic. Topics that need a filter are listed in FILTER_TOPICS so we can
# point to NSF's certified product search.
TIPS = {
    "lead": {
        "title": "Use a certified filter for lead",
        "text": ("If you want extra protection, you can use a filter certified to NSF/ANSI Standard 53 "
                 "(or a reverse osmosis system certified to Standard 58) for lead, and replace its cartridge "
                 "on schedule. Use cold water for drinking, cooking, and baby formula, since hot water can "
                 "pull more lead from pipes and boiling does not remove lead. If water has sat in the pipes "
                 "for hours, running the tap for a bit first can also help."),
        "link": LINKS["lead"],
    },
    "lsl": {
        "title": "Check your service line for lead",
        "text": ("EPA's Protect Your Tap quick check shows you how to find out what material the pipe "
                 "bringing water into your home is made of. You can also ask your utility for its service "
                 "line inventory, which lists which pipes are known or may contain lead."),
        "link": LINKS["quick_check"],
    },
    "nitrate": {
        "title": "Do not boil water to remove nitrate",
        "text": ("Boiling does not remove nitrate and can make it more concentrated. Nitrate matters most "
                 "for babies under six months, so if you mix infant formula with tap water, you may want to "
                 "ask your doctor or your utility about it. Reverse osmosis (NSF/ANSI Standard 58) and "
                 "distillation (Standard 62) systems can reduce nitrate."),
        "link": LINKS["regulated"],
    },
    "arsenic": {
        "title": "Look for a filter certified for arsenic",
        "text": ("Reverse osmosis systems certified to NSF/ANSI Standard 58 for arsenic reduction can lower "
                 "arsenic in drinking water. Check the label, because not every filter is certified for "
                 "arsenic."),
        "link": LINKS["regulated"],
    },
    "coliform": {
        "title": "Follow any boil-water notice",
        "text": ("If your utility issues a boil-water notice, EPA says to bring water to a rolling boil for "
                 "1 minute, then let it cool before drinking, cooking, or brushing teeth. Watch for notices "
                 "from your utility, and follow the exact steps in any notice you receive."),
        "link": LINKS["boil"],
    },
    "dbp": {
        "title": "A carbon filter may reduce disinfection byproducts",
        "text": ("Some carbon filters are certified under NSF/ANSI Standard 53 to reduce TTHM, one kind of "
                 "disinfection byproduct. Check the label for the specific claim, since not every carbon "
                 "filter is certified for it."),
        "link": LINKS["dbp"],
    },
    "radio": {
        "title": "Reverse osmosis or ion exchange can reduce radioactive elements",
        "text": ("Home reverse osmosis systems (NSF/ANSI Standard 58) and ion exchange systems can reduce "
                 "radium and uranium. Check the product label to see which contaminants it is certified "
                 "to reduce."),
        "link": LINKS["rules"],
    },
}

FILTER_TOPICS = {"lead", "nitrate", "arsenic", "dbp", "radio"}


# ---------------------------------------------------------------- helpers

KNOWN_CONTAMINANTS = {c for spec in TOPICS.values() for c in spec["contaminants"]}


def _topics_for(v):
    """Set of topic names a violation belongs to."""
    code = str(v.get("contaminant_code") or "").strip()
    rule = str(v.get("rule_code") or "").strip()
    if code in KNOWN_CONTAMINANTS:
        return {t for t, spec in TOPICS.items() if code in spec["contaminants"]}
    if code in CONTAMINANTS:
        return set()   # a contaminant we know that has no tip (e.g. the annual report)
    # Rule code is only a backup for contaminant codes we do not recognize.
    return {t for t, spec in TOPICS.items() if rule in spec["rules"]}


def _specific_topics(system):
    """(primary, secondary) topic lists, each most relevant first.

    primary   = health-based violations and a high lead test result.
    secondary = topics that only come from paperwork/monitoring violations. WHY separate:
                a late test is not a reason to put filter or boil-water advice first.
    """
    current = [v for v in (system.get("violations") or []) if is_current(v)]
    current.sort(key=lambda v: not counts_as_health_based(v))
    primary, secondary = [], []
    for v in current:
        for topic in sorted(_topics_for(v)):
            if topic == "coliform" and not counts_as_health_based(v):
                continue  # a boil-water tip for a late germ test would be alarmist
            # The lead-pipe check is calm, relevant advice, so it leads even though an
            # inventory gap is record-keeping.
            bucket = primary if counts_as_health_based(v) or topic == "lsl" else secondary
            if topic not in primary and topic not in secondary:
                bucket.append(topic)
    # A high lead test result matters even without a lead violation row.
    lead = system.get("lead_90th") or {}
    try:
        if float(lead.get("value_mg_l")) > LEAD_ACTION_LEVEL_MG_L and "lead" not in primary:
            if "lead" in secondary:
                secondary.remove("lead")
            primary.append("lead")
    except (TypeError, ValueError):
        pass
    return primary, secondary


def _phone(raw):
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    return None


# Words that mean the contact "org" is a real utility, not a person's name.
ORG_WORDS = {"CITY", "TOWN", "VILLAGE", "WATER", "UTILITY", "UTILITIES", "DEPARTMENT", "DEPT", "DISTRICT",
             "AUTHORITY", "COUNTY", "SYSTEM", "SYSTEMS", "BOARD", "COMMISSION", "COMPANY", "CO", "INC",
             "LLC", "PUBLIC", "WORKS", "SERVICE", "SERVICES", "MUNICIPAL", "BOROUGH", "TOWNSHIP", "PARK",
             "ASSOCIATION", "COOPERATIVE", "CORP", "CORPORATION", "SANITARY", "SEWER", "MUTUAL", "OF",
             "VILLAGE", "HOMES", "ESTATES", "MOBILE", "HOA", "PWS", "MUD", "PUD"}


def contact_label(system):
    """Readable utility name for the contact tip, or None if it looks like a person's name."""
    org = ((system.get("contact") or {}).get("org_name") or "").strip()
    if not org:
        return None
    words = {w.strip(",.()").upper() for w in org.split()}
    if not words & ORG_WORDS and len(org.split()) <= 3 and all(w.replace("'", "").isalpha() for w in org.split()):
        return None   # e.g. "GUILLERMO OLMEDILLO": a person, not something to print in a sentence
    return display_name(org)   # 'FLINT, CITY OF' -> 'City of Flint'


def _contact_tip(system):
    contact = system.get("contact") or {}
    label = contact_label(system)
    phone = _phone(contact.get("phone"))
    if phone and label:
        reach = f"You can reach {label} at {phone}."
    elif phone:
        reach = f"The phone number on file for this water system is {phone}."
    elif label:
        reach = f"Look for the phone number for {label} on your water bill."
    else:
        reach = "Look for your water utility's phone number on your water bill."
    return {
        "title": "Contact your water utility",
        "text": (f"{reach} Utilities can tell you how they are handling any open issue, what your water "
                 "is tested for, and whether your service line is lead."),
        "link": None,
    }


def _general_tips(system, wants_filter):
    ccr = {
        "title": "Read your annual water quality report",
        "text": ("Every community water system must send customers a yearly Consumer Confidence Report "
                 "listing what was found in the water. Check your utility's website or use EPA's page "
                 "to learn how to find it."),
        "link": LINKS["ccr"],
    }
    hotline = {
        "title": "Call the EPA Safe Drinking Water Hotline",
        # WHY no number here: we could not confirm it on EPA's page, so the page is the source.
        "text": ("EPA runs a Safe Drinking Water Hotline for questions about drinking water. "
                 "Its page lists the current ways to reach it."),
        "link": LINKS["hotline"],
    }
    nsf = {
        "title": "Search for certified filters",
        "text": ("NSF's online database lets you look up filters by the contaminants they are certified "
                 "to reduce, so you can check a product before you buy it."),
        "link": LINKS["nsf"],
    }
    return [_contact_tip(system), ccr, nsf if wants_filter else hotline, hotline]


# ------------------------------------------------------------------ main

def _tip(topic, system):
    tip = dict(TIPS[topic])
    if topic == "dbp":
        # Only the TTHM violation (code 2950) can honestly point to a TTHM filter claim.
        current = [v for v in (system.get("violations") or []) if is_current(v)]
        if not any(str(v.get("contaminant_code")) == "2950" for v in current):
            tip["text"] = ("Some carbon filters list a claim for reducing disinfection byproducts. "
                           "Check the label for the specific claim, since not every filter has one.")
    return tip


def guidance_for(system):
    """WaterSystem dict -> list of {"title", "text", "link"}, 0-4 items, most relevant first."""
    primary, secondary = _specific_topics(system)
    if not primary and not secondary:
        has_current = any(is_current(v) for v in (system.get("violations") or []))
        general = _general_tips(system, wants_filter=False)
        if has_current:
            return [general[0], general[1], general[2]]
        # Green: gentle, optional reading only.
        return [general[1], general[0]]

    if primary:
        topics = primary[:3]
        tips = [_tip(t, system) for t in topics]
        wants_filter = any(t in FILTER_TOPICS for t in primary)
    else:
        # Paperwork only: contact and the annual report come first, one topic tip after.
        general = _general_tips(system, wants_filter=False)
        tips = [general[0], general[1]] + [_tip(t, system) for t in secondary[:2]]
        return tips[:MAX_ITEMS]
    for extra in _general_tips(system, wants_filter):
        if len(tips) >= MAX_ITEMS:
            break
        if extra["title"] not in [t["title"] for t in tips]:
            tips.append(extra)
    return tips[:MAX_ITEMS]
