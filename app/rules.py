"""Shared rules that more than one layer must agree on.

Keep this file tiny: anything here is used by both the data layer (to decide
which violations to keep) and the translation layer (to decide the badge).
"""

from datetime import date

# EPA "Known" (K) violations were never returned to compliance. Recent ones are
# still a live problem; very old ones are usually stale paperwork, so we stop
# counting them after this many years.
KNOWN_STILL_CURRENT_YEARS = 5


def counts_as_health_based(violation):
    """True if a contract Violation dict should be treated as a health-based problem.

    EPA flags missing lead service line inventories (contaminant 5200, Lead and
    Copper Rule Revisions) as health-based treatment-technique violations, but
    they are record-keeping: the utility hasn't finished listing which pipes
    might be lead. Showing a red "health-based" badge for that would overstate
    risk, so every layer (badge, trend, guidance, comparisons) uses this rule.
    """
    if str(violation.get("contaminant_code")) == "5200":
        return False
    return bool(violation.get("is_health_based"))


def is_current(violation, today=None):
    """True if a contract Violation dict should count as a current problem."""
    status = violation.get("status")
    if status == "open":
        return True
    if status != "known":
        return False
    begin = violation.get("begin_date")
    if not begin:
        return False
    today = today or date.today()
    cutoff = date(today.year - KNOWN_STILL_CURRENT_YEARS, today.month, min(today.day, 28))
    return begin >= cutoff.isoformat()
