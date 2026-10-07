"""Build data/zip_to_city.csv from the GeoNames US postal-code file.

Source: https://download.geonames.org/export/zip/US.zip  (CC BY 4.0, credit GeoNames)
Run once:  .venv/Scripts/python scripts/build_zip_table.py
"""

import csv
import io
import os
import zipfile

import requests

URL = "https://download.geonames.org/export/zip/US.zip"
OUT = os.path.join(os.path.dirname(__file__), "..", "data", "zip_to_city.csv")


def main():
    response = requests.get(URL, timeout=60)
    response.raise_for_status()
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    # US.txt is tab-separated: country, zip, city, state name, state code, county name,
    # county code, admin3 name, admin3 code, latitude, longitude, accuracy
    text = archive.read("US.txt").decode("utf-8")

    rows = {}
    for line in text.splitlines():
        fields = line.split("\t")
        if len(fields) < 5:
            continue
        zip_code, city, state = fields[1], fields[2], fields[4]
        if len(zip_code) == 5 and zip_code.isdigit() and state:
            # lat/lon are the zip's center point; the data layer uses them to ask which
            # water-system service area contains the middle of the zip.
            lat, lon = (fields[9], fields[10]) if len(fields) >= 11 else ("", "")
            rows[zip_code] = (city.upper(), state, lat, lon)  # EPA stores city names in upper case

    with open(OUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        # Keep zip,city,state first so older readers that only use those columns still work.
        writer.writerow(["zip", "city", "state", "lat", "lon"])
        for zip_code in sorted(rows):
            writer.writerow([zip_code, *rows[zip_code]])
    print(f"Wrote {len(rows)} zips to {OUT}")


if __name__ == "__main__":
    main()
