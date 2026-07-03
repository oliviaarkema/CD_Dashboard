#!/usr/bin/env python3
"""
Country Dairy Sales Dashboard — data builder.

Reads the quarterly sales export (country_dairy_sales.csv), geocodes each
customer to its STREET ADDRESS via the free US Census batch geocoder (no API
key), falling back to ZIP-code centroid (pgeocode) for any address the Census
can't match, and writes data/customers.json + data/summary.json.

USAGE (run once per quarter after dropping in the new CSV):
    python3 build_data.py --as-of 2026-03-31
    # options:
    #   --csv some_other_export.csv
    #   --zip-only   skip street geocoding (offline; ZIP centroids only)

Requires: pip install pgeocode  (used only for the ZIP fallback)
Street geocoding needs internet; the ZIP fallback is offline and cached.
"""

import argparse
import csv
import io
import json
import math
import os
import sys
import urllib.request
import uuid
from collections import defaultdict
from datetime import date, datetime

try:
    import pgeocode
except ImportError:
    sys.exit("Missing dependency. Run:  python3 -m pip install pgeocode")

# --- Columns in the export -------------------------------------------------
COL_CUST = "CUST #"
COL_NAME = "DESCRIPTION"
COL_ADDR = "ADDR1"
COL_CITY = "CITY"
COL_STATE = "ST"
COL_ZIP = "ZIP CODE"
COL_TOTAL = "TOTAL CASES"

# Product columns (everything sold), used for the product ranking.
PRODUCT_COLS = [
    "Milk-CD Half Pints", "Milk-5Gal", "Milk Gals-Country Dairy",
    "Milk Gals-Cedar Cr", "Milk Hgls-Country Dairy", "Milk Pints-Country Dairy",
    "Milk Qts-Country Dairy", "Mix-Country Dairy", "WF Gallons", "WF Hgls",
]

# Gallons of product per case, by product. Used to convert case counts into the
# total-volume "Gallons sold" stat. Derived from the packout of each format
# (e.g. 4 gallons/case, 9 half-gallons/case = 4.5 gal, 20 pints/case = 2.5 gal).
GALLONS_PER_CASE = {
    "Milk-CD Half Pints": 3.125,
    "Milk-5Gal": 5.0,
    "Milk Gals-Country Dairy": 4.0,
    "Milk Gals-Cedar Cr": 4.0,
    "Milk Hgls-Country Dairy": 4.5,
    "Milk Pints-Country Dairy": 2.5,
    "Milk Qts-Country Dairy": 2.25,
    "Mix-Country Dairy": 5.0,
    "WF Gallons": 4.0,
    "WF Hgls": 4.5,
}


def clean_zip(raw):
    """Normalize a ZIP to its 5-digit form; return None if unusable."""
    if not raw:
        return None
    z = raw.strip().split("-")[0].strip()
    z = "".join(ch for ch in z if ch.isdigit())
    if len(z) == 4:          # lost a leading zero somewhere upstream
        z = "0" + z
    return z if len(z) == 5 else None


def to_int(raw):
    try:
        return int(float(str(raw).replace(",", "").strip() or 0))
    except ValueError:
        return 0


def jitter(lat, lng, key, index):
    """
    Spread customers that share a ZIP centroid into a small ring so circle
    markers don't stack perfectly on top of each other. Deterministic, so the
    map looks the same on every rebuild. ~0.4 km radius steps.
    """
    if index == 0:
        return lat, lng
    ring = 1 + (index - 1) // 8          # which concentric ring
    slot = (index - 1) % 8               # position on the ring
    ang = (slot / 8.0) * 2 * math.pi + (hash(key) % 360) * math.pi / 180.0
    r = 0.0035 * ring                    # degrees (~0.4 km per ring)
    return lat + r * math.cos(ang), lng + r * math.sin(ang) / math.cos(math.radians(lat))


CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"


def census_geocode(addr_rows, chunk=1000):
    """
    Street-level geocode via the US Census batch geocoder (free, no key).
    `addr_rows` is a list of (rid, street, city, state, zip). Returns
    {rid: (lat, lng)} for rows the Census matched. Batches of <=10k; we use
    1k chunks so a single bad chunk can't lose everything.
    """
    matched = {}
    for start in range(0, len(addr_rows), chunk):
        batch = addr_rows[start:start + chunk]
        buf = io.StringIO()
        w = csv.writer(buf)
        for rid, street, city, st, zc in batch:
            w.writerow([rid, street, city, st, zc])
        payload = buf.getvalue().encode("utf-8")

        boundary = uuid.uuid4().hex
        body = b"".join([
            (f"--{boundary}\r\n"
             f"Content-Disposition: form-data; name=\"benchmark\"\r\n\r\n"
             f"Public_AR_Current\r\n").encode(),
            (f"--{boundary}\r\n"
             f"Content-Disposition: form-data; name=\"addressFile\"; filename=\"addr.csv\"\r\n"
             f"Content-Type: text/csv\r\n\r\n").encode(),
            payload, b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ])
        req = urllib.request.Request(
            CENSUS_URL, data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        with urllib.request.urlopen(req, timeout=300) as resp:
            text = resp.read().decode("utf-8", "replace")

        for row in csv.reader(io.StringIO(text)):
            # rid, input, "Match"/"No_Match"/"Tie", matchtype, matched addr,
            # "lon,lat", tigerline id, side
            if len(row) >= 6 and row[2] == "Match" and row[5]:
                lon, lat = row[5].split(",")
                matched[row[0]] = (float(lat), float(lon))
        print(f"  Census batch {start//chunk + 1}: "
              f"{len(batch)} sent, {sum(1 for r in batch if r[0] in matched)} matched so far")
    return matched


# Population by ZIP (ZCTA), from Ro-Data's Census summary tables. We download
# the ~26MB table once, cache a slim {zip: population} JSON, and commit that so
# future rebuilds are offline. Used for the population-normalized map layer.
POP_SOURCE = ("https://raw.githubusercontent.com/Ro-Data/"
              "Ro-Census-Summaries-By-Zipcode/master/demo.txt")
POP_CACHE = "zcta_pop.json"


def load_zip_population(cache_path=POP_CACHE, refresh=False):
    if os.path.exists(cache_path) and not refresh:
        with open(cache_path) as f:
            return {k: int(v) for k, v in json.load(f).items()}
    print("Downloading ZIP population table (one-time, ~26MB)…")
    with urllib.request.urlopen(POP_SOURCE, timeout=300) as resp:
        text = resp.read().decode("utf-8", "replace")
    pop = {}
    rd = csv.reader(io.StringIO(text), delimiter="\t")
    next(rd, None)                       # header: ZCTA5, total_population, …
    for row in rd:
        if len(row) < 2:
            continue
        try:
            pop[row[0].strip()] = int(float(row[1]))
        except ValueError:
            continue
    with open(cache_path, "w") as f:
        json.dump(pop, f, separators=(",", ":"))
    print(f"  Cached {len(pop)} ZIP populations to {cache_path}")
    return pop


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="country_dairy_sales.csv")
    ap.add_argument("--out", default="data")
    ap.add_argument("--as-of", dest="as_of", default=None,
                    help="Sales-period date shown in the header, e.g. 2026-03-31. "
                         "Defaults to the CSV's last-modified date.")
    ap.add_argument("--zip-only", action="store_true",
                    help="Skip street geocoding; use ZIP centroids only (offline).")
    ap.add_argument("--refresh-pop", action="store_true",
                    help="Re-download the ZIP population table (else uses the cache).")
    args = ap.parse_args()

    with open(args.csv, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    print(f"Read {len(rows)} customer rows from {args.csv}")

    nomi = pgeocode.Nominatim("us")

    # 1) Street-level geocode via the Census (keyed by row index).
    street_ll = {}
    if not args.zip_only:
        addr_rows = [
            (str(i),
             (r.get(COL_ADDR) or "").strip(),
             (r.get(COL_CITY) or "").strip(),
             (r.get(COL_STATE) or "").strip(),
             clean_zip(r.get(COL_ZIP)) or "")
            for i, r in enumerate(rows)
            if (r.get(COL_ADDR) or "").strip()
        ]
        print(f"Street-geocoding {len(addr_rows)} addresses via US Census…")
        try:
            street_ll = census_geocode(addr_rows)
        except Exception as e:               # network/service issue — fall back
            print(f"  Census geocoding unavailable ({e}); using ZIP centroids.")
        print(f"Street-matched {len(street_ll)}/{len(rows)} addresses")

    # 2) ZIP centroids (for the fallback + the population-normalized layer).
    zips = {clean_zip(r.get(COL_ZIP)) for r in rows}
    zips.discard(None)
    zip_ll = {}
    for z in zips:
        rec = nomi.query_postal_code(z)
        lat, lng = rec.latitude, rec.longitude
        if lat == lat and lng == lng:        # not NaN
            zip_ll[z] = (float(lat), float(lng))

    # 3) Population per ZIP, for the normalized (per-capita) map layer.
    zip_pop = load_zip_population(refresh=args.refresh_pop)
    zip_geo = {}                             # zip -> {pop, lat, lng} for present ZIPs
    for z in zips:
        if z in zip_ll and zip_pop.get(z, 0) > 0:
            lat, lng = zip_ll[z]
            zip_geo[z] = {"pop": zip_pop[z], "lat": round(lat, 5), "lng": round(lng, 5)}
    print(f"Population matched for {len(zip_geo)}/{len(zips)} ZIPs")

    seen_at_zip = defaultdict(int)
    customers = []
    ungeocoded = []
    n_street = n_zip = 0
    product_totals = defaultdict(int)
    state_totals = defaultdict(lambda: {"customers": 0, "cases": 0})

    for i, r in enumerate(rows):
        name = (r.get(COL_NAME) or "").strip()
        cases = to_int(r.get(COL_TOTAL))
        state = (r.get(COL_STATE) or "").strip()
        z = clean_zip(r.get(COL_ZIP))

        for p in PRODUCT_COLS:
            product_totals[p] += to_int(r.get(p))
        if state:
            state_totals[state]["customers"] += 1
            state_totals[state]["cases"] += cases

        # Prefer the exact street match; otherwise fall back to the ZIP
        # centroid (jittered so same-ZIP customers don't stack).
        if str(i) in street_ll:
            lat, lng = street_ll[str(i)]
            geo = "street"
            n_street += 1
        elif z in zip_ll:
            base_lat, base_lng = zip_ll[z]
            idx = seen_at_zip[z]
            seen_at_zip[z] += 1
            lat, lng = jitter(base_lat, base_lng, z, idx)
            geo = "zip"
            n_zip += 1
        else:
            ungeocoded.append(name)
            continue

        # Per-product case counts, aligned to PRODUCT_COLS order, so the map can
        # filter the heat/dots to a single product.
        per_product = [to_int(r.get(p)) for p in PRODUCT_COLS]

        customers.append({
            "id": (r.get(COL_CUST) or "").strip(),
            "name": name,
            "addr": (r.get(COL_ADDR) or "").strip(),
            "city": (r.get(COL_CITY) or "").strip(),
            "state": state,
            "zip": z,
            "lat": round(lat, 5),
            "lng": round(lng, 5),
            "cases": cases,
            "p": per_product,
            "geo": geo,
        })

    print(f"Placed {n_street} at street level, {n_zip} at ZIP centroid, "
          f"{len(ungeocoded)} unmapped")
    customers.sort(key=lambda c: c["cases"], reverse=True)

    # Header "Sales Data from ..." date. Prefer an explicit --as-of (the sales
    # period end, e.g. quarter close); otherwise fall back to the CSV's
    # last-modified date.
    csv_updated = datetime.fromtimestamp(os.path.getmtime(args.csv)).date().isoformat()
    as_of = args.as_of or csv_updated

    summary = {
        "generated": date.today().isoformat(),
        "as_of": as_of,
        "data_updated": csv_updated,
        "source_csv": args.csv,
        # PRODUCT_COLS order — the map's per-customer "p" arrays align to this.
        "product_order": PRODUCT_COLS,
        # ZIP -> {pop, lat, lng} for the population-normalized layer. The client
        # aggregates active customers by ZIP and divides cases by pop.
        "zips": zip_geo,
        "total_customers": len(rows),
        "mapped_customers": len(customers),
        "street_matched": n_street,
        "zip_fallback": n_zip,
        "unmapped_customers": len(ungeocoded),
        "total_cases": sum(to_int(r.get(COL_TOTAL)) for r in rows),
        "gallons_sold": round(sum(product_totals[p] * GALLONS_PER_CASE.get(p, 0)
                                  for p in PRODUCT_COLS)),
        "top_customers": [
            {"name": c["name"], "city": c["city"], "state": c["state"], "cases": c["cases"]}
            for c in customers[:10]
        ],
        "products_by_cases": sorted(
            [{"product": p, "cases": u} for p, u in product_totals.items()],
            key=lambda d: d["cases"], reverse=True,
        ),
        "states": sorted(
            [{"state": s, **v} for s, v in state_totals.items()],
            key=lambda d: d["cases"], reverse=True,
        ),
    }

    with open(f"{args.out}/customers.json", "w") as f:
        json.dump(customers, f, separators=(",", ":"))
    with open(f"{args.out}/summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    print(f"Wrote {args.out}/customers.json ({len(customers)} mapped points)")
    print(f"Wrote {args.out}/summary.json")
    if ungeocoded:
        print(f"NOTE: {len(ungeocoded)} customers had no usable ZIP and were "
              f"left off the map (still counted in totals): "
              f"{', '.join(ungeocoded[:8])}{' ...' if len(ungeocoded) > 8 else ''}")


if __name__ == "__main__":
    main()
