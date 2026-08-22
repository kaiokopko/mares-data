"""Gera uma release NOAA imutavel e troca current.json por ultimo."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from global_data import WINDOW_DAYS, load_station, write_weekly_release


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--station", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--date", help="YYYY-MM-DD UTC; padrao: agora")
    args = parser.parse_args()

    now = datetime.now(UTC) if args.date is None else datetime.fromisoformat(args.date).replace(tzinfo=UTC)
    release_id = now.date().isoformat()
    root = args.root.resolve()
    release = root / "releases" / release_id
    station = load_station(args.station)

    forecast_files = write_weekly_release(station, release, now)
    station_id = station["id"].split(":", 1)[1]
    write_json(
        release / "forecast" / station["source"].lower() / station_id / "index.json",
        {"v": 1, "station": station["id"], "generated_at": int(now.timestamp()), "weeks": [path.stem for path in forecast_files]},
    )
    catalog_station = release / "catalog" / station["source"].lower() / f"{station['id'].split(':', 1)[1]}.json"
    catalog_station.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(args.station, catalog_station)

    station_summary = {
        key: station[key]
        for key in ("id", "name", "lat_e5", "lon_e5", "source", "datum", "unit", "timezone", "prediction_class", "attribution")
    }
    write_json(release / "catalog" / "index.json", {"v": 1, "generated_at": int(now.timestamp()), "stations": [station_summary]})
    write_json(
        release / "release.json",
        {"v": 1, "release": release_id, "generated_at": int(now.timestamp()), "sources": [station["source"]], "forecast_window_days": WINDOW_DAYS, "min_client_version": 1},
    )
    write_json(root / "current.json", {"v": 1, "release": release_id, "generated_at": int(now.timestamp()), "min_client_version": 1})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
