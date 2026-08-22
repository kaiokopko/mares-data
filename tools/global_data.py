"""Contratos e geradores deterministas para a previsao internacional.

O modulo nao publica nada. Ele transforma respostas oficiais em pacotes
pequenos e versionados que podem ser hospedados como arquivos estaticos.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests


ROOT = Path(__file__).resolve().parents[1]
FORECAST_VERSION = 1
WINDOW_DAYS = 42
MAX_EVENTS_PER_WEEK = 64


def _required(data: dict[str, Any], keys: set[str], label: str) -> None:
    missing = keys.difference(data)
    if missing:
        raise ValueError(f"{label} sem campos obrigatorios: {', '.join(sorted(missing))}")


def validate_station(station: dict[str, Any]) -> None:
    _required(
        station,
        {
            "v", "id", "name", "lat_e5", "lon_e5", "timezone", "source",
            "datum", "unit", "prediction_class", "license_status", "attribution", "enabled",
        },
        "estacao",
    )
    if station["v"] != 1 or not str(station["id"]).startswith(("chm:", "noaa:", "ticon:")):
        raise ValueError("estacao com versao ou id invalido")
    if station["source"] not in {"CHM", "NOAA", "TICON"}:
        raise ValueError("fonte invalida")
    if station["unit"] != "m":
        raise ValueError("a unidade canonica precisa ser metros")
    if station["license_status"] != "verified":
        raise ValueError("estacao sem licenca verificada nao pode ser publicada")
    ZoneInfo(station["timezone"])


def load_station(path: Path) -> dict[str, Any]:
    station = json.loads(path.read_text(encoding="utf-8"))
    validate_station(station)
    return station


def _epoch(moment: datetime) -> int:
    return int(moment.astimezone(UTC).timestamp())


def _event(raw: dict[str, str], zone: ZoneInfo) -> dict[str, Any]:
    moment = datetime.strptime(raw["t"], "%Y-%m-%d %H:%M").replace(tzinfo=UTC)
    local = moment.astimezone(zone)
    return {
        "ts": _epoch(moment),
        "local": local.strftime("%Y-%m-%d %H:%M"),
        "offset_s": int(local.utcoffset().total_seconds()),
        "height_cm": round(float(raw["v"]) * 100),
        "kind": raw["type"],
    }


def build_noaa_forecast(
    station: dict[str, Any], predictions: list[dict[str, str]], generated_at: datetime
) -> dict[str, Any]:
    """Converte extremos NOAA em um formato seguro para o relogio.

    A NOAA e consultada em GMT; a conversao para horario civil acontece aqui,
    usando o fuso IANA da estacao, nunca no Monkey C.
    """
    validate_station(station)
    if station["source"] != "NOAA":
        raise ValueError("esta funcao recebe somente estacao NOAA")
    zone = ZoneInfo(station["timezone"])
    events = [_event(raw, zone) for raw in predictions]
    if len(events) < 2:
        raise ValueError("previsao NOAA precisa ter pelo menos dois extremos")
    if any(event["kind"] not in {"H", "L"} for event in events):
        raise ValueError("tipo de extremo NOAA invalido")
    if any(events[index]["ts"] >= events[index + 1]["ts"] for index in range(len(events) - 1)):
        raise ValueError("eventos NOAA precisam estar em ordem UTC estrita")

    return {
        "v": FORECAST_VERSION,
        "station": station["id"],
        "source": "NOAA",
        "datum": station["datum"],
        "unit": "m",
        "prediction_class": "official_extremes",
        "timezone": station["timezone"],
        "generated_at": _epoch(generated_at),
        "valid_from": events[0]["ts"],
        "valid_to": events[-1]["ts"],
        "events": events,
    }


def forecast_hash(forecast: dict[str, Any]) -> str:
    encoded = json.dumps(forecast, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def weekly_chunks(forecast: dict[str, Any]) -> list[dict[str, Any]]:
    """Fragmenta por segunda-feira local para cada valor caber no Storage."""
    _required(forecast, {"events", "timezone", "station"}, "previsao")
    zone = ZoneInfo(forecast["timezone"])
    chunks: dict[str, list[dict[str, Any]]] = {}
    for event in forecast["events"]:
        local = datetime.fromtimestamp(event["ts"], UTC).astimezone(zone)
        monday = (local.date() - timedelta(days=local.weekday())).isoformat()
        chunks.setdefault(monday, []).append(event)

    result = []
    for week, events in sorted(chunks.items()):
        if len(events) > MAX_EVENTS_PER_WEEK:
            raise ValueError(f"bloco semanal excessivo: {week}")
        chunk = {key: value for key, value in forecast.items() if key != "events"}
        chunk["week_start"] = week
        chunk["events"] = events
        chunk["sha256"] = forecast_hash(chunk)
        result.append(chunk)
    return result


def fetch_noaa_predictions(station_id: str, begin: datetime, days: int = WINDOW_DAYS) -> list[dict[str, str]]:
    """Baixa extremos oficiais NOAA em UTC, sem chave de API do usuario."""
    end = begin + timedelta(days=days)
    response = requests.get(
        "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter",
        params={
            "product": "predictions",
            "application": "mares-garmin",
            "begin_date": begin.strftime("%Y%m%d"),
            "end_date": end.strftime("%Y%m%d"),
            "datum": "MLLW",
            "station": station_id,
            "time_zone": "gmt",
            "units": "metric",
            "interval": "hilo",
            "format": "json",
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if "error" in payload:
        raise ValueError(f"NOAA recusou previsao: {payload['error'].get('message', payload['error'])}")
    predictions = payload.get("predictions")
    if not isinstance(predictions, list):
        raise ValueError("resposta NOAA sem predictions")
    return predictions


def write_weekly_release(station: dict[str, Any], output: Path, now: datetime) -> list[Path]:
    station_id = station["id"].split(":", 1)[1]
    predictions = fetch_noaa_predictions(station_id, now, WINDOW_DAYS)
    forecast = build_noaa_forecast(station, predictions, now)
    files = []
    for chunk in weekly_chunks(forecast):
        destination = output / "forecast" / station["source"].lower() / station_id / f"{chunk['week_start']}.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(chunk, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        files.append(destination)
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description="Gera pacotes NOAA estaticos para Marés")
    parser.add_argument("station", type=Path, help="arquivo canônico da estação")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "releases" / "global")
    parser.add_argument("--date", help="data UTC inicial YYYY-MM-DD; padrao: agora")
    args = parser.parse_args()

    now = datetime.now(UTC) if args.date is None else datetime.fromisoformat(args.date).replace(tzinfo=UTC)
    files = write_weekly_release(load_station(args.station), args.output, now)
    for file in files:
        print(file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
