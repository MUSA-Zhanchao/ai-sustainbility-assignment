"""
Randomly download N Google Street View images within a bounding box.

Workflow:
  1. Sample a random (lat, lon) inside the bbox
  2. Query the Street View Metadata API (free) to find the nearest panorama
  3. If a new outdoor panorama exists, download the image (billed per request)
  4. Repeat until N unique panoramas are saved

Usage:
  export GOOGLE_MAPS_API_KEY="your_key"
  python random_streetview.py
"""

import os
import csv
import time
import random
import requests
import osmnx as ox
from shapely.geometry import Point
from dotenv import load_dotenv


# ---------------- Config ----------------
PLACE = "Philadelphia, Pennsylvania, USA"   # sampling area = official city boundary

load_dotenv()
API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY")
if not API_KEY:
    raise RuntimeError("没找到 GOOGLE_MAPS_API_KEY，检查 .env 文件")

N_IMAGES = 150
# Bounding box: (min_lat, min_lon, max_lat, max_lon) -- default: Philadelphia
BBOX = (39.867, -75.280, 40.138, -74.955)

SEARCH_RADIUS = 50          # meters; how far from the random point to look for a pano
IMG_SIZE = "640x640"        # max 640x640 for standard keys
FOV = 90                    # field of view
PITCH = 0
RANDOM_HEADING = True       # random camera direction; False -> fixed heading 0
MAX_ATTEMPTS = 5000         # safety cap on metadata calls
SEED = 42

OUT_DIR = "streetview_images"
META_CSV = os.path.join(OUT_DIR, "metadata.csv")

META_URL = "https://maps.googleapis.com/maps/api/streetview/metadata"
IMG_URL = "https://maps.googleapis.com/maps/api/streetview"
# ----------------------------------------


def load_boundary(place):
    """Get the city boundary polygon (WGS84) from OpenStreetMap."""
    gdf = ox.geocode_to_gdf(place)
    return gdf.geometry.iloc[0]


def random_point(poly):
    """Rejection sampling: uniform random point inside the polygon."""
    min_lon, min_lat, max_lon, max_lat = poly.bounds
    while True:
        lon = random.uniform(min_lon, max_lon)
        lat = random.uniform(min_lat, max_lat)
        if poly.contains(Point(lon, lat)):
            return lat, lon


def get_metadata(lat, lon, session):
    params = {
        "location": f"{lat},{lon}",
        "radius": SEARCH_RADIUS,
        "source": "outdoor",     # skip indoor / business interiors
        "key": API_KEY,
    }
    r = session.get(META_URL, params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def download_image(pano_id, heading, path, session):
    params = {
        "pano": pano_id,
        "size": IMG_SIZE,
        "fov": FOV,
        "pitch": PITCH,
        "heading": heading,
        "key": API_KEY,
    }
    r = session.get(IMG_URL, params=params, timeout=30)
    r.raise_for_status()
    with open(path, "wb") as f:
        f.write(r.content)


def main():
    random.seed(SEED)
    os.makedirs(OUT_DIR, exist_ok=True)

    seen = set()
    rows = []
    attempts = 0
    session = requests.Session()
    city = load_boundary(PLACE)

    while len(rows) < N_IMAGES and attempts < MAX_ATTEMPTS:
        attempts += 1
        lat, lon = random_point(city)

        try:
            meta = get_metadata(lat, lon, session)
        except requests.RequestException as e:
            print(f"[metadata error] {e}")
            time.sleep(1)
            continue

        status = meta.get("status")
        if status == "REQUEST_DENIED":
            raise RuntimeError(f"API key problem: {meta.get('error_message')}")
        if status != "OK":
            continue  # ZERO_RESULTS etc.

        pano_id = meta["pano_id"]
        if pano_id in seen:
            continue
        # Keep only Google's own imagery (drop user-uploaded photospheres)
        if "Google" not in meta.get("copyright", ""):
            continue
        # The pano may snap up to SEARCH_RADIUS away -- make sure it's still in the city
        if not city.contains(Point(meta["location"]["lng"], meta["location"]["lat"])):
            continue
        seen.add(pano_id)

        heading = random.randint(0, 359) if RANDOM_HEADING else 0
        idx = len(rows) + 1
        fname = f"sv_{idx:04d}_{pano_id}.jpg"

        try:
            download_image(pano_id, heading, os.path.join(OUT_DIR, fname), session)
        except requests.RequestException as e:
            print(f"[image error] {e}")
            seen.discard(pano_id)
            continue

        loc = meta["location"]
        rows.append({
            "id": idx,
            "file": fname,
            "pano_id": pano_id,
            "sample_lat": round(lat, 6),
            "sample_lon": round(lon, 6),
            "pano_lat": loc["lat"],
            "pano_lon": loc["lng"],
            "date": meta.get("date", ""),
            "heading": heading,
        })
        print(f"[{idx}/{N_IMAGES}] {fname}  date={meta.get('date', '')}")
        time.sleep(0.05)

    with open(META_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["id"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nDone: {len(rows)} images, {attempts} metadata calls. Saved to {OUT_DIR}/")


if __name__ == "__main__":
    main()
