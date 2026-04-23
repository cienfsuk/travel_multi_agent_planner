from __future__ import annotations

import math
import re

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
        spot_nodes = [{"lat": spot.lat, "lon": spot.lon} for spot in spots]
        if len(spots) <= 3:
            order = self._matrix_order(hotel, spot_nodes)
        else:
            order = self.waypoint_order(hotel, spot_nodes)
        ordered = [spots[index] for index in order if 0 <= index < len(spots)]
        return ordered or list(spots)

    def route_walking(self, current: dict, nxt: dict) -> tuple[list[list[float]], int, float]:
        return self._request_route("walking", current, nxt)

    def route_driving(self, current: dict, nxt: dict) -> tuple[list[list[float]], int, float]:
        return self._request_route("driving", current, nxt)

    def route_transit(self, current: dict, nxt: dict) -> tuple[list[list[float]], int, float]:
        return self._request_route("transit", current, nxt)

    def plan_ordered_route(
        self,
        points: list[dict],
        mode: str = "driving",
        prefer_waypoints: bool = True,
    ) -> dict:
        self._ensure_key()
        normalized_points = self._normalize_route_points(points)
        if len(normalized_points) < 2:
            raise ValueError("At least two points are required to plan a route.")

        resolved_mode = self._normalize_route_mode(mode)
        warnings: list[str] = []

        if resolved_mode == "driving" and prefer_waypoints and len(normalized_points) <= 32:
            try:
                return self._request_driving_route_with_waypoints(normalized_points)
            except Exception as exc:
                warnings.append(f"waypoints planning failed, fallback to per-leg planning: {exc}")

        result = self._request_route_by_legs(normalized_points, resolved_mode)
        if warnings:
            result["warnings"] = warnings + result.get("warnings", [])
            if result.get("status") == "ok":
                result["status"] = "partial"
        return result

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
        safe_path = self._sanitize_path(path)
        if safe_path:
            path = safe_path
        distance_km = self._extract_route_distance_km(route, path)
        duration = self._extract_route_duration_minutes(route, distance_km, mode)
        if not path:
            raise RuntimeError("腾讯路径规划未返回有效路线，无法生成可靠方案。")
        return path, duration, distance_km

    def _endpoint_for_mode(self, mode: str) -> str:
        if mode == "walking":
            return "https://apis.map.qq.com/ws/direction/v1/walking/"
        if mode == "bicycling":
            return "https://apis.map.qq.com/ws/direction/v1/bicycling/"
        if mode == "transit":
            return "https://apis.map.qq.com/ws/direction/v1/transit/"
        return "https://apis.map.qq.com/ws/direction/v1/driving/"

    def _request_driving_route_with_waypoints(self, points: list[dict]) -> dict:
        if len(points) < 2:
            raise ValueError("At least two points are required.")
        if len(points) > 32:
            raise ValueError("Driving waypoints mode supports at most 32 ordered points.")

        params = {
            "from": f"{points[0]['lat']},{points[0]['lon']}",
            "to": f"{points[-1]['lat']},{points[-1]['lon']}",
            "key": self.api_key,
        }
        if len(points) > 2:
            params["waypoints"] = ";".join(f"{point['lat']},{point['lon']}" for point in points[1:-1])

        payload = self._get(
            "https://apis.map.qq.com/ws/direction/v1/driving/",
            params,
        )
        route = ((payload.get("result") or {}).get("routes") or [{}])[0]
        decoded_path = self._sanitize_path(self._decode_polyline(route.get("polyline") or []))
        if len(decoded_path) < 2:
            raise RuntimeError("Driving route did not return a valid polyline.")

        total_distance_km = self._extract_route_distance_km(route, decoded_path)
        total_duration_minutes = self._extract_route_duration_minutes(route, total_distance_km, "driving")
        legs = self._split_polyline_into_legs(decoded_path, points, total_duration_minutes)

        return {
            "requested_mode": "driving",
            "mode": "driving",
            "status": "ok",
            "distance_km": round(sum(float(leg.get("distance_km", 0.0)) for leg in legs), 3),
            "duration_minutes": int(sum(int(leg.get("duration_minutes", 0)) for leg in legs)),
            "path": decoded_path,
            "legs": legs,
            "warnings": [],
        }

    def _request_route_by_legs(self, points: list[dict], mode: str) -> dict:
        merged_path: list[list[float]] = []
        legs: list[dict] = []
        warnings: list[str] = []
        has_fallback = False

        for from_index in range(len(points) - 1):
            to_index = from_index + 1
            current = points[from_index]
            nxt = points[to_index]
            leg_status = "ok"
            warning = ""
            try:
                path, duration_minutes, distance_km = self._request_route(mode, current, nxt)
                safe_path = self._sanitize_path(path)
            except Exception as exc:
                has_fallback = True
                warning = f"leg {from_index + 1} failed ({exc}), fallback to straight segment."
                warnings.append(warning)
                safe_path = [
                    [float(current["lon"]), float(current["lat"])],
                    [float(nxt["lon"]), float(nxt["lat"])],
                ]
                distance_km = round(self._haversine(current["lat"], current["lon"], nxt["lat"], nxt["lon"]), 3)
                duration_minutes = max(0, round(distance_km / self._mode_speed_kmh(mode) * 60))
                leg_status = "fallback"
            if duration_minutes <= 0 and distance_km > 0:
                duration_minutes = max(1, round(distance_km / self._mode_speed_kmh(mode) * 60))

            if len(safe_path) < 2:
                safe_path = [
                    [float(current["lon"]), float(current["lat"])],
                    [float(nxt["lon"]), float(nxt["lat"])],
                ]

            if not merged_path:
                merged_path.extend(safe_path)
            else:
                merged_path.extend(safe_path[1:] if safe_path[0] == merged_path[-1] else safe_path)

            legs.append(
                {
                    "from_index": from_index,
                    "to_index": to_index,
                    "status": leg_status,
                    "distance_km": round(float(distance_km), 3),
                    "duration_minutes": int(max(0, duration_minutes)),
                    "path": safe_path,
                    "warning": warning,
                }
            )

        return {
            "requested_mode": mode,
            "mode": mode,
            "status": "partial" if has_fallback else "ok",
            "distance_km": round(sum(float(leg["distance_km"]) for leg in legs), 3),
            "duration_minutes": int(sum(int(leg["duration_minutes"]) for leg in legs)),
            "path": merged_path,
            "legs": legs,
            "warnings": warnings,
        }

    def _normalize_route_points(self, points: list[dict]) -> list[dict]:
        normalized: list[dict] = []
        for index, point in enumerate(points):
            try:
                lat = float(point.get("lat"))
                lon = float(point.get("lon"))
            except Exception as exc:
                raise ValueError(f"Invalid route point #{index + 1}: {exc}") from exc
            if not self._is_valid_lon_lat(lon, lat):
                raise ValueError(f"Route point #{index + 1} out of range: lat={lat}, lon={lon}")
            normalized.append(
                {
                    "lat": lat,
                    "lon": lon,
                    "label": str(point.get("label", "")),
                }
            )
        return normalized

    def _normalize_route_mode(self, mode: str) -> str:
        value = (mode or "").strip().lower()
        if value in {"walking", "walk"}:
            return "walking"
        if value in {"driving", "drive", "car", "taxi"}:
            return "driving"
        if value in {"bicycling", "bike", "cycling", "ebicycling"}:
            return "bicycling"
        if value in {"transit", "bus", "metro", "public_transport"}:
            return "transit"
        return "driving"

    def _mode_speed_kmh(self, mode: str) -> float:
        if mode == "walking":
            return 4.5
        if mode == "bicycling":
            return 13.0
        if mode == "transit":
            return 22.0
        return 28.0

    def _split_polyline_into_legs(
        self,
        path: list[list[float]],
        points: list[dict],
        total_duration_minutes: int,
    ) -> list[dict]:
        if len(path) < 2:
            return []
        route_indexes: list[int] = [0]
        cursor = 0
        for point in points[1:-1]:
            nearest = self._nearest_path_index(path, point["lon"], point["lat"], cursor)
            route_indexes.append(nearest)
            cursor = nearest
        route_indexes.append(len(path) - 1)

        legs: list[dict] = []
        total_path_distance = self._path_distance_km(path)
        consumed_duration = 0
        total_duration_minutes = max(0, int(round(total_duration_minutes)))

        for from_index in range(len(points) - 1):
            start_idx = route_indexes[from_index]
            end_idx = route_indexes[from_index + 1]
            if end_idx <= start_idx:
                end_idx = min(len(path) - 1, start_idx + 1)
            leg_path = path[start_idx : end_idx + 1]
            if len(leg_path) < 2:
                leg_path = [
                    [float(points[from_index]["lon"]), float(points[from_index]["lat"])],
                    [float(points[from_index + 1]["lon"]), float(points[from_index + 1]["lat"])],
                ]
            leg_distance = self._path_distance_km(leg_path)
            if from_index == len(points) - 2:
                leg_duration = max(0, total_duration_minutes - consumed_duration)
            else:
                ratio = 0.0 if total_path_distance <= 0 else leg_distance / total_path_distance
                leg_duration = max(0, round(total_duration_minutes * ratio))
                consumed_duration += leg_duration

            legs.append(
                {
                    "from_index": from_index,
                    "to_index": from_index + 1,
                    "status": "ok",
                    "distance_km": round(leg_distance, 3),
                    "duration_minutes": int(leg_duration),
                    "path": leg_path,
                    "warning": "",
                }
            )
        return legs

    def _nearest_path_index(
        self,
        path: list[list[float]],
        lon: float,
        lat: float,
        start_index: int,
    ) -> int:
        best_index = max(0, min(start_index, len(path) - 1))
        best_distance = float("inf")
        for index in range(best_index, len(path)):
            point_lon = float(path[index][0])
            point_lat = float(path[index][1])
            distance = self._haversine(lat, lon, point_lat, point_lon)
            if distance < best_distance:
                best_distance = distance
                best_index = index
        return best_index

    def _path_distance_km(self, path: list[list[float]]) -> float:
        if len(path) < 2:
            return 0.0
        total = 0.0
        for start, end in zip(path, path[1:]):
            total += self._haversine(float(start[1]), float(start[0]), float(end[1]), float(end[0]))
        return round(total, 3)

    def _sanitize_path(self, path: list[list[float]]) -> list[list[float]]:
        cleaned: list[list[float]] = []
        for point in path:
            if not isinstance(point, list) or len(point) < 2:
                continue
            lon = float(point[0])
            lat = float(point[1])
            if not self._is_valid_lon_lat(lon, lat):
                continue
            if not cleaned or cleaned[-1][0] != lon or cleaned[-1][1] != lat:
                cleaned.append([lon, lat])
        return cleaned

    def _is_valid_lon_lat(self, lon: float, lat: float) -> bool:
        return -180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0

    def _extract_route_distance_km(self, route: dict, path: list[list[float]]) -> float:
        direct_distance = self._parse_numeric(route.get("distance"))
        if direct_distance > 0:
            if direct_distance > 50:
                return round(direct_distance / 1000, 3)
            return round(direct_distance, 3)

        steps = route.get("steps") or []
        if isinstance(steps, list):
            step_distance = 0.0
            for step in steps:
                if not isinstance(step, dict):
                    continue
                value = self._parse_numeric(step.get("distance"))
                if value > 0:
                    step_distance += value
            if step_distance > 0:
                if step_distance > 50:
                    return round(step_distance / 1000, 3)
                return round(step_distance, 3)

        if path:
            return round(self._path_distance_km(path), 3)
        return 0.0

    def _extract_route_duration_minutes(self, route: dict, distance_km: float, mode: str) -> int:
        direct_candidates = [
            route.get("duration"),
            route.get("duration_in_traffic"),
            route.get("time"),
            route.get("cost_time"),
            route.get("estimated_time"),
        ]
        for raw in direct_candidates:
            value = self._parse_numeric(raw)
            if value <= 0:
                continue
            return self._normalize_duration_minutes(value, distance_km, mode)

        steps = route.get("steps") or []
        if isinstance(steps, list) and steps:
            step_minutes = 0
            has_step_duration = False
            for step in steps:
                if not isinstance(step, dict):
                    continue
                raw_step_duration = self._parse_numeric(
                    step.get("duration") or step.get("time") or step.get("cost_time")
                )
                if raw_step_duration <= 0:
                    continue
                step_distance = self._extract_route_distance_km(step, [])
                step_minutes += self._normalize_duration_minutes(
                    raw_step_duration,
                    step_distance,
                    mode,
                )
                has_step_duration = True
            if has_step_duration:
                return max(0, int(round(step_minutes)))

        if distance_km > 0:
            return max(1, round(distance_km / self._mode_speed_kmh(mode) * 60))
        return 0

    def _normalize_duration_minutes(self, raw_value: float, distance_km: float, mode: str) -> int:
        if raw_value <= 0:
            return 0
        minutes_as_minutes = raw_value
        minutes_as_seconds = raw_value / 60.0

        if distance_km <= 0.05:
            if raw_value >= 300:
                return max(0, int(round(minutes_as_seconds)))
            return max(0, int(round(minutes_as_minutes)))

        expected_speed = self._mode_speed_kmh(mode)
        min_speed, max_speed = self._mode_speed_bounds_kmh(mode)

        def score(minutes: float) -> float:
            minutes = max(minutes, 0.1)
            speed = distance_km / (minutes / 60.0)
            penalty = abs(math.log(max(speed, 0.1) / expected_speed))
            if speed < min_speed or speed > max_speed:
                penalty += 3.5
            return penalty

        score_minutes = score(minutes_as_minutes)
        score_seconds = score(minutes_as_seconds)
        chosen = minutes_as_seconds if score_seconds < score_minutes else minutes_as_minutes
        return max(0, int(round(chosen)))

    def _mode_speed_bounds_kmh(self, mode: str) -> tuple[float, float]:
        if mode == "walking":
            return (1.0, 8.0)
        if mode == "bicycling":
            return (3.0, 40.0)
        if mode == "transit":
            return (4.0, 100.0)
        return (5.0, 140.0)

    def _parse_numeric(self, value: object) -> float:
        if value is None:
            return 0.0
        if isinstance(value, (int, float)):
            if math.isfinite(float(value)):
                return float(value)
            return 0.0
        text = str(value).strip()
        if not text:
            return 0.0
        match = re.search(r"-?\d+(?:\.\d+)?", text)
        if not match:
            return 0.0
        try:
            return float(match.group(0))
        except Exception:
            return 0.0

    def _get(self, url: str, params: dict) -> dict:
        return self._http.get(url, params)

    def _decode_polyline(self, polyline: list) -> list[list[float]]:
        if not isinstance(polyline, list) or len(polyline) < 2:
            return []
        values = [float(item) for item in polyline]
        # Tencent polyline stores the first point as absolute coordinates and
        # all subsequent values as 1e6 deltas against the value two positions before.
        # This also applies to a 2-point route (len == 4), so decode whenever
        # there is at least one delta pair.
        if len(values) > 2:
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
