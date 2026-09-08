"""Gera uma release NOAA imutavel e troca current.json por ultimo."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from global_data import WINDOW_DAYS, fetch_official_forecast, load_station, write_rolling_forecast, write_weekly_release


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--station", type=Path, action="append", required=True,
                        help="estacao aprovada; pode ser informado mais de uma vez")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--date", help="YYYY-MM-DD UTC; padrao: agora")
    args = parser.parse_args()

    now = datetime.now(UTC) if args.date is None else datetime.fromisoformat(args.date).replace(tzinfo=UTC)
    release_id = now.date().isoformat()
    root = args.root.resolve()
    release = root / "releases" / release_id
    current_path = root / "current.json"
    previous_release = None
    if current_path.exists():
        previous_release = json.loads(current_path.read_text(encoding="utf-8")).get("release")
    # A previsão NOAA muda diariamente, mas a parte CHM autorizada é anual.
    # Carregamos a última cópia imutável para que uma atualização NOAA nunca
    # apague o catálogo brasileiro do ponteiro current.json.
    if previous_release and previous_release != release_id:
        previous = root / "releases" / previous_release
        for relative in (Path("catalog") / "chm", Path("forecast") / "chm"):
            source = previous / relative
            if source.exists():
                shutil.copytree(source, release / relative, dirs_exist_ok=True)
    stations_to_publish = [load_station(path) for path in args.station]
    station_summaries = []
    for station, station_path in zip(stations_to_publish, args.station, strict=True):
        forecast = fetch_official_forecast(station, now)
        forecast_files = write_weekly_release(station, release, now, forecast)
        rolling_file = write_rolling_forecast(station, release, now, forecast)
        station_id = station["id"].split(":", 1)[1]
        write_json(
            release / "forecast" / station["source"].lower() / station_id / "index.json",
            {
                "v": 1,
                "station": station["id"],
                "generated_at": int(now.timestamp()),
                "weeks": [path.stem for path in forecast_files],
                "rolling": rolling_file.name,
                "window_days": WINDOW_DAYS,
            },
        )
        catalog_station = release / "catalog" / station["source"].lower() / f"{station_id}.json"
        catalog_station.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(station_path, catalog_station)
        station_summaries.append({
            key: station[key]
            for key in ("id", "name", "lat_e5", "lon_e5", "source", "datum", "unit", "timezone", "prediction_class", "attribution", "country", "region", "radius_km", "license_url")
            if key in station
        })
    catalog_path = release / "catalog" / "index.json"
    carried = json.loads((root / "releases" / previous_release / "catalog" / "index.json").read_text(encoding="utf-8")) if previous_release and (root / "releases" / previous_release / "catalog" / "index.json").exists() else {"stations": []}
    stations = [entry for entry in carried.get("stations", []) if entry.get("source") == "CHM"] + station_summaries
    write_json(catalog_path, {"v": 1, "generated_at": int(now.timestamp()), "stations": stations})
    write_json(
        release / "release.json",
        {"v": 1, "release": release_id, "generated_at": int(now.timestamp()), "sources": sorted(set([station["source"] for station in stations_to_publish] + (["CHM"] if any(entry.get("source") == "CHM" for entry in stations) else []))), "forecast_window_days": WINDOW_DAYS, "min_client_version": 1},
    )
    write_json(root / "current.json", {"v": 1, "release": release_id, "generated_at": int(now.timestamp()), "min_client_version": 1})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
