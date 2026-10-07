"""Small text helpers shared by the web layer and the guidance text."""
import re

# EPA names are ALL CAPS. These acronyms must stay upper-case when we tidy them.
ACRONYMS = {"USA", "PUD", "MUD", "MHP", "WSC", "LADWP", "PWS", "WD", "CWS", "SD", "WA",
            "NE", "NW", "SE", "SW", "II", "III", "IV", "HOA", "RV", "LLC", "INC", "DWSD", "MWRA"}
# Short words that look like acronyms (no vowels) but are really abbreviations.
NOT_ACRONYMS = {"ST", "MT", "FT", "DR", "MR", "CT", "TWP", "CO", "HWY", "RD", "NY"}
# Small words stay lower-case unless they start the name.
SMALL_WORDS = {"of", "the", "and", "for", "in", "at", "on", "to"}


def tidy_name(raw):
    """'FLINT, CITY OF' -> 'Flint, City of'. Leaves lower/mixed-case text alone."""
    if not raw:
        return ""
    if raw != raw.upper():  # already mixed case: someone formatted it on purpose
        return raw.strip()
    words = []
    for i, word in enumerate(raw.split()):
        bare = re.sub(r"[^A-Z]", "", word)
        if word.startswith("(") or word.endswith(")"):
            # Parenthesised text like "(MWRA)" is usually an acronym; "(SECOND WARD)" is not.
            if bare in ACRONYMS or (2 <= len(bare) <= 5 and bare not in NOT_ACRONYMS
                                    and not re.search(r"[AEIOUY]", bare)):
                words.append(word)
                continue
            words.append(word[0] + word[1:].capitalize() if word.startswith("(") else word.capitalize())
            continue
        # No vowels + short = almost certainly an acronym (LADWP, MHP, WSC).
        looks_like_acronym = (2 <= len(bare) <= 5 and not re.search(r"[AEIOUY]", bare)
                              and bare not in NOT_ACRONYMS)
        if bare in ACRONYMS or looks_like_acronym:
            words.append(word)
        elif bare.lower() in SMALL_WORDS and i > 0:
            words.append(word.lower())
        else:
            # Capitalize after hyphens/slashes too, but not after apostrophes ("Mary's").
            words.append("-".join(
                "/".join(p.capitalize() for p in h.split("/")) for h in word.split("-")))
    return " ".join(words)


# "FLINT, CITY OF" is how EPA files city-run systems; people say "City of Flint".
_KIND_SUFFIX = re.compile(r"^(?P<head>.+),\s*(?P<kind>(?:city|town|village|county|borough|township) of)$",
                          re.IGNORECASE)


def display_name(raw):
    """tidy_name, then 'Flint, City of' -> 'City of Flint'."""
    name = tidy_name(raw)
    match = _KIND_SUFFIX.match(name)
    if match:
        kind = match.group("kind")
        return f"{kind[0].upper()}{kind[1:]} {match.group('head')}"
    return name


def tidy_address(raw):
    """'20 WEST LAKE DRIVE, VALHALLA, NY 10595' -> '20 West Lake Drive, Valhalla, NY 10595'."""
    # Tidy each comma-separated part on its own: EPA addresses are sometimes partly mixed
    # case ("3900 Donaldson Pl., NW, WASHINGTON, DC"), and tidy_name skips mixed-case text.
    text = ", ".join(tidy_name(part.strip()) for part in (raw or "").split(","))
    # tidy_name lower-cases two-letter words like "NY", so put the state code back.
    return re.sub(r",\s*([A-Za-z]{2})(\s+\d{5}(?:-\d{4})?)?$",
                  lambda m: ", " + m.group(1).upper() + (m.group(2) or ""), text)
