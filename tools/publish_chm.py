"""Publica uma janela CHM nova todos os dias, junto com o catalogo NOAA."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from chm_global import build_chm_forecast
from global_data import WINDOW_DAYS, forecast_hash, weekly_chunks


def write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--date")
    args = parser.parse_args()
    now = datetime.now(UTC) if args.date is None else datetime.fromisoformat(args.date).replace(tzinfo=UTC)
    root = args.root.resolve(); release_id = now.date().isoformat(); release = root / "releases" / release_id
    current = json.loads((root / "current.json").read_text(encoding="utf-8")) if (root / "current.json").exists() else {}
    previous = root / "releases" / current.get("release", "")
    if previous.exists() and previous != release:
        for relative in (Path("catalog") / "noaa", Path("forecast") / "noaa"):
            if (previous / relative).exists(): shutil.copytree(previous / relative, release / relative, dirs_exist_ok=True)
    summaries: list[dict[str, Any]] = []
    for path in sorted((root / "sources" / "chm" / "2026").glob("estacao_*.json")):
        station, forecast = build_chm_forecast(path, now)
        forecast["sha256"] = forecast_hash(forecast)
        code = station["id"].split(":", 1)[1]; folder = release / "forecast" / "chm" / code
        write(folder / "next-30.json", forecast)
        weeks = []
        for chunk in weekly_chunks(forecast):
            weeks.append(chunk["week_start"]); write(folder / f"{chunk['week_start']}.json", chunk)
        write(folder / "index.json", {"v": 1, "station": station["id"], "generated_at": int(now.timestamp()), "weeks": weeks, "rolling": "next-30.json", "window_days": WINDOW_DAYS})
        write(release / "catalog" / "chm" / f"{code}.json", station)
        summaries.append({key: station[key] for key in ("id", "name", "lat_e5", "lon_e5", "source", "datum", "unit", "timezone", "prediction_class", "attribution")})
    previous_catalog = json.loads((previous / "catalog" / "index.json").read_text(encoding="utf-8")) if (previous / "catalog" / "index.json").exists() else {"stations": []}
    noaa = [entry for entry in previous_catalog.get("stations", []) if entry.get("source") == "NOAA"]
    write(release / "catalog" / "index.json", {"v": 1, "generated_at": int(now.timestamp()), "stations": summaries + noaa})
    write(release / "release.json", {"v": 1, "release": release_id, "generated_at": int(now.timestamp()), "sources": ["CHM"] + (["NOAA"] if noaa else []), "forecast_window_days": WINDOW_DAYS, "min_client_version": 1})
    write(root / "current.json", {"v": 1, "release": release_id, "generated_at": int(now.timestamp()), "min_client_version": 1})
    print(f"{len(summaries)} estacoes CHM atualizadas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
