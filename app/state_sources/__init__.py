"""State-run water data, added through small "adapters".

An adapter is any object with:
    STATE = "CA"
    def fetch(self, pwsid) -> state_report dict (see docs/DATA_CONTRACT.md) or None

HOW TO ADD ANOTHER STATE (example: Texas)
  1. Copy app/state_sources/california.py to texas.py.
  2. Change STATE to "TX" and rewrite fetch() so it asks Texas's open-data
     site about one PWSID and returns the state_report dict (or None when the
     state has no row for that system, or the request fails).
  3. Add two lines below:  `from . import texas`  and  `ADAPTERS["TX"] = texas.ADAPTER`
  4. Add a test in tests/test_details.py with a fake response.
Nothing else changes: app/details.py looks the state up here.
"""

from . import california

# state code -> adapter
ADAPTERS = {
    "CA": california.ADAPTER,
}


def get_adapter(state_code):
    """The adapter for a 2-letter state code, or None if we have none."""
    return ADAPTERS.get((state_code or "").upper())


def fetch_state_report(state_code, pwsid):
    """Ask the state adapter about one system. Returns a state_report dict or None. Never raises."""
    adapter = get_adapter(state_code)
    if adapter is None:
        return None
    try:
        return adapter.fetch(pwsid)
    except Exception:  # a state website being down must never break the page
        return None
