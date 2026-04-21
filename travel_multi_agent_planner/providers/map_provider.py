from __future__ import annotations

import math

import requests

from ..models import TransportSegment
from .tencent_http import TencentRequestHelper


class TencentMapProvider:
    name = "tencent-map"

    def __init__(self, api_key: str | None, timeout: int = 10) -> None:
        self.api_key = api_key
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "TravelMindCourseProject/1.0"})
        self._http = TencentRequestHelper(
            api_key=api_key,
            session=self.session,
            timeout=timeout,
            service_name="腾讯路径服务",
        )

    def is_available(self) -> bool:
        return bool(self.api_key)

    def route_segments(self, day_nodes: list[dict]) -> list[TransportSegment]:
        self._ensure_key()
        segments: list[TransportSegment] = []
        for current, nxt in zip(day_nodes, day_nodes[1:]):
            distance = self._haversine(current["lat"], current["lon"], nxt["lat"], nxt["lon"])
            segment_type = self._pick_transport_mode(distance, current["kind"], nxt["kind"])
            try:
                path, raw_duration, routed_distance, final_type, path_status = self._request_best_route(current, nxt, segment_type)
            except Exception:
                path = []
                raw_duration = 0
                routed_distance = distance
                final_type = segment_type
                path_status = "missing"
            duration = self._estimate_duration(routed_distance, final_type, raw_duration)
            cost = self._estimate_cost(routed_distance, final_type)
            duration_source = "official" if raw_duration > 0 else "estimated"
            segments.append(
                TransportSegment(
                    segment_type=final_type,  # type: ignore[arg-type]
                    from_label=current["label"],
                    to_label=nxt["label"],
                    duration_minutes=duration,
                    estimated_cost=cost,
                    description=self._segment_description(final_type, current, nxt, routed_distance, duration, duration_source),
                    distance_km=routed_distance,
                    path=path,
                    path_status=path_status,
                )
            )
        return segments

    def distance_matrix(self, origins: list[dict], destinations: list[dict], mode: str = "walking") -> list[list[dict]]:
        self._ensure_key()
        if not origins or not destinations:
            return []
        payload = self._get(
            f"https://apis.map.qq.com/ws/distance/v1/matrix",
            {
                "mode": mode,
                "from": ";".join(f"{item['lat']},{item['lon']}" for item in origins),
                "to": ";".join(f"{item['lat']},{item['lon']}" for item in destinations),
                "key": self.api_key,
            },
        )
        rows = payload.get("result", {}).get("rows", [])
        return rows if isinstance(rows, list) else []

    def waypoint_order(self, hotel: dict, spots: list[dict]) -> list[int]:
        if len(spots) <= 3:
            return list(range(len(spots)))
        try:
            payload = self._get(
                "https://apis.map.qq.com/ws/direction/v1/waypoint_order",
                {
                    "from": f"{hotel['lat']},{hotel['lon']}",
                    "to": f"{spots[-1]['lat']},{spots[-1]['lon']}",
                    "waypoints": ";".join(f"{spot['lat']},{spot['lon']}" for spot in spots[:-1]),
                    "key": self.api_key,
                },
            )
            order = payload.get("result", {}).get("order", [])
            if isinstance(order, list) and len(order) == len(spots) - 1:
                return [int(index) for index in order] + [len(spots) - 1]
        except Exception:
            pass
        return self._matrix_order(hotel, spots)

    def reorder_spots(self, hotel: dict, spots: list) -> list:
        if len(spots) <= 1:
            return list(spots)
        if len(spots) <= 3:
            return self._improve_spot_order(hotel, list(spots))
        spot_nodes = [{"lat": spot.lat, "lon": spot.lon} for spot in spots]
        order = self.waypoint_order(hotel, spot_nodes)
        ordered = [spots[index] for index in order if 0 <= index < len(spots)]
        return self._improve_spot_order(hotel, ordered)

    def route_walking(self, current: dict, nxt: dict) -> tuple[list[list[float]], int, float]:
        return self._request_route("walking", current, nxt)

    def route_driving(self, current: dict, nxt: dict) -> tuple[list[list[float]], int, float]:
        return self._request_route("driving", current, nxt)

    def route_transit(self, current: dict, nxt: dict) -> tuple[list[list[float]], int, float]:
        return self._request_route("transit", current, nxt)

    def _request_best_route(self, current: dict, nxt: dict, preferred_mode: str) -> tuple[list[list[float]], int, float, str, str]:
        mode_sequence = self._mode_sequence(preferred_mode)
        last_error: Exception | None = None
        for mode in mode_sequence:
            try:
                path, duration, distance = self._request_route(mode, current, nxt)
                mapped_type = {"walking": "walk", "driving": "taxi", "transit": "metro"}.get(mode, "walk")
                if mapped_type == "metro" and distance >= 10:
                    mapped_type = "bus"
                path_status = "ok" if mode == mode_sequence[0] else "fallback"
                return path, duration, distance, mapped_type, path_status
            except Exception as exc:
                last_error = exc
                continue
        if last_error is not None:
            raise last_error
        raise RuntimeError("腾讯路径规划未返回有效路线，无法生成可靠方案。")

    def _request_route(self, mode: str, current: dict, nxt: dict) -> tuple[list[list[float]], int, float]:
        endpoint = self._endpoint_for_mode(mode)
        payload = self._get(
            endpoint,
            {
                "from": f"{current['lat']},{current['lon']}",
                "to": f"{nxt['lat']},{nxt['lon']}",
                "key": self.api_key,
            },
        )
        result = payload.get("result") or {}
        route = (result.get("routes") or [{}])[0]
        polyline = route.get("polyline") or self._extract_transit_polyline(route)
        path = self._decode_polyline(polyline)
        distance_km = round(float(route.get("distance", 0.0)) / 1000, 2)
        raw_duration_seconds = float(route.get("duration", 0.0) or 0.0)
        duration = round(raw_duration_seconds / 60) if raw_duration_seconds > 0 else 0
        if not path:
            raise RuntimeError("腾讯路径规划未返回有效路线，无法生成可靠方案。")
        return path, duration, distance_km

    def _endpoint_for_mode(self, mode: str) -> str:
        if mode == "walking":
            return "https://apis.map.qq.com/ws/direction/v1/walking/"
        if mode == "transit":
            return "https://apis.map.qq.com/ws/direction/v1/transit/"
        return "https://apis.map.qq.com/ws/direction/v1/driving/"

    def _get(self, url: str, params: dict) -> dict:
        return self._http.get(url, params)

    def _decode_polyline(self, polyline: list) -> list[list[float]]:
        if not isinstance(polyline, list) or len(polyline) < 2:
            return []
        values = [float(item) for item in polyline]
        if len(values) > 4:
            for index in range(2, len(values)):
                values[index] = values[index - 2] + values[index] / 1000000
        points: list[list[float]] = []
        for index in range(0, len(values) - 1, 2):
            lat = values[index]
            lon = values[index + 1]
            points.append([lon, lat])
        return points

    def _pick_transport_mode(self, distance_km: float, current_kind: str, next_kind: str) -> str:
        if distance_km <= 1.2:
            return "walk"
        if next_kind in {"lunch", "dinner"} and distance_km <= 2.4:
            return "walk"
        if distance_km <= 7.0:
            return "metro"
        if distance_km <= 12.0:
            return "taxi"
        return "bus"

    def _estimate_duration(self, distance_km: float, segment_type: str, raw_duration_minutes: int) -> int:
        if raw_duration_minutes > 0:
            if segment_type == "walk":
                return max(3, raw_duration_minutes)
            if segment_type == "metro":
                return max(6, raw_duration_minutes)
            if segment_type == "bus":
                return max(8, raw_duration_minutes)
            if segment_type == "taxi":
                return max(5, raw_duration_minutes)
            return max(20, raw_duration_minutes)
        if segment_type == "walk":
            return max(4, round(distance_km / 4.5 * 60))
        if segment_type == "metro":
            return max(8, round(distance_km / 28 * 60) + 6)
        if segment_type == "bus":
            return max(10, round(distance_km / 18 * 60) + 8)
        if segment_type == "taxi":
            return max(6, round(distance_km / 26 * 60) + 2)
        return max(20, raw_duration_minutes or round(distance_km / 120 * 60))

    def _estimate_cost(self, distance_km: float, segment_type: str) -> float:
        if segment_type == "walk":
            return 0.0
        if segment_type == "metro":
            return 3.0 if distance_km <= 6 else 5.0
        if segment_type == "bus":
            return 2.0
        if segment_type == "taxi":
            return round(10 + max(distance_km - 2, 0) * 2.6, 2)
        return round(max(60.0, distance_km * 0.8), 2)

    def _segment_description(self, segment_type: str, current: dict, nxt: dict, distance_km: float, duration_minutes: int, duration_source: str) -> str:
        label_map = {
            "intercity": "城际交通",
            "taxi": "打车",
            "metro": "地铁",
            "bus": "公交",
            "walk": "步行",
        }
        if duration_source == "official":
            return f"{label_map.get(segment_type, segment_type)}从 {current['label']} 前往 {nxt['label']}，腾讯路径返回约 {duration_minutes} 分钟，路线约 {distance_km:.1f} km。"
        return f"{label_map.get(segment_type, segment_type)}从 {current['label']} 前往 {nxt['label']}，腾讯未返回可靠耗时，按路线距离估算约 {duration_minutes} 分钟 / {distance_km:.1f} km。"

    def _extract_transit_polyline(self, route: dict) -> list:
        steps = route.get("steps") or []
        values: list = []
        for step in steps:
            polyline = step.get("polyline") or []
            if isinstance(polyline, list) and polyline:
                if not values:
                    values.extend(polyline)
                else:
                    values.extend(polyline[2:] if len(polyline) > 2 else polyline)
        return values

    def _mode_sequence(self, preferred_mode: str) -> list[str]:
        if preferred_mode == "walk":
            return ["walking", "transit", "driving"]
        if preferred_mode == "metro":
            return ["transit", "walking", "driving"]
        if preferred_mode == "bus":
            return ["transit", "driving", "walking"]
        if preferred_mode == "taxi":
            return ["driving", "transit", "walking"]
        return ["walking", "transit", "driving"]

    def _matrix_order(self, hotel: dict, spots: list[dict]) -> list[int]:
        try:
            rows = self.distance_matrix([hotel], spots, mode="walking")
        except Exception:
            return list(range(len(spots)))
        if not rows:
            return list(range(len(spots)))
        elements = rows[0].get("elements", []) if isinstance(rows[0], dict) else []
        ranked = sorted(
            enumerate(elements),
            key=lambda item: float((item[1] or {}).get("duration", 999999)),
        )
        return [index for index, _ in ranked]

    def _improve_spot_order(self, hotel: dict, spots: list) -> list:
        if len(spots) <= 2:
            return list(spots)
        best = list(spots)
        best_distance = self._route_distance(hotel, best)
        improved = True
        while improved:
            improved = False
            for start in range(len(best) - 2):
                for end in range(start + 2, len(best) + 1):
                    candidate = best[:start] + list(reversed(best[start:end])) + best[end:]
                    candidate_distance = self._route_distance(hotel, candidate)
                    if candidate_distance + 0.05 < best_distance:
                        best = candidate
                        best_distance = candidate_distance
                        improved = True
                        break
                if improved:
                    break
        return best

    def _route_distance(self, hotel: dict, spots: list) -> float:
        total = 0.0
        current_lat = hotel["lat"]
        current_lon = hotel["lon"]
        for spot in spots:
            total += self._haversine(current_lat, current_lon, spot.lat, spot.lon)
            current_lat = spot.lat
            current_lon = spot.lon
        return total

    def _ensure_key(self) -> None:
        if not self.api_key:
            raise RuntimeError("缺少 TENCENT_MAP_SERVER_KEY，无法执行可靠在线路线规划。")

    def _haversine(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        radius = 6371.0
        d_lat = math.radians(lat2 - lat1)
        d_lon = math.radians(lon2 - lon1)
        a = (
            math.sin(d_lat / 2) ** 2
            + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2
        )
        return 2 * radius * math.asin(math.sqrt(a))
