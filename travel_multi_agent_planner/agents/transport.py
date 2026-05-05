from __future__ import annotations

import math
from datetime import date, datetime, timedelta

from ..models import CityProfile, DailyTransportPlan, HotelVenue, IntercityOption, PointOfInterest, TransportSegment, TripRequest


class TransportAgent:
    def __init__(self, intercity_provider: object | None = None, llm_provider: object | None = None) -> None:
        self.intercity_provider = intercity_provider
        self.llm_provider = llm_provider
        self._intercity_choice_cache: dict[tuple[str, str, str], IntercityOption | None] = {}

    def build_day_transport(
        self,
        request: TripRequest,
        profile: CityProfile,
        day_spots: list[PointOfInterest],
        hotel: HotelVenue | None,
        segments: list[TransportSegment],
        day: int,
        total_days: int,
    ) -> DailyTransportPlan:
        first_district = day_spots[0].district if day_spots else profile.city
        inbound = self._infer_intercity_transport(request, profile)
        local_modes = [segment.segment_type for segment in segments if segment.segment_type != "intercity"]
        dominant = " / ".join(self._unique_preserve_order(local_modes)) if local_modes else "步行 + 地铁"
        hotel_hint = f"酒店落在 {hotel.district}，" if hotel else ""
        intra_city = f"{hotel_hint}当天优先围绕 {first_district} 展开，市内交通以 {dominant} 为主。{profile.local_transport_tip}"
        duration = sum(segment.duration_minutes for segment in segments)
        cost = round(sum(segment.estimated_cost for segment in segments if segment.segment_type != "intercity"), 2)
        route_path: list[list[float]] = []
        for segment in segments:
            if segment.path:
                route_path.extend(segment.path)
        return DailyTransportPlan(
            inbound=inbound if day == 1 else ("返程衔接见末日安排" if day < total_days else f"{profile.city} -> {request.origin} 安排返程交通"),
            intra_city=intra_city,
            walking_intensity=self._walking_intensity(day_spots),
            estimated_duration_minutes=duration,
            estimated_cost=cost,
            route_summary=self._summarize_segments(segments),
            route_path=route_path,
        )

    def estimate_round_trip_cost(self, request: TripRequest, profile: CityProfile) -> tuple[float, str]:
        outbound = self._resolve_intercity_option(request.origin, profile.city, self._departure_date(request))
        inbound = self._resolve_intercity_option(profile.city, request.origin, self._return_date(request))
        if outbound and inbound:
            total = outbound.price_cny + inbound.price_cny
            return total, (
                f"12306 查询结果：去程 {outbound.transport_code} {outbound.depart_time}-{outbound.arrive_time} "
                f"{outbound.price_cny:.1f} 元；返程 {inbound.transport_code} {inbound.depart_time}-{inbound.arrive_time} "
                f"{inbound.price_cny:.1f} 元。查询时间 {outbound.queried_at}"
            )
        origin_key = request.origin.strip().lower()
        transport = profile.intercity_transport.get(origin_key)
        if transport:
            one_way = float(transport["one_way_cost"])
            mode = str(transport["mode"])
            duration_hours = float(transport["duration_hours"])
            return one_way * 2, f"{mode} 往返，单程约 {duration_hours:.1f} 小时"
        distance = self._city_distance_estimate(request.origin, profile.city)
        if distance >= 850:
            return 1200.0, "按飞机往返估算，适用于远距离跨省出行"
        if distance >= 240:
            return 560.0, "按高铁往返估算，适用于中长距离出行"
        if distance >= 80:
            return 180.0, "按城际铁路 / 大巴往返估算"
        return 0.0, "同城或极短距离出发地，不额外计入城际交通"

    def infer_intercity_segment(self, request: TripRequest, profile: CityProfile, from_label: str, to_label: str, leg: str = "outbound") -> TransportSegment:
        travel_date = self._departure_date(request) if leg == "outbound" else self._return_date(request)
        origin_city = request.origin if leg == "outbound" else profile.city
        destination_city = profile.city if leg == "outbound" else request.origin
        option = self._resolve_intercity_option(origin_city, destination_city, travel_date)
        if option is not None:
            return TransportSegment(
                segment_type="intercity",
                from_label=from_label,
                to_label=to_label,
                duration_minutes=option.duration_minutes,
                estimated_cost=option.price_cny,
                description=(
                    f"{option.source_name} 查询：{option.transport_code} "
                    f"{option.from_station} {option.depart_time} -> {option.to_station} {option.arrive_time}，"
                    f"{option.seat_label} {option.price_cny:.1f} 元。"
                ),
                transport_code=option.transport_code,
                depart_time=option.depart_time,
                arrive_time=option.arrive_time,
                queried_at=option.queried_at,
                source_name=option.source_name,
                source_url=option.source_url,
                confidence=option.confidence,
            )
        distance = self._city_distance_estimate(request.origin, profile.city)
        if distance >= 850:
            return TransportSegment(
                segment_type="intercity",
                from_label=from_label,
                to_label=to_label,
                duration_minutes=160,
                estimated_cost=620.0,
                description=f"{request.origin} -> {profile.city} 建议优先飞机，落地后接打车或地铁进城。",
                confidence="estimated",
            )
        if distance >= 240:
            return TransportSegment(
                segment_type="intercity",
                from_label=from_label,
                to_label=to_label,
                duration_minutes=110,
                estimated_cost=280.0,
                description=f"{request.origin} -> {profile.city} 建议优先高铁，进出站更利于答辩演示说明。",
                confidence="estimated",
            )
        return TransportSegment(
            segment_type="intercity",
            from_label=from_label,
            to_label=to_label,
            duration_minutes=55,
            estimated_cost=90.0,
            description=f"{request.origin} -> {profile.city} 建议城际铁路 / 大巴，适合短途周末游。",
            confidence="estimated",
        )

    def _infer_intercity_transport(self, request: TripRequest, profile: CityProfile) -> str:
        option = self._resolve_intercity_option(request.origin, profile.city, self._departure_date(request))
        if option is not None:
            return (
                f"{request.origin} -> {profile.city} 查询到 {option.transport_code}，"
                f"出发 {option.depart_time}，到达 {option.arrive_time}，"
                f"{option.seat_label} {option.price_cny:.1f} 元。"
            )
        origin_key = request.origin.strip().lower()
        transport = profile.intercity_transport.get(origin_key)
        if transport:
            mode = str(transport["mode"])
            duration_hours = float(transport["duration_hours"])
            one_way = float(transport["one_way_cost"])
            return f"{request.origin} -> {profile.city} 建议使用 {mode}，单程约 {duration_hours:.1f} 小时，参考票价 {one_way:.0f} 元。"
        distance = self._city_distance_estimate(request.origin, profile.city)
        if distance >= 850:
            return f"{request.origin} -> {profile.city} 建议飞机，单程约 2.5 到 3.5 小时。"
        if distance >= 240:
            return f"{request.origin} -> {profile.city} 建议高铁，单程约 1.5 到 3 小时。"
        if distance >= 80:
            return f"{request.origin} -> {profile.city} 建议城际铁路 / 大巴，单程约 1 小时左右。"
        return "出发地与目的地距离较近，城际交通成本很低。"

    def _walking_intensity(self, day_spots: list[PointOfInterest]) -> str:
        total_duration = sum(spot.duration_hours for spot in day_spots)
        if total_duration >= 7:
            return "high"
        if total_duration >= 5:
            return "medium"
        return "low"

    def _summarize_segments(self, segments: list[TransportSegment]) -> str:
        if not segments:
            return "当天路线较短，以步行和地铁为主。"
        return "；".join(
            f"{segment.from_label} -> {segment.to_label}：{self._segment_label(segment.segment_type)} {segment.duration_minutes} 分钟 / {segment.estimated_cost:.0f} 元"
            for segment in segments
        )

    def _segment_label(self, segment_type: str) -> str:
        labels = {
            "intercity": "高铁/飞机/城际",
            "taxi": "打车",
            "metro": "地铁",
            "bus": "公交",
            "walk": "步行",
        }
        return labels.get(segment_type, segment_type)

    def _city_distance_estimate(self, origin: str, destination: str) -> float:
        coord_map = {
            "上海": (31.2304, 121.4737),
            "shanghai": (31.2304, 121.4737),
            "杭州": (30.2741, 120.1551),
            "hangzhou": (30.2741, 120.1551),
            "苏州": (31.2990, 120.5853),
            "suzhou": (31.2990, 120.5853),
            "南京": (32.0603, 118.7969),
            "nanjing": (32.0603, 118.7969),
            "成都": (30.5728, 104.0668),
            "chengdu": (30.5728, 104.0668),
        }
        origin_coord = coord_map.get(origin.strip().lower()) or coord_map.get(origin.strip())
        destination_coord = coord_map.get(destination.strip().lower()) or coord_map.get(destination.strip())
        if not origin_coord or not destination_coord:
            return 320.0
        return self._haversine(origin_coord[0], origin_coord[1], destination_coord[0], destination_coord[1])

    def _haversine(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        radius = 6371.0
        d_lat = math.radians(lat2 - lat1)
        d_lon = math.radians(lon2 - lon1)
        a = (
            math.sin(d_lat / 2) ** 2
            + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2
        )
        return 2 * radius * math.asin(math.sqrt(a))

    def _unique_preserve_order(self, items: list[str]) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for item in items:
            if item not in seen:
                seen.add(item)
                ordered.append(item)
        return ordered

    def _resolve_intercity_option(self, origin_city: str, destination_city: str, travel_date: str) -> IntercityOption | None:
        origin_name = self._normalize_city_name(origin_city)
        destination_name = self._normalize_city_name(destination_city)
        cache_key = (origin_name, destination_name, travel_date)
        if cache_key in self._intercity_choice_cache:
            return self._intercity_choice_cache[cache_key]
        provider = self.intercity_provider
        if provider is None or not hasattr(provider, "query_options"):
            self._intercity_choice_cache[cache_key] = None
            return None
        try:
            options = provider.query_options(origin_name, destination_name, travel_date, limit=5)
        except Exception:
            options = []
        choice = self._select_option(options)
        self._intercity_choice_cache[cache_key] = choice
        return choice

    def _select_option(self, options: list[IntercityOption]) -> IntercityOption | None:
        if not options:
            return None
        ranked = sorted(
            options,
            key=lambda item: (
                self._departure_penalty(item.depart_time),
                item.duration_minutes,
                item.price_cny,
                item.depart_time,
            ),
        )
        return ranked[0]

    def _departure_penalty(self, depart_time: str) -> int:
        minutes = self._time_to_minutes(depart_time)
        if minutes is None:
            return 0
        if minutes < 6 * 60:
            return 240
        if minutes < 7 * 60:
            return 120
        if minutes < 8 * 60:
            return 45
        if minutes < 8 * 60 + 30:
            return 15
        if minutes >= 23 * 60:
            return 90
        if minutes >= 21 * 60 + 30:
            return 30
        return 0

    def _time_to_minutes(self, value: str) -> int | None:
        try:
            hour_text, minute_text = value.split(":", 1)
            return int(hour_text) * 60 + int(minute_text)
        except Exception:
            return None

    def _departure_date(self, request: TripRequest) -> str:
        if request.departure_date:
            return request.departure_date
        return (date.today() + timedelta(days=1)).isoformat()

    def _return_date(self, request: TripRequest) -> str:
        departure = datetime.fromisoformat(self._departure_date(request)).date()
        return (departure + timedelta(days=max(0, request.days - 1))).isoformat()

    def _normalize_city_name(self, city_name: str) -> str:
        alias_map = {
            "shanghai": "上海",
            "nanjing": "南京",
            "hangzhou": "杭州",
            "suzhou": "苏州",
            "chengdu": "成都",
            "beijing": "北京",
            "guangzhou": "广州",
            "shenzhen": "深圳",
            "wuhan": "武汉",
            "xian": "西安",
            "xi'an": "西安",
        }
        raw = city_name.strip()
        return alias_map.get(raw.lower(), raw.replace("市", ""))
