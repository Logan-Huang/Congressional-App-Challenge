"""Turn a WaterSystem dict (see docs/DATA_CONTRACT.md) into plain English.

Rule-based on purpose (no ML): every sentence can be traced to an if-statement.

Tone rules (WHY): we only know there are no OPEN violations on record, so we
never say water is "safe" or "met all standards", and we avoid scary words.
Many violations are just late paperwork, and the text must say so honestly.
"""

from datetime import date

from app.codes import (
    LEAD_ACTION_LEVEL_PPB,
    UNKNOWN_NAME,
    get_contaminant,
    get_kind,
)

LABELS = {
    "green": "No current violations",
    "yellow": "Open paperwork/monitoring issue",
    "red": "Active health-based violation",
}

MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]


# ---------------------------------------------------------------- helpers

def _month_year(iso):
    """'2020-07-01' -> 'July 2020'. Returns None for anything unparseable."""
    try:
        d = date.fromisoformat(str(iso)[:10])
    except (TypeError, ValueError):
        return None
    return f"{MONTHS[d.month - 1]} {d.year}"


def _num(x):
    """Friendly number: 41.0 -> '41', 0.0123456 -> '0.0123'."""
    if abs(x) >= 100:
        return f"{x:,.0f}"
    return f"{round(x, 4):g}"


def _info(v):
    return get_contaminant(v.get("contaminant_code"), v.get("rule_code"))


def _name(v):
    info = _info(v)
    return info["name"] if info else UNKNOWN_NAME


def _is_open(v):
    return v.get("status") == "open"


def _measured_text(v):
    """'12 mg/L' or '41 ppb', or None when we cannot show it honestly.

    WHY the checks: the data sometimes lacks a unit, or reports a number that
    is not above the limit. Showing it then would be confusing or wrong.
    """
    info = _info(v)
    if info is not None and info.get("show_measure") is False:
        return None
    try:
        value = float(v.get("measure"))
    except (TypeError, ValueError):
        return None
    unit = (v.get("unit") or "").strip()
    if not unit:
        return None
    low = unit.lower()
    if low in ("ug/l", "µg/l", "μg/l", "ppb"):
        value, unit = value, "ppb"
    elif low == "mg/l":
        if info is not None and info.get("to_ppb"):
            value, unit = value * 1000, "ppb"   # people think of lead/arsenic in ppb
        else:
            unit = "mg/L"
    limit_num = info.get("limit_num") if info else None
    if limit_num is not None:
        if info.get("unit") != unit or value <= limit_num:
            return None
    return f"{_num(value)} {unit}"


def _opened(v):
    when = _month_year(v.get("begin_date"))
    return f"(open since {when})" if when else "(still open)"


def _phrase(v):
    """Short noun phrase for lists: 'nitrate above the federal limit'."""
    info = _info(v)
    name = _name(v)
    kind = get_kind(v.get("category_code"))
    if kind == "limit":
        return f"{name} above the federal limit"
    if kind == "treatment":
        if info and info.get("treatment_phrase"):
            return info["treatment_phrase"]
        if info is None:  # WHY: "a missed step for a regulated contaminant" tells the reader nothing
            return "a missed federal treatment or monitoring requirement"
        return f"a missed treatment step for {name}"
    if info and info.get("paperwork"):
        return info["paperwork"]
    if kind == "testing":
        return f"missed or late testing for {name}"
    if kind == "report":
        return f"a late or missing report about {name}"
    if info is None:
        return "an unmet federal drinking water requirement"
    return f"an unmet requirement for {name}"


def _headline(v):
    """One sentence describing a single open violation."""
    info = _info(v)
    name = _name(v)
    kind = get_kind(v.get("category_code"))
    health = bool(v.get("is_health_based"))

    if kind == "limit":
        measured = _measured_text(v)
        limit = info.get("limit") if info else None
        text = f"Testing found {name}"
        if measured:
            text += f" at {measured},"
        text += " above the federal limit"
        if limit:
            text += f" of {limit}"
        return f"{text} {_opened(v)}."
    if kind == "treatment" and (info is None or info.get("treatment_phrase")):
        return f"This water system has an open {'health-based ' if health else ''}issue: {_phrase(v)} {_opened(v)}."
    if kind == "treatment":
        return f"This water system has not completed a required treatment step for {name} {_opened(v)}."
    if health:
        return f"This water system has an open health-based issue: {_phrase(v)} {_opened(v)}."
    return f"This water system has an open issue: {_phrase(v)} {_opened(v)}."


def _sort_key(v):
    tier = v.get("notification_tier")
    return (not v.get("is_health_based"),
            tier if isinstance(tier, int) else 9,
            str(v.get("begin_date") or "9999"))


def _issue_list(violations):
    """'3 open issues: X, Y (and 1 more).' Health-based items come first."""
    ordered = sorted(violations, key=_sort_key)
    counts = {}
    for v in ordered:
        p = _phrase(v)
        counts[p] = counts.get(p, 0) + 1
    items = [p if n == 1 else f"{p} ({n} separate violations)" for p, n in counts.items()]
    total = len(violations)
    if len(items) == 1:
        # The total already says how many, so skip the "(N separate violations)" suffix.
        return f"This water system has {total} open issues, all involving {next(iter(counts))}."
    if len(items) == 2:
        shown = f"{items[0]} and {items[1]}"
    else:
        shown = f"{items[0]} and {items[1]} (and {len(items) - 2} more)"
    return f"This water system has {total} open issues: {shown}."


def _tier_sentence(tier, plural):
    """What the notification tier means, in words a parent can use."""
    this = "at least one of these" if plural else "this"
    if tier == 2 and plural:
        # WHY: the caller passes the MINIMUM tier, so with tier 2 none of them is tier 1.
        return ("EPA does not treat these as emergencies, but the utility must still notify "
                "customers within 30 days.")
    if tier == 1:
        return (f"EPA treats {this} as an immediate health concern, so the utility must notify "
                "customers within 24 hours; if you get a boil-water or do-not-drink notice, follow it.")
    if tier == 2:
        return (f"EPA does not treat {this} as an emergency, but the utility must still notify "
                "customers within 30 days.")
    return "Because this is a health-based standard, the utility is required to tell its customers about it."


def _lead_ppb(system):
    """Latest lead 90th-percentile in ppb, or None."""
    lead = system.get("lead_90th")
    if not lead:
        return None
    try:
        return float(lead.get("value_mg_l")) * 1000
    except (TypeError, ValueError):
        return None


def _lead_sentence(system):
    ppb = _lead_ppb(system)
    if ppb is None:
        return None
    lead = system["lead_90th"]
    # sample_date is the END of the monitoring period (can be in the future), so show the year only.
    year = str(lead.get("sample_date") or "")[:4]
    where = (f"In its most recent lead testing of home taps (monitoring period ending {year})"
             if year.isdigit() else "In its most recent lead testing of home taps")
    if ppb > LEAD_ACTION_LEVEL_PPB:
        return (f"{where}, the 90th-percentile result was {_num(ppb)} ppb, "
                f"above the federal action level of {LEAD_ACTION_LEVEL_PPB} ppb.")
    if ppb == 0:
        return (f"{where}, no lead was detected in the 90th-percentile result; "
                f"the federal action level is {LEAD_ACTION_LEVEL_PPB} ppb.")
    return (f"{where}, 9 out of 10 homes tested at or below {_num(ppb)} ppb; "
            f"the federal action level is {LEAD_ACTION_LEVEL_PPB} ppb.")


def _history_sentence(system, past):
    """One sentence about resolved/archived violations (green status only)."""
    if not past:
        return None
    count = max(len(past), system.get("total_violation_count") or 0)
    recent = max(past, key=lambda v: str(v.get("begin_date") or ""))
    when = _month_year(recent.get("begin_date"))
    detail = _phrase(recent) + (f", which began in {when}" if when else "")
    plural = "violations" if count != 1 else "violation"
    return (f"This system has {count} past {plural} on record that "
            f"{'are' if count != 1 else 'is'} no longer open; the most recent was {detail}.")


# ------------------------------------------------------------------ main

def translate_system(system):
    """WaterSystem dict -> {"status", "status_label", "sentences"} (1-3 sentences)."""
    violations = system.get("violations") or []
    open_v = [v for v in violations if _is_open(v)]
    past = [v for v in violations if not _is_open(v)]
    health_v = [v for v in open_v if v.get("is_health_based")]

    if not open_v:
        status = "green"
        sentences = ["There are no open violations of federal drinking water standards "
                     "on record for this water system."]
        sentences.append(_history_sentence(system, past))
        sentences.append(_lead_sentence(system))

    elif health_v:
        status = "red"
        top = sorted(health_v, key=_sort_key)[0]
        plural = len(open_v) > 1
        sentences = [_issue_list(open_v) if plural else _headline(top)]
        tier = min((v["notification_tier"] for v in health_v
                    if isinstance(v.get("notification_tier"), int)), default=None)
        sentences.append(_tier_sentence(tier, len(health_v) > 1))
        info = _info(top)
        sentences.append(info.get("health") if info else None)
        if len([s for s in sentences if s]) < 3:
            sentences.append(_lead_sentence(system))

    else:
        status = "yellow"
        plural = len(open_v) > 1
        sentences = [_issue_list(open_v) if plural else _headline(open_v[0])]
        lead_ppb = _lead_ppb(system)
        if lead_ppb is not None and lead_ppb > LEAD_ACTION_LEVEL_PPB:
            # WHY: never say "does not mean unsafe" right next to a lead exceedance.
            sentences.append("Some required tests or reports are missing or late.")
        else:
            # WHY this wording: honest about missing results without implying danger.
            sentences.append(
                ("These are paperwork or monitoring problems that do not by themselves mean the water "
                 "is unsafe, but some required results are missing.") if plural else
                ("This is a paperwork or monitoring problem that does not by itself mean the water "
                 "is unsafe, but some required results are missing."))
        sentences.append(_lead_sentence(system))

    sentences = [s for s in sentences if s][:3]
    return {"status": status, "status_label": LABELS[status], "sentences": sentences}
