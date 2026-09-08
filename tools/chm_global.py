"""Converte o acervo anual CHM em uma janela rolante para o relogio."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from global_data import FORECAST_VERSION, WINDOW_DAYS


def _events(data: dict[str, Any]) -> list[tuple[date, int, int]]:
    result: list[tuple[date, int, int]] = []
    values = str(data["eventos"])
    indices = data["dias_indice_char"]
    for day, start in enumerate(indices, start=1):
        end = indices[day] - 1 if day < len(indices) else len(values)
        for item in values[start:end].split(";") if values[start:end] else []:
            hhmm, height = item.split(",")
            result.append((date(int(data["ano"]), 1, 1) + timedelta(days=day - 1), (int(hhmm) // 100) * 60 + int(hhmm) % 100, int(height)))
    return result


def _kind(events: list[tuple[date, int, int]], index: int) -> str:
    height = events[index][2]
    before = events[index - 1][2] if index else height
    after = events[index + 1][2] if index + 1 < len(events) else height
    return "H" if height >= before and height >= after else "L"


def build_chm_forecast(path: Path, generated_at: datetime) -> tuple[dict[str, Any], dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    offset = float(raw["fuso_horas"])
    zone = timezone(timedelta(hours=offset))
    start, end = generated_at.date(), generated_at.date() + timedelta(days=WINDOW_DAYS)
    events: list[dict[str, Any]] = []
    all_events = _events(raw)
    for index, (event_date, minute, height) in enumerate(all_events):
        if not start - timedelta(days=1) <= event_date <= end:
            continue
        moment = datetime.combine(event_date, time(minute // 60, minute % 60), tzinfo=zone)
        events.append({"ts": int(moment.astimezone(UTC).timestamp()), "local": moment.strftime("%Y-%m-%d %H:%M"), "offset_s": int(offset * 3600), "height_cm": height, "kind": _kind(all_events, index)})
    if len(events) < 2:
        raise ValueError(f"CHM sem cobertura: {path.name}")
    station = {"v": 1, "id": f"chm:{raw['porto']}", "name": str(raw["nome"]).title(), "lat_e5": round(float(raw["latitude"]) * 100000), "lon_e5": round(float(raw["longitude"]) * 100000), "timezone": f"Etc/GMT{'-' if offset > 0 else '+'}{abs(int(offset))}" if offset else "Etc/GMT", "source": "CHM", "datum": "Nivel de Reducao", "unit": "m", "prediction_class": "official_extremes", "license_status": "verified", "attribution": "Centro de Hidrografia da Marinha (CHM)", "enabled": True}
    forecast = {"v": FORECAST_VERSION, "station": station["id"], "source": "CHM", "datum": station["datum"], "unit": "m", "prediction_class": "official_extremes", "timezone": station["timezone"], "generated_at": int(generated_at.timestamp()), "valid_from": events[0]["ts"], "valid_to": events[-1]["ts"], "window_days": WINDOW_DAYS, "events": events}
    return station, forecast
