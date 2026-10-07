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
from app.guidance import guidance_for
from app.rules import counts_as_health_based, is_current

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
    return get_contaminant(v.get("contaminant_code"), v.get("rule_code"), v.get("violation_code"))


def _name(v):
    info = _info(v)
    return info["name"] if info else UNKNOWN_NAME


def _is_known(v):
    """EPA "Known" = never returned to compliance (we do not call it fixed)."""
    return v.get("status") == "known"


def _adj(v):
    return "unresolved" if _is_known(v) else "open"


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
    if _is_known(v):
        # WHY: "Known" means EPA never saw it returned to compliance; it is not "fixed".
        return f"(since {when}; it has not been marked resolved)" if when else "(it has not been marked resolved)"
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
    health = counts_as_health_based(v)

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
        return f"This water system has an {_adj(v)} {'health-based ' if health else ''}issue: {_phrase(v)} {_opened(v)}."
    if kind == "treatment":
        return f"This water system has not completed a required treatment step for {name} {_opened(v)}."
    if health:
        return f"This water system has an {_adj(v)} health-based issue: {_phrase(v)} {_opened(v)}."
    return f"This water system has an {_adj(v)} issue: {_phrase(v)} {_opened(v)}."


def _sort_key(v):
    tier = v.get("notification_tier")
    return (not counts_as_health_based(v),
            tier if isinstance(tier, int) else 9,
            str(v.get("begin_date") or "9999"))


def _issue_list(violations):
    """'3 open issues: X and Y.' / '9 open issues, including X and Y.' Health-based first."""
    ordered = sorted(violations, key=_sort_key)
    counts = {}
    for v in ordered:
        p = _phrase(v)
        counts[p] = counts.get(p, 0) + 1
    items = [p if n == 1 else f"{p} ({n} separate violations)" for p, n in counts.items()]
    total = len(violations)
    word = "open" if all(not _is_known(v) for v in violations) else "unresolved"
    if len(items) == 1:
        # The total already says how many, so skip the "(N separate violations)" suffix.
        return f"This water system has {total} {word} issues, all involving {next(iter(counts))}."
    if len(items) == 2:
        return f"This water system has {total} {word} issues: {items[0]} and {items[1]}."
    # WHY "including": "(and 5 more)" next to "22 issues" read as if the numbers didn't add up.
    return f"This water system has {total} {word} issues, including {items[0]} and {items[1]}."


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
    # WHY: old "Known" ones were never marked resolved, so "no longer open" would overstate.
    gone = "no longer open" if all(v.get("status") in ("resolved", "archived") for v in past)         else "no longer counted as current"
    return (f"This system has {count} past {plural} on record that "
            f"{'are' if count != 1 else 'is'} {gone}; the most recent was {detail}.")


# ------------------------------------------------------------------ main

def translate_system(system):
    """WaterSystem dict -> {"status", "status_label", "sentences"} (1-3 sentences)."""
    violations = system.get("violations") or []
    open_v = [v for v in violations if is_current(v)]   # shared rule: open, or recent "known"
    past = [v for v in violations if not is_current(v)]
    health_v = [v for v in open_v if counts_as_health_based(v)]

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
        elif all(str(v.get("contaminant_code")) == "5200" for v in open_v):
            # Only lead-pipe inventory gaps: explain what an inventory is instead.
            info = _info(open_v[0])
            sentences.append(info.get("health") if info else None)
        else:
            # WHY this wording: honest about missing results without implying danger.
            sentences.append(
                ("These are paperwork or monitoring problems that do not by themselves mean the water "
                 "is unsafe, but some required results are missing.") if plural else
                ("This is a paperwork or monitoring problem that does not by itself mean the water "
                 "is unsafe, but some required results are missing."))
        sentences.append(_lead_sentence(system))

    sentences = [s for s in sentences if s][:3]
    return {"status": status, "status_label": LABELS[status], "sentences": sentences,
            "trend": _safe(_trend, system, default=NO_TREND),
            "guidance": _safe(guidance_for, system, default=[])}


def _safe(fn, arg, default):
    """WHY: trend and guidance are extras; a bug in them must never take the whole page down."""
    try:
        return fn(arg)
    except Exception:  # noqa: BLE001
        return default


# ------------------------------------------------------------------ trend

NO_TREND = {"direction": "no_history", "sentence": "We don't have enough history for this water system to show a trend."}


def _count(n, noun):
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _meaningful(recent, prior):
    """True when two counts differ by 2+, or one is zero and the other is not."""
    return abs(recent - prior) >= 2 or (recent != prior and 0 in (recent, prior))


def _trend(system):
    """Compare the last 5 calendar years with the 5 before (history.by_year, see contract)."""
    rows = ((system.get("history") or {}).get("by_year")) or []
    years = [r for r in rows if isinstance(r, dict) and isinstance(r.get("year"), int)]
    if not years:
        return dict(NO_TREND)
    last = max(r["year"] for r in years)

    def total(key, lo, hi):
        return sum(int(r.get(key) or 0) for r in years if lo <= r["year"] <= hi)

    h_new, h_old = total("health_based", last - 4, last), total("health_based", last - 9, last - 5)
    o_new, o_old = total("other", last - 4, last), total("other", last - 9, last - 5)

    if h_new + h_old + o_new + o_old == 0:
        return {"direction": "steady", "sentence": "No violations are on record for this water system over the last 10 years."}
    if _meaningful(h_new, h_old):
        if h_new < h_old:
            text = (f"In the last 5 years this system had {_count(h_new, 'health-based violation')}, "
                    f"down from {h_old} in the 5 years before.")
            if _has_current(system, True):
                # WHY: a violation that began years ago and is still open is missing from the
                # "recent" count, so "improving" alone would sound too good.
                return {"direction": "steady",
                        "sentence": text + " Some health-based issues from earlier years are still open."}
            return {"direction": "improving", "sentence": text}
        # Calm wording: counts only, no alarm words.
        return {"direction": "worsening",
                "sentence": (f"This system had more health-based violations in the last 5 years "
                             f"({h_new}) than in the 5 years before ({h_old}).")}
    if _meaningful(o_new, o_old):
        # WHY softer: paperwork changes say little about the water itself.
        word = "fewer" if o_new < o_old else "more"
        return {"direction": "steady",
                "sentence": (f"Health-based violations were about the same as before, but there were {word} "
                             f"paperwork or monitoring violations in the last 5 years ({o_new} compared with {o_old}).")}
    if h_new + h_old == 0:
        return {"direction": "steady",
                "sentence": "No health-based violations are on record over the last 10 years, only paperwork or monitoring ones."}
    return {"direction": "steady",
            "sentence": (f"Health-based violations were about the same in the last 5 years ({h_new}) "
                         f"as in the 5 years before ({h_old}).")}


# ---------------------------------------------------------------- details
# translate_details turns the "insights" (comparison, state report, housing age)
# into sentences. Every part may be None, and each part is handled on its own.

def _per_100(pct):
    """2.4 -> 'about 2 in 100'. None stays None."""
    try:
        pct = float(pct)
    except (TypeError, ValueError):
        return None
    if pct < 0.5:
        return "fewer than 1 in 100"
    return f"about {max(1, round(pct))} in 100"


def _has_current(system, health_only):
    for v in (system.get("violations") or []):
        if is_current(v) and (counts_as_health_based(v) or not health_only):
            return True
    return False


def _state_place(state):
    """'District of Columbia' -> 'the District of Columbia'."""
    place = state.get("name") or state.get("code")
    return f"the {place}" if place == "District of Columbia" else place


def _comparison_sentences(comparison, system):
    if not comparison:
        return []
    state = comparison.get("state") or {}
    nation = comparison.get("national") or {}
    s_rate = _per_100(state.get("pct_current_health_based"))
    n_rate = _per_100(nation.get("pct_current_health_based"))
    if system.get("violations", []) is None:
        mine = ""   # violations unknown: say nothing about this system rather than guess
    else:
        mine = ("; in federal records, this system does" if _has_current(system, True)
                else "; in federal records, this system does not")
    out = []
    place = _state_place(state)
    # WHY a count for small states: "25 in 100" for a state with 4 systems is misleading.
    try:
        few = int(state.get("systems")) < 20
        k_state = round(float(state["pct_current_health_based"]) * int(state["systems"]) / 100)
    except (TypeError, ValueError, KeyError):
        few, k_state = False, None
    if s_rate and place:
        if few and k_state is not None:
            text = (f"{k_state} of {state['systems']} water systems in {place} "
                    f"{'has' if k_state == 1 else 'have'} a current health-based violation")
        else:
            text = f"{s_rate.capitalize()} water systems in {place} have a current health-based violation"
        if n_rate:
            text += f" ({n_rate} across the U.S.)"
        out.append(f"{text}{mine}.")
    elif n_rate:
        out.append(f"Across the U.S., {n_rate} water systems have a current health-based violation{mine}.")

    # Neighbors whose lookup failed have no flag (None); leave them out instead of calling them clean.
    neighbors = [n for n in (comparison.get("neighbors") or [])
                 if isinstance(n, dict) and n.get("has_current_health_based") is not None]
    if neighbors:
        k = sum(1 for n in neighbors if n.get("has_current_health_based"))
        if k == 0:
            tail = "none has a current health-based violation"
        else:
            tail = f"{k} {'has' if k == 1 else 'have'} a current health-based violation"
        out.append(f"Of {len(neighbors)} nearby systems, {tail}.")
    return out[:2]


# What each California SAFER status means, in plain words (from the state's own definitions).
SAFER_TEXT = {
    "Failing": "is on the state's \"Failing\" list, which means it has not met one or more drinking water standards",
    "At-Risk": "is rated \"At-Risk\", which means it is more likely to have trouble meeting drinking water standards in the future",
    "Potentially At-Risk": "is rated \"Potentially At-Risk\", which falls between \"Not At-Risk\" and \"At-Risk\" in the state's rating",
    "Not At-Risk": "is rated \"Not At-Risk\", the state's lowest level of concern in its risk assessment",
}


def _state_sentences(report):
    if not report:
        return []
    status = report.get("status")
    out = []
    if status in SAFER_TEXT:
        first = f"In the state's risk assessment, this system {SAFER_TEXT[status]}."
        since = _month_year(report.get("failing_since")) if status == "Failing" else None
        if since:
            first = first[:-1] + f" (since {since})."
        out.append(first)
    elif status == "Not Assessed":
        out.append("This system was not included in the state's risk assessment.")
    failing = status == "Failing"
    if report.get("state") == "CA":
        # WHY: true and useful; state limits can be stricter than the federal ones above.
        out.append("California sets some drinking water limits that are stricter than the federal ones"
                   + (", so the state's list can differ from the federal records above; ask your utility about this."
                      if failing else "."))
    elif failing:
        out.append("The state list uses its own criteria, which can differ from the federal records above; "
                   "ask your utility about this.")
    return out[:2]


def _lead_pipe(details, system):
    pipes = (details or {}).get("lead_pipes") or {}
    housing = pipes.get("housing") or {}
    year = housing.get("median_year_built")
    try:
        year = int(year)
    except (TypeError, ValueError):
        year = None

    lead = system.get("lead_90th") or {}
    try:
        lead_high = float(lead.get("value_mg_l")) * 1000 > LEAD_ACTION_LEVEL_PPB
    except (TypeError, ValueError):
        lead_high = False
    lsl_violation = any(is_current(v) and str(v.get("contaminant_code")) == "5200"
                        and str(v.get("violation_code")).upper() == "2E"
                        for v in (system.get("violations") or []))

    # WHY 1960 and 1986: lead lines were common before 1960 and legal until 1986. A median of
    # 1960-1985 means many homes still predate the ban, so we say "possible", not "less likely".
    older = year is not None and year < 1960
    if older or lsl_violation or lead_high:
        level = "elevated"
    elif year is not None and year >= 1986:
        level = "typical"
    else:
        level = "unknown"

    sentences = []
    where = "ZIP code" if housing.get("geo") == "zip" else "neighborhood"
    if year is not None:
        sentences.append(f"The typical home in your {where} was built around {year}, "
                         "and EPA says lead pipes were banned in 1986.")
    else:
        sentences.append("We could not find housing-age data for this area; EPA says lead pipes were banned in 1986.")
    if level == "elevated":
        reasons = []
        if older:
            reasons.append("homes in your area are fairly old")
        if lsl_violation:
            reasons.append("the utility has not finished its lead pipe inventory")
        if lead_high:
            reasons.append("recent lead tests of home taps were above the federal action level")
        sentences.append("Lead pipes may be more likely here because " + " and ".join(reasons) + ".")
    elif level == "typical":
        sentences.append("Based on housing age alone, lead pipes look less likely than in older areas, "
                         "but one ZIP code can include older blocks, so you can ask your utility for its service line inventory.")
    elif year is not None:
        sentences.append("Many homes here were built before the 1986 ban, so lead pipes are possible; "
                         "ask your utility for its service line inventory.")
    # WHY always: this is a rough guess from housing age, never a test of the reader's own pipe.
    sentences.append("This is only an estimate from neighborhood housing age, not a test of your pipe; "
                     "EPA's quick check can help you look at yours.")
    return {"level": level, "sentences": sentences[:3]}


def translate_details(details, system):
    """Details + WaterSystem -> {"comparison_sentences", "state_sentences", "lead_pipe"}. Never raises."""
    details = details or {}
    system = system or {}
    return {
        "comparison_sentences": _safe(lambda d: _comparison_sentences(d.get("comparison"), system), details, []),
        "state_sentences": _safe(lambda d: _state_sentences(d.get("state_report")), details, []),
        "lead_pipe": _safe(lambda d: _lead_pipe(d, system), details,
                           {"level": "unknown", "sentences": [
                               "We could not estimate lead pipe risk for this area. "
                               "This would only be an estimate, not a test of your pipe."]}),
    }
