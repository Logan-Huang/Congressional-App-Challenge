"""Plain-English lookup tables for EPA SDWIS codes.

WHY this file exists: the EPA data is full of numeric codes ("1040", "5000").
Nobody should ever see those, so translate.py looks every code up here and
falls back to generic wording when it does not know one.

How to add a code: copy an entry in CONTAMINANTS and edit it. Fields:
  name       short plain name that reads well after "for" or "found ..."
  limit      the federal limit in friendly units (text), or None
  limit_num  the same limit as a number, in `unit` (used to sanity-check data)
  unit       the unit `limit_num` is in ("ppb", "mg/L", "pCi/L", ...)
  to_ppb     True if the API reports this in mg/L but people think in ppb
  health     ONE complete sentence paraphrasing EPA's health-effects wording
  paperwork  optional wording used when the violation is a paperwork issue
  treatment_phrase  optional wording used instead of "a missed treatment step for <name>"

Sources: EPA National Primary Drinking Water Regulations table, and the
official code list in data/sdwa_ref_codes.csv (every curated code below was
checked against it). Codes we have no curated entry for fall back to the
official description, made readable, so nothing shows as a raw code.
"""

import csv
import os

# Lead action level. The tighter 10 ppb level from the Lead and Copper Rule
# Improvements does not take effect until November 2027, so we use 15 ppb.
LEAD_ACTION_LEVEL_PPB = 15

# Used when a contaminant code is not in the table. Never show a raw code.
UNKNOWN_NAME = "a regulated contaminant"

CONTAMINANTS = {
    # ---- Germs -----------------------------------------------------------
    "3100": {  # Total coliform (Revised Total Coliform Rule)
        "name": "germs (coliform bacteria)",
        "limit": None, "limit_num": None, "unit": None, "to_ppb": False,
        "show_measure": False,  # counts of positive samples are not meaningful to readers
        "health": ("Coliform bacteria are usually not harmful themselves, but they can be a "
                   "sign that other germs that cause illness may be in the water."),
    },
    "3014": {  # E. coli
        "name": "E. coli bacteria",
        "limit": None, "limit_num": None, "unit": None, "to_ppb": False,
        "show_measure": False,
        "health": ("E. coli can be a sign of contamination with human or animal waste, which "
                   "can cause diarrhea, cramps, nausea, and headaches."),
    },
    # ---- Inorganic chemicals --------------------------------------------
    "1005": {"name": "arsenic", "limit": "10 ppb", "limit_num": 10, "unit": "ppb", "to_ppb": True,
             "health": ("Over many years, drinking water with arsenic above the limit can cause skin "
                        "damage and circulation problems, and may raise the risk of cancer.")},
    "1010": {"name": "barium", "limit": "2 mg/L", "limit_num": 2, "unit": "mg/L", "to_ppb": False,
             "health": "Over many years, barium above the limit can raise blood pressure."},
    "1015": {"name": "cadmium", "limit": "5 ppb", "limit_num": 5, "unit": "ppb", "to_ppb": True,
             "health": "Over many years, cadmium above the limit can damage the kidneys."},
    "1020": {"name": "chromium", "limit": "100 ppb", "limit_num": 100, "unit": "ppb", "to_ppb": True,
             "health": "Some people who drink water with chromium above the limit for many years get allergic skin reactions."},
    "1022": {"name": "copper", "limit": "1.3 mg/L (action level)", "limit_num": 1.3, "unit": "mg/L", "to_ppb": False,
             "health": ("Short-term, copper above the action level can cause stomach and intestinal upset; "
                        "over many years it can harm the liver or kidneys.")},
    "1025": {"name": "fluoride", "limit": "4 mg/L", "limit_num": 4, "unit": "mg/L", "to_ppb": False,
             "health": ("Over many years, fluoride above the limit can cause bone problems, and children "
                        "may get discolored teeth.")},
    "1030": {"name": "lead", "limit": "15 ppb (action level)", "limit_num": 15, "unit": "ppb", "to_ppb": True,
             "health": ("Lead can slow physical and mental development in babies and children, and over "
                        "many years can cause kidney problems and high blood pressure in adults.")},
    "1035": {"name": "mercury", "limit": "2 ppb", "limit_num": 2, "unit": "ppb", "to_ppb": True,
             "health": "Over many years, mercury above the limit can damage the kidneys."},
    "1038": {"name": "nitrate", "limit": "10 mg/L", "limit_num": 10, "unit": "mg/L", "to_ppb": False,
             "health": ("Infants under six months who drink water with nitrate above the limit can become "
                        "seriously ill, with trouble getting enough oxygen (sometimes called blue baby syndrome).")},
    "1040": {"name": "nitrate", "limit": "10 mg/L", "limit_num": 10, "unit": "mg/L", "to_ppb": False,
             "health": ("Infants under six months who drink water with nitrate above the limit can become "
                        "seriously ill, with trouble getting enough oxygen (sometimes called blue baby syndrome).")},
    "1041": {"name": "nitrite", "limit": "1 mg/L", "limit_num": 1, "unit": "mg/L", "to_ppb": False,
             "health": ("Infants under six months who drink water with nitrite above the limit can become "
                        "seriously ill, with trouble getting enough oxygen (sometimes called blue baby syndrome).")},
    "1045": {"name": "selenium", "limit": "50 ppb", "limit_num": 50, "unit": "ppb", "to_ppb": True,
             "health": "Over many years, selenium above the limit can cause hair or fingernail loss and circulation problems."},
    "1074": {"name": "antimony", "limit": "6 ppb", "limit_num": 6, "unit": "ppb", "to_ppb": True,
             "health": "Antimony above the limit can raise blood cholesterol and lower blood sugar."},
    "1085": {"name": "thallium", "limit": "2 ppb", "limit_num": 2, "unit": "ppb", "to_ppb": True,
             "health": "Over many years, thallium above the limit can cause hair loss and problems with the blood, kidneys, intestines, or liver."},
    # ---- Disinfection byproducts and disinfectants ----------------------
    "2950": {"name": "disinfection byproducts (TTHM)", "limit": "80 ppb", "limit_num": 80, "unit": "ppb", "to_ppb": True,
             "health": ("Over many years, drinking water with TTHM above the limit may cause liver, kidney, "
                        "or nervous system problems and may raise the risk of cancer.")},
    "2456": {"name": "disinfection byproducts (haloacetic acids)", "limit": "60 ppb", "limit_num": 60, "unit": "ppb", "to_ppb": True,
             "health": "Over many years, drinking water with HAA5 above the limit may raise the risk of cancer."},
    "1009": {"name": "chlorite", "limit": "1 mg/L", "limit_num": 1, "unit": "mg/L", "to_ppb": False,
             "health": "Chlorite above the limit can affect the blood in young children and in unborn babies."},
    "1011": {"name": "bromate", "limit": "10 ppb", "limit_num": 10, "unit": "ppb", "to_ppb": True,
             "health": "Over many years, bromate above the limit may raise the risk of cancer."},
    "0999": {"name": "chlorine (added to kill germs)", "limit": "4 mg/L", "limit_num": 4, "unit": "mg/L", "to_ppb": False,
             "health": "At high levels, chlorine can irritate the eyes and nose and upset the stomach."},
    "1006": {"name": "chloramine (added to kill germs)", "limit": "4 mg/L", "limit_num": 4, "unit": "mg/L", "to_ppb": False,
             "health": "At high levels, chloramine can irritate the eyes and nose and upset the stomach."},
    # ---- Radionuclides ---------------------------------------------------
    "4000": {"name": "radioactivity (gross alpha)", "limit": "15 pCi/L", "limit_num": 15, "unit": "pCi/L", "to_ppb": False,
             "health": "Over many years, alpha radiation above the limit may raise the risk of cancer."},
    "4010": {"name": "radium", "limit": "5 pCi/L", "limit_num": 5, "unit": "pCi/L", "to_ppb": False,
             "health": "Over many years, radium above the limit may raise the risk of cancer."},
    "4006": {"name": "uranium", "limit": "30 ppb", "limit_num": 30, "unit": "ppb", "to_ppb": True,
             "health": "Over many years, uranium above the limit may raise the risk of cancer and can harm the kidneys."},
    # ---- Whole rules (the "contaminant" is really a rule) ---------------
    "5000": {"name": "lead and copper", "limit": None, "limit_num": None, "unit": None, "to_ppb": False,
             "health": ("Lead can slow physical and mental development in babies and children, and over "
                        "many years can cause kidney problems and high blood pressure in adults."),
             "paperwork": "missed or late lead and copper testing or reporting"},
    # 5200 is EPA's "Lead and Copper Rule Revisions" (verified in sdwa_ref_codes.csv). Most
    # of its violations are record-keeping; the inventory one (violation code 2E) is
    # handled in VIOLATION_OVERRIDES below.
    "5200": {"name": "the lead service line rules", "limit": None, "limit_num": None, "unit": None, "to_ppb": False,
             "treatment_phrase": "a missed step under the lead service line rules",
             "health": ("These rules are about finding and replacing lead service pipes and telling "
                        "customers about them. By themselves they do not mean lead was found in the water."),
             "paperwork": "missed lead service line reporting or notices"},
    "7000": {"name": "the annual water quality report", "limit": None, "limit_num": None, "unit": None, "to_ppb": False,
             "health": None,
             "paperwork": "a late annual water quality report"},
}

# Treatment rules. Each code below was checked against data/sdwa_ref_codes.csv:
#   0200 Surface Water Treatment Rule, 0300 Interim Enhanced SWTR,
#   0400 Stage 1 and 0600 Stage 2 Disinfectants and Disinfection Byproducts Rules,
#   0500 Filter Backwash Rule, 0700 Groundwater Rule.
_SWTR = {
    "name": "germ removal from river or lake water",
    "limit": None, "limit_num": None, "unit": None, "to_ppb": False,
    "health": ("These treatment steps protect against germs such as Giardia and Cryptosporidium, "
               "which can cause stomach illness."),
}
for _code in ("0200", "0300"):
    CONTAMINANTS[_code] = _SWTR

CONTAMINANTS["0500"] = {
    "name": "treatment of recycled filter backwash water",
    "limit": None, "limit_num": None, "unit": None, "to_ppb": False,
    "health": ("Treating the water recycled from filter cleaning helps keep germs such as "
               "Cryptosporidium out of drinking water."),
}

_DBP_RULES = {
    "name": "disinfection byproduct rules",
    "limit": None, "limit_num": None, "unit": None, "to_ppb": False,
    "health": ("Disinfection byproducts form when chlorine reacts with natural material in water; over "
               "many years, high levels may raise cancer risk and affect the liver, kidneys, or nervous system."),
}
for _code in ("0400", "0600"):
    CONTAMINANTS[_code] = _DBP_RULES

_GWR = {
    "name": "germ protection for well water",
    "limit": None, "limit_num": None, "unit": None, "to_ppb": False,
    "health": "These steps protect well water from germs that can cause stomach illness.",
}
CONTAMINANTS["0700"] = _GWR

CONTAMINANTS["8000"] = CONTAMINANTS["3100"]   # Revised Total Coliform Rule

# Special cases keyed by (contaminant_code, violation_code). The same contaminant code can mean
# different things depending on the violation, so these are checked first.
VIOLATION_OVERRIDES = {
    ("5200", "2E"): {   # "LSL Inventory"
        "name": "the lead service line inventory", "limit": None, "limit_num": None, "unit": None, "to_ppb": False,
        # Wins over the generic "missed treatment step" wording: an inventory is record-keeping.
        "treatment_phrase": "an unfinished inventory of lead pipes",
        "health": ("This is a record-keeping step: the utility must identify which service pipes "
                   "may contain lead. It does not mean lead was found in the water."),
        "paperwork": "an unfinished inventory of lead pipes",
    },
}

# Fallback by rule code when the contaminant code is unknown. Every rule code here was
# checked against the official list (RULE_CODE rows).
RULE_TO_CONTAMINANT = {
    "110": "3100", "111": "8000",            # Total Coliform Rule / Revised Total Coliform Rule
    "121": "0200", "122": "0300", "123": "0300",  # Surface water treatment rules
    "130": "0500",                           # Filter Backwash Rule
    "140": "0700",                           # Ground Water Rule
    "210": "0400", "220": "0600",            # Stage 1 / Stage 2 DBP rules
    "331": "1040",                           # Nitrate Rule
    "332": "1005",                           # Arsenic Rule
    "350": "5000",                           # Lead and Copper Rule
    "351": "5200", "352": "5200",            # Lead and Copper Rule Revisions / Improvements
    "420": "7000",                           # Consumer Confidence Rule
}

# What the category code means, as a short phrase for sentences.
CATEGORY_KIND = {
    "MCL": "limit",    # measured above a maximum contaminant level
    "MRDL": "limit",   # disinfectant above its maximum residual level
    "TT": "treatment", # required treatment technique not followed
    "MR": "testing",   # monitoring/reporting
    "MON": "testing",
    "RPT": "report",
    "Other": "other",
}

CATEGORY_TEXT = {
    "MCL": "a contaminant was measured above its federal limit",
    "MRDL": "a disinfectant (such as chlorine) was measured above its federal limit",
    "TT": "a required water treatment step was not completed",
    "MR": "a required test or report was missed or late",
    "MON": "a required test was missed or late",
    "RPT": "a required report was missed or late",
    "Other": "another federal requirement was not met",
}


# ---- Official names (data/sdwa_ref_codes.csv) ---------------------------------

_REF_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "data", "sdwa_ref_codes.csv")
_ref_cache = None


def _official(value_type, code):
    """Official EPA description for a code, or None (file missing, code unknown)."""
    global _ref_cache
    if _ref_cache is None:
        _ref_cache = {}
        try:
            with open(_REF_PATH, newline="", encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    _ref_cache[(row["VALUE_TYPE"], row["VALUE_CODE"].strip())] = row["VALUE_DESCRIPTION"].strip()
        except (OSError, KeyError, csv.Error):
            pass  # WHY: a missing reference file must never break the page; we just use generic wording
    return _ref_cache.get((value_type, code))


def _readable(description):
    """'Endosulfan II' -> 'endosulfan ii'; rules keep their proper names: 'the Public Notice Rule'."""
    d = description.strip()
    if " rule" in d.lower() or d.lower().endswith(("rule", "rules", "revisions")):
        return "the " + (d.title() if d.isupper() else d)
    if d.isupper():
        return d.lower()
    words = [w if (len(w) > 1 and w.isupper()) else w.lower() for w in d.split()]
    return " ".join(words)


# Official rule names are legal jargon. When an official name contains one of these
# fragments we show the plain wording instead (the rule name itself never reaches the page).
PLAIN_RULE_NAMES = [
    ("long term 2", "germ removal from river or lake water"),
    ("surface water treatment", "germ removal from river or lake water"),
    ("ground water rule", "germ protection for well water"),
    ("groundwater rule", "germ protection for well water"),
    ("consumer confidence", "the annual water quality report"),
]


def _generic_info(name):
    """Info dict for a code we have an official name for but no curated wording."""
    for fragment, plain in PLAIN_RULE_NAMES:
        if fragment in name.lower():
            name = plain
            break
    return {"name": name, "limit": None, "limit_num": None, "unit": None, "to_ppb": False,
            "show_measure": False, "health": None}


def get_contaminant(contaminant_code, rule_code=None, violation_code=None):
    """Return the info dict for a code, or None if we know nothing about it.

    Order: special violation cases, curated contaminant, official contaminant name
    (made readable), curated rule fallback, official rule name.
    """
    code = str(contaminant_code).strip() if contaminant_code is not None else ""
    vcode = str(violation_code).strip().upper() if violation_code is not None else ""
    if (code, vcode) in VIOLATION_OVERRIDES:
        return VIOLATION_OVERRIDES[(code, vcode)]
    if code in CONTAMINANTS:
        return CONTAMINANTS[code]
    # WHY official name before the rule fallback: a contaminant's own name is more specific than its rule.
    official = _official("CONTAMINANT_CODE", code) if code else None
    if official:
        return _generic_info(_readable(official))
    rule = str(rule_code).strip() if rule_code is not None else ""
    if rule in RULE_TO_CONTAMINANT:
        return CONTAMINANTS[RULE_TO_CONTAMINANT[rule]]
    official = _official("RULE_CODE", rule) if rule else None
    if official:
        return _generic_info(_readable(official))
    return None


def get_kind(category_code):
    """'limit' | 'treatment' | 'testing' | 'report' | 'other' (unknown -> 'other')."""
    return CATEGORY_KIND.get(str(category_code).strip() if category_code else "", "other")
