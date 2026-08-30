"""Contratos e geradores deterministas para a previsao internacional.

O modulo nao publica nada. Ele transforma respostas oficiais em pacotes
pequenos e versionados que podem ser hospedados como arquivos estaticos.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests


ROOT = Path(__file__).resolve().parents[1]
FORECAST_VERSION = 1
WINDOW_DAYS = 30
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
    if station["v"] != 1 or not str(station["id"]).startswith(("chm:", "noaa:", "dmi:", "kartverket:", "ticon:")):
        raise ValueError("estacao com versao ou id invalido")
    if station["source"] not in {"CHM", "NOAA", "DMI", "KARTVERKET", "TICON"}:
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


def _iso_event(moment_text: str, value_cm: float, kind: str, zone: ZoneInfo) -> dict[str, Any]:
    moment = datetime.fromisoformat(moment_text.replace("Z", "+00:00")).astimezone(UTC)
    local = moment.astimezone(zone)
    return {
        "ts": _epoch(moment),
        "local": local.strftime("%Y-%m-%d %H:%M"),
        "offset_s": int(local.utcoffset().total_seconds()),
        "height_cm": round(value_cm),
        "kind": kind,
    }


def _official_forecast(station: dict[str, Any], events: list[dict[str, Any]], generated_at: datetime) -> dict[str, Any]:
    validate_station(station)
    events.sort(key=lambda event: event["ts"])
    if len(events) < 2:
        raise ValueError(f"previsao {station['source']} precisa ter pelo menos dois extremos")
    if any(event["kind"] not in {"H", "L"} for event in events):
        raise ValueError(f"tipo de extremo {station['source']} invalido")
    if any(events[index]["ts"] >= events[index + 1]["ts"] for index in range(len(events) - 1)):
        raise ValueError(f"eventos {station['source']} precisam estar em ordem UTC estrita")
    return {
        "v": FORECAST_VERSION,
        "station": station["id"],
        "source": station["source"],
        "datum": station["datum"],
        "unit": "m",
        "prediction_class": "official_extremes",
        "timezone": station["timezone"],
        "generated_at": _epoch(generated_at),
        "valid_from": events[0]["ts"],
        "valid_to": events[-1]["ts"],
        "events": events,
    }


def build_dmi_forecast(station: dict[str, Any], predictions: list[dict[str, Any]], generated_at: datetime) -> dict[str, Any]:
    if station["source"] != "DMI":
        raise ValueError("esta funcao recebe somente estacao DMI")
    zone = ZoneInfo(station["timezone"])
    kinds = {"maximum": "H", "minimum": "L"}
    events = [_iso_event(raw["predictionTime"], float(raw["value"]), kinds.get(raw["predictionType"], "?"), zone) for raw in predictions]
    return _official_forecast(station, events, generated_at)


def build_kartverket_forecast(station: dict[str, Any], predictions: list[dict[str, Any]], generated_at: datetime) -> dict[str, Any]:
    if station["source"] != "KARTVERKET":
        raise ValueError("esta funcao recebe somente estacao Kartverket")
    zone = ZoneInfo(station["timezone"])
    kinds = {"high": "H", "low": "L"}
    events = [_iso_event(raw["time"], float(raw["value_cm"]), kinds.get(raw["flag"], "?"), zone) for raw in predictions]
    return _official_forecast(station, events, generated_at)


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


def fetch_dmi_predictions(station_id: str, begin: datetime, days: int = WINDOW_DAYS) -> list[dict[str, Any]]:
    end = begin + timedelta(days=days)
    response = requests.get(
        "https://opendataapi.dmi.dk/v2/oceanObs/collections/tidewater/items",
        params={"datetime": f"{begin.astimezone(UTC).isoformat().replace('+00:00', 'Z')}/{end.astimezone(UTC).isoformat().replace('+00:00', 'Z')}", "stationId": station_id, "predictionType": "minimum_maximum", "limit": 1000},
        timeout=30,
    )
    response.raise_for_status()
    features = response.json().get("features")
    if not isinstance(features, list):
        raise ValueError("resposta DMI sem features")
    return [feature["properties"] for feature in features if isinstance(feature, dict) and isinstance(feature.get("properties"), dict)]


def fetch_kartverket_predictions(station_id: str, begin: datetime, days: int = WINDOW_DAYS) -> list[dict[str, Any]]:
    end = begin + timedelta(days=days)
    response = requests.get(
        "https://vannstand.kartverket.no/tideapi.php",
        params={"tide_request": "stationdata", "stationcode": station_id, "fromtime": begin.astimezone(UTC).strftime("%Y-%m-%dT%H:%M"), "totime": end.astimezone(UTC).strftime("%Y-%m-%dT%H:%M"), "datatype": "tab", "refcode": "cd", "tzone": 0, "dst": 0, "lang": "en"},
        timeout=30,
    )
    response.raise_for_status()
    root = ET.fromstring(response.content)
    errors = [element.text for element in root.findall(".//error") if element.text]
    if errors:
        raise ValueError(f"Kartverket recusou previsao: {'; '.join(errors)}")
    return [{"time": element.attrib["time"], "value_cm": element.attrib["value"], "flag": element.attrib["flag"]} for element in root.findall(".//waterlevel")]


def fetch_official_forecast(station: dict[str, Any], now: datetime) -> dict[str, Any]:
    station_id = station["id"].split(":", 1)[1]
    if station["source"] == "NOAA":
        return build_noaa_forecast(station, fetch_noaa_predictions(station_id, now, WINDOW_DAYS), now)
    if station["source"] == "DMI":
        return build_dmi_forecast(station, fetch_dmi_predictions(station_id, now, WINDOW_DAYS), now)
    if station["source"] == "KARTVERKET":
        return build_kartverket_forecast(station, fetch_kartverket_predictions(station_id, now, WINDOW_DAYS), now)
    raise ValueError(f"fonte sem adaptador remoto: {station['source']}")


def write_weekly_release(
    station: dict[str, Any], output: Path, now: datetime, forecast: dict[str, Any] | None = None
) -> list[Path]:
    station_id = station["id"].split(":", 1)[1]
    forecast = fetch_official_forecast(station, now) if forecast is None else forecast
    files = []
    for chunk in weekly_chunks(forecast):
        destination = output / "forecast" / station["source"].lower() / station_id / f"{chunk['week_start']}.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(chunk, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        files.append(destination)
    return files


def write_rolling_forecast(
    station: dict[str, Any], output: Path, now: datetime, forecast: dict[str, Any] | None = None
) -> Path:
    """Escreve uma unica previsao de 30 dias para o cliente Garmin.

    O relogio guarda somente a estacao ativa. Um documento unico reduz a
    cadeia de requisicoes Bluetooth para ``current.json`` + ``next-30.json``
    e continua pequeno o bastante para o Storage do Connect IQ.
    """
    station_id = station["id"].split(":", 1)[1]
    forecast = fetch_official_forecast(station, now) if forecast is None else dict(forecast)
    forecast["window_days"] = WINDOW_DAYS
    forecast["sha256"] = forecast_hash(forecast)
    destination = output / "forecast" / station["source"].lower() / station_id / "next-30.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(forecast, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )
    return destination


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
