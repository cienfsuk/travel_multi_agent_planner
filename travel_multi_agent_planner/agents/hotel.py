from __future__ import annotations

import math

from ..models import HotelVenue, PointOfInterest, TripRequest


class HotelAgent:
    def attach_hotels(
        self,
        request: TripRequest,
        daily_spot_plans: list[dict],
        hotel_options: list[HotelVenue],
        llm_provider: object | None = None,
    ) -> tuple[list[dict], list[str]]:
        notes: list[str] = []
        all_candidates = list(hotel_options)
        candidates = list(all_candidates)
        if request.must_have_hotel_area:
            required_area = request.must_have_hotel_area.strip()
            candidates = [hotel for hotel in candidates if self._hotel_matches_area(hotel, required_area)]
            if not candidates:
                candidates = list(all_candidates)
                notes.append(f"酒店偏好区域“{required_area}”未命中，已降级为全量候选继续规划。")
            else:
                notes.append(f"酒店已优先匹配偏好区域：{required_area}（命中 {len(candidates)} 家）。")

        for plan in daily_spot_plans:
            spots: list[PointOfInterest] = plan["spots"]
            if not candidates:
                plan["hotel"] = None
                notes.append(f"第 {plan['day']} 天未获得酒店候选。")
                continue
            selected = self._select_hotel(request, spots, candidates, llm_provider)
            plan["hotel"] = selected
        if candidates:
            notes.append("Hotel Agent 已根据预算、区域与点位距离完成酒店分配。")
        return daily_spot_plans, notes

    def _select_hotel(
        self,
        request: TripRequest,
        spots: list[PointOfInterest],
        hotel_options: list[HotelVenue],
        llm_provider: object | None = None,
    ) -> HotelVenue:
        if llm_provider and hasattr(llm_provider, "select_hotel"):
            selected = llm_provider.select_hotel(request, hotel_options, spots)
            if isinstance(selected, dict):
                selected_name = str(selected.get("hotel_name", "")).lower()
                for hotel in hotel_options:
                    if hotel.name.lower() == selected_name:
                        return hotel
        ranked = sorted(hotel_options, key=lambda hotel: self._hotel_score(request, spots, hotel), reverse=True)
        return ranked[0]

    def _hotel_score(self, request: TripRequest, spots: list[PointOfInterest], hotel: HotelVenue) -> tuple[float, float]:
        avg_lat = sum(spot.lat for spot in spots) / max(len(spots), 1)
        avg_lon = sum(spot.lon for spot in spots) / max(len(spots), 1)
        distance_penalty = self._haversine(avg_lat, avg_lon, hotel.lat, hotel.lon)
        area_bonus = 2.0 if request.must_have_hotel_area and self._hotel_matches_area(hotel, request.must_have_hotel_area) else 0.0
        budget_bonus = self._budget_fit_bonus(request.hotel_budget_preference, hotel.price_per_night)
        hotel_text = f"{hotel.district} {hotel.address} {hotel.name}"
        tag_bonus = 1.0 if request.preferred_areas and any(area and area in hotel_text for area in request.preferred_areas) else 0.0
        return (budget_bonus + area_bonus + tag_bonus - distance_penalty, -hotel.price_per_night)

    def _hotel_matches_area(self, hotel: HotelVenue, area: str) -> bool:
        target = area.strip().lower()
        if not target:
            return True
        text = f"{hotel.name} {hotel.district} {hotel.address}".lower()
        return target in text

    def _budget_fit_bonus(self, preference: str, price_per_night: float) -> float:
        if preference == "budget":
            return 2.5 if price_per_night <= 240 else 0.5
        if preference == "premium":
            return 2.2 if price_per_night >= 420 else 1.0
        return 2.5 if 220 <= price_per_night <= 380 else 1.0

    def _haversine(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        radius = 6371.0
        d_lat = math.radians(lat2 - lat1)
        d_lon = math.radians(lon2 - lon1)
        a = (
            math.sin(d_lat / 2) ** 2
            + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2
        )
        return 2 * radius * math.asin(math.sqrt(a))
