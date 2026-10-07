"""Build data/housing_age.csv: median year homes were built, per census tract and zip (ZCTA).

Source: Census ACS 5-year table B25035 (no API key). Run:
    .venv/Scripts/python scripts/build_housing_age.py
Homes built before 1986 may have lead pipes or lead solder; this is only a rough hint.
"""

import csv
import io
from pathlib import Path

import requests

URL = ("https://www2.census.gov/programs-surveys/acs/summary_file/2024/table-based-SF/"
       "data/5YRData/acsdt5y2024-b25035.dat")
OUT = Path(__file__).resolve().parent.parent / "data" / "housing_age.csv"


def main():
    text = requests.get(URL, timeout=120).text
    rows = []
    for row in csv.DictReader(io.StringIO(text), delimiter="|"):
        geo_id = row["GEO_ID"]
        if geo_id.startswith("1400000US") and len(geo_id) == 9 + 11:
            geo_type, geoid = "tract", geo_id[9:]
        elif geo_id.startswith("860Z200US") and len(geo_id) == 9 + 5:
            geo_type, geoid = "zip", geo_id[9:]
        else:
            continue
        try:
            year = int(row["B25035_E001"])
        except ValueError:
            continue  # blank = Census had no estimate
        # Census uses large negative numbers (e.g. -666666666) as "no data" markers.
        if not 1800 <= year <= 2030:
            continue
        rows.append((geo_type, geoid, year))
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["geo_type", "geoid", "median_year_built"])
        w.writerows(rows)
    print(len(rows), "rows ->", OUT)


if __name__ == "__main__":
    main()
