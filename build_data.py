#!/usr/bin/env python3
"""
Country Dairy Sales Dashboard — data builder.

Reads the quarterly sales export (country_dairy_sales.csv), geocodes each
customer by ZIP-code centroid (offline, via pgeocode), and writes
data/customers.json + data/summary.json for the static dashboard.

USAGE (run once per quarter after dropping in the new CSV):
    python3 build_data.py
    # optionally: python3 build_data.py --csv some_other_export.csv

Requires: pip install pgeocode
pgeocode downloads a US ZIP centroid table once and caches it locally, so
reruns are offline and reproducible.
"""

import argparse
import csv
import json
import math
import os
import sys
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="country_dairy_sales.csv")
    ap.add_argument("--out", default="data")
    ap.add_argument("--as-of", dest="as_of", default=None,
                    help="Sales-period date shown in the header, e.g. 2026-03-31. "
                         "Defaults to the CSV's last-modified date.")
    args = ap.parse_args()

    with open(args.csv, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    print(f"Read {len(rows)} customer rows from {args.csv}")

    nomi = pgeocode.Nominatim("us")

    # Geocode unique ZIPs in one pass.
    zips = {clean_zip(r.get(COL_ZIP)) for r in rows}
    zips.discard(None)
    zip_ll = {}
    for z in zips:
        rec = nomi.query_postal_code(z)
        lat, lng = rec.latitude, rec.longitude
        if lat == lat and lng == lng:    # not NaN
            zip_ll[z] = (float(lat), float(lng))
    print(f"Geocoded {len(zip_ll)}/{len(zips)} unique ZIPs")

    seen_at_zip = defaultdict(int)
    customers = []
    ungeocoded = []
    product_totals = defaultdict(int)
    state_totals = defaultdict(lambda: {"customers": 0, "cases": 0})

    for r in rows:
        name = (r.get(COL_NAME) or "").strip()
        cases = to_int(r.get(COL_TOTAL))
        state = (r.get(COL_STATE) or "").strip()
        z = clean_zip(r.get(COL_ZIP))

        for p in PRODUCT_COLS:
            product_totals[p] += to_int(r.get(p))
        if state:
            state_totals[state]["customers"] += 1
            state_totals[state]["cases"] += cases

        if z not in zip_ll:
            ungeocoded.append(name)
            continue

        base_lat, base_lng = zip_ll[z]
        idx = seen_at_zip[z]
        seen_at_zip[z] += 1
        lat, lng = jitter(base_lat, base_lng, z, idx)

        # Per-product case counts, aligned to PRODUCT_COLS order, so the map can
        # filter the heat/dots to a single product.
        per_product = [to_int(r.get(p)) for p in PRODUCT_COLS]

        customers.append({
            "id": (r.get(COL_CUST) or "").strip(),
            "name": name,
            "city": (r.get(COL_CITY) or "").strip(),
            "state": state,
            "zip": z,
            "lat": round(lat, 5),
            "lng": round(lng, 5),
            "cases": cases,
            "p": per_product,
        })

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
        "total_customers": len(rows),
        "mapped_customers": len(customers),
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
