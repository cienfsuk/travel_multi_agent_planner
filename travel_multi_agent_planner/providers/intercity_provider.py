from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests

from ..models import IntercityOption


class ChinaRailway12306Provider:
    name = "china-railway-12306"

    def __init__(self, timeout: int = 20) -> None:
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://kyfw.12306.cn/otn/leftTicket/init",
                "Accept": "application/json,text/javascript,*/*;q=0.01",
            }
        )
        self._cache_dir = Path(__file__).resolve().parent.parent / "_rail_cache"
        self._station_index: list[dict] | None = None
        self._query_cache: dict[tuple[str, str, str], list[IntercityOption]] = {}
        self._session_ready = False

    def is_available(self) -> bool:
        return True

    def query_options(self, origin_city: str, destination_city: str, travel_date: str, limit: int = 5) -> list[IntercityOption]:
        cache_key = (origin_city.strip(), destination_city.strip(), travel_date.strip())
        if cache_key in self._query_cache:
            return self._query_cache[cache_key][:limit]
        origin_stations = self._major_stations(origin_city)
        destination_stations = self._major_stations(destination_city)
        if not origin_stations or not destination_stations:
            self._query_cache[cache_key] = []
            return []
        self._ensure_session_ready()
        candidates: dict[str, IntercityOption] = {}
        for origin_station in origin_stations[:3]:
            for destination_station in destination_stations[:3]:
                if origin_station["code"] == destination_station["code"]:
                    continue
                for option in self._query_station_pair(origin_station, destination_station, travel_date):
                    option_key = f"{option.transport_code}|{option.from_station}|{option.to_station}|{option.depart_time}"
                    previous = candidates.get(option_key)
                    if previous is None or option.price_cny < previous.price_cny:
                        candidates[option_key] = option
                if len(candidates) >= limit * 3:
                    break
            if len(candidates) >= limit * 3:
                break
        ranked = sorted(candidates.values(), key=self._sort_key)
        selected = ranked[:limit]
        self._query_cache[cache_key] = selected
        return selected

    def _query_station_pair(self, origin_station: dict, destination_station: dict, travel_date: str) -> list[IntercityOption]:
        try:
            payload = self._query_left_ticket(origin_station["code"], destination_station["code"], travel_date)
        except Exception:
            return []
        data = payload.get("data") or {}
        rows = data.get("result") or []
        station_map = data.get("map") or {}
        options: list[IntercityOption] = []
        for row in rows[:20]:
            option = self._parse_option(row, station_map, travel_date)
            if option is None:
                continue
            if option.from_station != origin_station["name"] or option.to_station != destination_station["name"]:
                continue
            priced = self._attach_price(option)
            if priced is not None:
                options.append(priced)
            if len(options) >= 6:
                break
        return options

    def _query_left_ticket(self, from_code: str, to_code: str, travel_date: str) -> dict:
        for attempt in range(2):
            self._ensure_session_ready(force_refresh=attempt > 0)
            response = self.session.get(
                "https://kyfw.12306.cn/otn/leftTicket/query",
                params={
                    "leftTicketDTO.train_date": travel_date,
                    "leftTicketDTO.from_station": from_code,
                    "leftTicketDTO.to_station": to_code,
                    "purpose_codes": "ADULT",
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            try:
                return response.json()
            except Exception:
                continue
        raise RuntimeError("12306 余票接口未返回有效 JSON。")

    def _attach_price(self, option: IntercityOption) -> IntercityOption | None:
        price_data = {}
        for attempt in range(2):
            try:
                if attempt > 0:
                    self._ensure_session_ready(force_refresh=True)
                response = self.session.get(
                    "https://kyfw.12306.cn/otn/leftTicket/queryTicketPrice",
                    params={
                        "train_no": option.train_no,
                        "from_station_no": option.from_station_no,
                        "to_station_no": option.to_station_no,
                        "seat_types": option.seat_types,
                        "train_date": option.travel_date,
                    },
                    timeout=self.timeout,
                )
                response.raise_for_status()
                price_data = response.json().get("data") or {}
                break
            except Exception:
                price_data = {}
                continue
        if not price_data:
            return None
        seat_label, price = self._pick_price(price_data, option.transport_code)
        if seat_label == "" or price <= 0:
            return None
        option.seat_label = seat_label
        option.price_cny = price
        option.note = f"{option.transport_code}｜{seat_label} {price:.1f} 元"
        return option

    def _pick_price(self, price_data: dict, transport_code: str) -> tuple[str, float]:
        seat_map = {
            "A9": "商务座",
            "P": "特等座",
            "M": "一等座",
            "O": "二等座",
            "A6": "高级软卧",
            "A4": "软卧",
            "A3": "硬卧",
            "A1": "硬座",
            "WZ": "无座",
        }
        prices: dict[str, float] = {}
        for key, label in seat_map.items():
            value = price_data.get(key)
            if isinstance(value, str):
                amount = self._parse_price(value)
                if amount > 0:
                    prices[label] = amount
        if not prices:
            return "", 0.0
        preferred = ["二等座", "一等座", "商务座", "无座"] if transport_code[:1] in {"G", "D", "C"} else ["硬座", "无座", "硬卧", "软卧"]
        for label in preferred:
            if label in prices:
                return label, prices[label]
        best_label = min(prices.items(), key=lambda item: item[1])[0]
        return best_label, prices[best_label]

    def _parse_option(self, row: str, station_map: dict, travel_date: str) -> IntercityOption | None:
        parts = row.split("|")
        if len(parts) < 36:
            return None
        train_code = parts[3].strip()
        if not train_code:
            return None
        from_station = station_map.get(parts[6], parts[6])
        to_station = station_map.get(parts[7], parts[7])
        duration_minutes = self._duration_to_minutes(parts[10])
        start_train_date = self._format_12306_date(parts[13]) or travel_date
        source_url = self._build_query_url(from_station, parts[6], to_station, parts[7], travel_date)
        return IntercityOption(
            mode="rail",
            transport_code=train_code,
            from_station=from_station,
            to_station=to_station,
            depart_time=parts[8],
            arrive_time=parts[9],
            duration_minutes=duration_minutes,
            price_cny=0.0,
            seat_label="",
            queried_at=self._now(),
            source_name="中国铁路12306",
            source_url=source_url,
            train_no=parts[2],
            from_station_no=parts[16],
            to_station_no=parts[17],
            seat_types=parts[35],
            travel_date=start_train_date,
            confidence="queried",
        )

    def _major_stations(self, city_name: str) -> list[dict]:
        normalized = city_name.replace("市", "").strip()
        stations = []
        for station in self._station_entries():
            station_city = station.get("city", "").replace("市", "").strip()
            station_name = station["name"]
            if station_city == normalized or station_name == normalized or station_name.startswith(normalized):
                stations.append(station)
        ranked = sorted(stations, key=lambda station: self._station_score(normalized, station["name"]))
        deduped: list[dict] = []
        seen: set[str] = set()
        for station in ranked:
            if station["code"] in seen:
                continue
            seen.add(station["code"])
            deduped.append(station)
        return deduped

    def _station_entries(self) -> list[dict]:
        if self._station_index is not None:
            return self._station_index
        cache_path = self._cache_dir / "station_index.json"
        try:
            response = self.session.get("https://kyfw.12306.cn/otn/resources/js/framework/station_name.js", timeout=self.timeout)
            response.raise_for_status()
            text = response.text
            body = text.split("='", 1)[1].rsplit("';", 1)[0]
            entries: list[dict] = []
            for chunk in body.split("@"):
                if not chunk:
                    continue
                parts = chunk.split("|")
                if len(parts) < 3:
                    continue
                entries.append(
                    {
                        "name": parts[1].strip(),
                        "code": parts[2].strip(),
                        "city": parts[7].strip() if len(parts) > 7 else "",
                    }
                )
            if entries:
                self._station_index = entries
                self._cache_dir.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
                return entries
        except Exception:
            pass
        if cache_path.exists():
            try:
                self._station_index = json.loads(cache_path.read_text(encoding="utf-8"))
                return self._station_index
            except Exception:
                pass
        self._station_index = []
        return self._station_index

    def _ensure_session_ready(self, force_refresh: bool = False) -> None:
        if self._session_ready and not force_refresh:
            return
        if force_refresh:
            self.session.cookies.clear()
        response = self.session.get("https://kyfw.12306.cn/otn/leftTicket/init?linktypeid=dc", timeout=self.timeout)
        response.raise_for_status()
        self._session_ready = True

    def _station_score(self, city_name: str, station_name: str) -> tuple[int, int, str]:
        if station_name == city_name:
            return (0, len(station_name), station_name)
        priorities = ["南", "虹桥", "东", "西", "北"]
        for index, token in enumerate(priorities, start=1):
            if station_name == f"{city_name}{token}":
                return (index, len(station_name), station_name)
        if station_name.startswith(city_name):
            return (len(priorities) + 2, len(station_name), station_name)
        return (99, len(station_name), station_name)

    def _duration_to_minutes(self, text: str) -> int:
        match = re.match(r"(?P<hour>\d{2}):(?P<minute>\d{2})", text or "")
        if not match:
            return 0
        return int(match.group("hour")) * 60 + int(match.group("minute"))

    def _format_12306_date(self, text: str) -> str:
        if len(text) != 8 or not text.isdigit():
            return ""
        return f"{text[0:4]}-{text[4:6]}-{text[6:8]}"

    def _parse_price(self, text: str) -> float:
        cleaned = str(text).replace("¥", "").strip()
        try:
            return float(cleaned)
        except Exception:
            return 0.0

    def _build_query_url(self, from_name: str, from_code: str, to_name: str, to_code: str, travel_date: str) -> str:
        return (
            "https://kyfw.12306.cn/otn/leftTicket/init?"
            f"linktypeid=dc&fs={quote(from_name)},{from_code}&ts={quote(to_name)},{to_code}&date={travel_date}&flag=N,N,Y"
        )

    def _sort_key(self, option: IntercityOption) -> tuple[int, int, float, str]:
        mode_rank = 0 if option.transport_code[:1] in {"G", "D", "C"} else 1
        return (mode_rank, option.duration_minutes, option.price_cny, option.depart_time)

    def _now(self) -> str:
        return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
