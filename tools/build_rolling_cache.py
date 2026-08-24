"""Cria ``next-30.json`` a partir dos blocos versionados de uma release.

Serve tanto para o acervo CHM (que ja chega em blocos autorizados) quanto
para releases NOAA antigas. Nao consulta fonte externa e nao altera extremos.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from global_data import WINDOW_DAYS, forecast_hash


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build(release: Path, now: datetime) -> list[Path]:
    written: list[Path] = []
    start = int(now.timestamp())
    end = int((now + timedelta(days=WINDOW_DAYS)).timestamp())
    for index_path in sorted(release.glob("forecast/*/*/index.json")):
        index = _read(index_path)
        weeks = index.get("weeks")
        if not isinstance(weeks, list):
            continue
        blocks = [_read(index_path.parent / f"{week}.json") for week in weeks]
        if not blocks:
            continue
        events = [event for block in blocks for event in block.get("events", []) if start <= event.get("ts", 0) <= end]
        events.sort(key=lambda event: event["ts"])
        if len(events) < 2:
            raise ValueError(f"janela sem extremos suficientes: {index_path}")
        first = blocks[0]
        forecast = {
            key: first[key]
            for key in ("v", "station", "source", "datum", "unit", "prediction_class", "timezone", "generated_at")
        }
        forecast.update({"valid_from": events[0]["ts"], "valid_to": events[-1]["ts"], "window_days": WINDOW_DAYS, "events": events})
        forecast["sha256"] = forecast_hash(forecast)
        target = index_path.parent / "next-30.json"
        target.write_text(json.dumps(forecast, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        written.append(target)
    return written


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("release", type=Path)
    parser.add_argument("--date", required=True, help="inicio UTC YYYY-MM-DD")
    args = parser.parse_args()
    now = datetime.fromisoformat(args.date).replace(tzinfo=UTC)
    for path in build(args.release.resolve(), now):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
