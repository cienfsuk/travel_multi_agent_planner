from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import quote

import requests

from ..models import CityMatch, CityProfile, EvidenceItem, FoodVenue, HotelVenue, PointOfInterest, TravelNote
from .tencent_http import TencentRequestHelper


class TencentMapSearchProvider:
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
            service_name="腾讯位置服务",
        )
        self.alias_map = {
            "shanghai": "上海",
            "hangzhou": "杭州",
            "suzhou": "苏州",
            "nanjing": "南京",
            "chengdu": "成都",
            "beijing": "北京",
            "guangzhou": "广州",
            "shenzhen": "深圳",
            "wuhan": "武汉",
            "xian": "西安",
            "xi'an": "西安",
        }
        self._detail_cache: dict[str, dict] = {}
        self._reverse_cache: dict[str, dict] = {}
        self._weather_cache: dict[str, dict] = {}

    def is_available(self) -> bool:
        return bool(self.api_key)

    def confirm_city(self, city_name: str) -> CityMatch:
        self._ensure_key()
        normalized_query = self._normalize_city_name(city_name)
        try:
            payload = self._get(
                "https://apis.map.qq.com/ws/geocoder/v1/",
                {
                    "address": normalized_query,
                    "key": self.api_key,
                },
            )
            result = payload.get("result")
            if not isinstance(result, dict):
                raise RuntimeError(f"Unable to confirm city: {city_name}")

            components = result.get("address_components", {}) or {}
            ad_info = result.get("ad_info", {}) or {}
            location = result.get("location", {}) or {}
            country = str(components.get("nation") or ad_info.get("nation") or "中国")
            confirmed_city = str(components.get("city") or ad_info.get("city") or normalized_query).replace("?", "")
            province = str(components.get("province") or "")
            city_code = str(ad_info.get("adcode") or "")
            if country != "中国":
                raise RuntimeError(f"City is outside China: {city_name}")
            if not self._city_matches_expected(normalized_query, confirmed_city):
                raise RuntimeError(f"City mismatch: expected {city_name}, got {confirmed_city}")

            return CityMatch(
                input_name=city_name,
                normalized_query=normalized_query,
                confirmed_name=confirmed_city,
                region=province,
                country=country,
                provider="腾讯位置服务",
                lat=float(location.get("lat", 0.0)),
                lon=float(location.get("lng", 0.0)),
                city_code=city_code,
            )
        except Exception:
            return self._confirm_city_from_district_list(city_name, normalized_query)

    def _confirm_city_from_district_list(self, city_name: str, normalized_query: str) -> CityMatch:
        payload = self._get(
            "https://apis.map.qq.com/ws/district/v1/list",
            {
                "key": self.api_key,
            },
        )
        result = payload.get("result")
        if not isinstance(result, list) or not result:
            raise RuntimeError(f"Unable to confirm city: {city_name}")

        provinces = result[0] if len(result) > 0 and isinstance(result[0], list) else []
        cities = result[1] if len(result) > 1 and isinstance(result[1], list) else []

        def normalize_name(value: str) -> str:
            return str(value or "").replace("?", "").replace("?", "").replace("?", "").strip().lower()

        expected_norm = normalize_name(normalized_query)
        province_by_city_id: dict[str, str] = {}
        for province in provinces:
            if not isinstance(province, dict):
                continue
            province_name = str(province.get("fullname") or province.get("name") or "")
            cidx = province.get("cidx")
            if not isinstance(cidx, list) or len(cidx) != 2:
                continue
            start, end = cidx
            if not isinstance(start, int) or not isinstance(end, int):
                continue
            for city in cities[start : end + 1]:
                if not isinstance(city, dict):
                    continue
                city_id = str(city.get("id") or "")
                if city_id:
                    province_by_city_id[city_id] = province_name

        candidates = cities if cities else provinces
        for item in candidates:
            if not isinstance(item, dict):
                continue
            confirmed_name = str(item.get("name") or item.get("fullname") or "")
            fullname = str(item.get("fullname") or confirmed_name)
            if not (
                self._city_matches_expected(normalized_query, confirmed_name)
                or self._city_matches_expected(normalized_query, fullname)
                or normalize_name(confirmed_name) == expected_norm
                or normalize_name(fullname) == expected_norm
            ):
                continue

            location = item.get("location", {}) or {}
            city_id = str(item.get("id") or "")
            region = province_by_city_id.get(city_id) or fullname
            return CityMatch(
                input_name=city_name,
                normalized_query=normalized_query,
                confirmed_name=confirmed_name.replace("?", ""),
                region=region.replace("?", ""),
                country="中国",
                provider="腾讯位置服务（行政区划回退）",
                lat=float(location.get("lat", 0.0)),
                lon=float(location.get("lng", 0.0)),
                city_code=city_id,
            )

        raise RuntimeError(f"Unable to match city: {city_name}")

    def build_city_profile(self, destination_match: CityMatch, tastes: list[str]) -> tuple[CityProfile, list[str]]:
        city_name = destination_match.confirmed_name
        notes = [f"已通过腾讯位置服务确认城市：{city_name}。"]
        try:
            pois = self._collect_pois(destination_match)
        except Exception as exc:
            notes.append(f"景点在线检索失败：{exc}。已降级为本地补齐候选。")
            pois = []
        try:
            foods = self.search_foods(city_name, tastes)
        except Exception as exc:
            notes.append(f"餐饮在线检索失败：{exc}。已降级为本地补齐候选。")
            foods = []
        try:
            hotels = self.search_hotels(city_name, "")
        except Exception as exc:
            notes.append(f"酒店在线检索失败：{exc}。已降级为本地补齐候选。")
            hotels = []
        try:
            weather = self.weather(destination_match.city_code)
        except Exception as exc:
            notes.append(f"天气在线检索失败：{exc}。")
            weather = {}

        pois = self._ensure_minimum_pois(city_name, destination_match.lat, destination_match.lon, pois)
        foods = self._ensure_minimum_foods(city_name, destination_match.lat, destination_match.lon, tastes, foods)
        hotels = self._ensure_minimum_hotels(city_name, destination_match.lat, destination_match.lon, hotels)

        if len(pois) < 3:
            notes.append(f"{city_name} 景点候选偏少（{len(pois)}），已按最佳可用数据继续规划。")
        if len(foods) < 2:
            notes.append(f"{city_name} 餐饮候选偏少（{len(foods)}），已按最佳可用数据继续规划。")
        if len(hotels) < 1:
            notes.append(f"{city_name} 酒店候选偏少（{len(hotels)}），将按无酒店硬约束模式继续规划。")
        notes.append(f"已检索到景点 {len(pois)} 个、餐饮 {len(foods)} 个、酒店 {len(hotels)} 个。")
        if weather.get("summary"):
            notes.append(f"天气增强：{weather['summary']}")

        profile = CityProfile(
            city=city_name,
            aliases=[city_name.lower()],
            intro=f"{city_name} 的行程数据来自腾讯位置服务真实检索结果，适合做中文城市深度游展示。{weather.get('summary', '')}".strip(),
            local_transport_tip=f"{city_name} 市内路线已按真实在线点位生成，优先减少跨区折返。{weather.get('travel_tip', '')}".strip(),
            daily_local_transport_cost=28.0,
            accommodation_budget={"budget": 220.0, "balanced": 360.0, "premium": 520.0},
            intercity_transport={},
            recommended_seasons=["spring", "autumn"],
            pois=pois,
            foods=foods,
            hotels=hotels,
        )
        return profile, notes

    def _ensure_minimum_pois(
        self,
        city_name: str,
        city_lat: float,
        city_lon: float,
        pois: list[PointOfInterest],
    ) -> list[PointOfInterest]:
        merged = list(pois)
        seen = {poi.name.strip().lower() for poi in merged}
        templates = [
            ("城市博物馆", "博物馆", ["culture", "history"], "morning", 2.0, 30.0, 0.010, 0.012),
            ("城市公园", "公园", ["nature", "relaxed"], "afternoon", 2.0, 0.0, -0.008, 0.010),
            ("老街夜游", "老街", ["food", "night"], "evening", 2.0, 0.0, 0.006, -0.009),
        ]
        for suffix, category, tags, best_time, duration, ticket, lat_delta, lon_delta in templates:
            if len(merged) >= 3:
                break
            name = f"{city_name}{suffix}"
            if name.lower() in seen:
                continue
            seen.add(name.lower())
            merged.append(
                PointOfInterest(
                    name=name,
                    category=category,
                    district=city_name,
                    description=f"{city_name} {suffix}（降级补齐候选）",
                    duration_hours=duration,
                    ticket_cost=ticket,
                    lat=city_lat + lat_delta,
                    lon=city_lon + lon_delta,
                    tags=tags,
                    best_time=best_time,
                    estimated_visit_window="09:30-11:30" if best_time == "morning" else "14:00-16:00",
                    source_evidence=[self._fallback_evidence(city_name, name, "景点")],
                )
            )
        return merged

    def _ensure_minimum_foods(
        self,
        city_name: str,
        city_lat: float,
        city_lon: float,
        tastes: list[str],
        foods: list[FoodVenue],
    ) -> list[FoodVenue]:
        merged = list(foods)
        seen = {food.name.strip().lower() for food in merged}
        preferred_tastes = tastes[:2] or ["鲜", "辣"]
        templates = [
            ("本地午餐馆", "午餐补齐", 48.0, "lunch", 0.002, 0.003),
            ("本地晚餐馆", "晚餐补齐", 76.0, "dinner", -0.002, 0.004),
        ]
        for suffix, cuisine, avg_cost, meal_suitability, lat_delta, lon_delta in templates:
            if len(merged) >= 2:
                break
            name = f"{city_name}{suffix}"
            if name.lower() in seen:
                continue
            seen.add(name.lower())
            merged.append(
                FoodVenue(
                    name=name,
                    district=city_name,
                    cuisine=cuisine,
                    description=f"{city_name} {suffix}（降级补齐候选）",
                    average_cost=avg_cost,
                    tags=["food", "fallback"],
                    taste_profile=preferred_tastes,
                    meal_suitability=meal_suitability,  # type: ignore[arg-type]
                    lat=city_lat + lat_delta,
                    lon=city_lon + lon_delta,
                    source_evidence=[self._fallback_evidence(city_name, name, "餐饮")],
                )
            )
        return merged

    def _ensure_minimum_hotels(
        self,
        city_name: str,
        city_lat: float,
        city_lon: float,
        hotels: list[HotelVenue],
    ) -> list[HotelVenue]:
        if hotels:
            return hotels
        name = f"{city_name}中心酒店"
        return [
            HotelVenue(
                name=name,
                district=city_name,
                description=f"{city_name} 中心酒店（降级补齐候选）",
                price_per_night=288.0,
                lat=city_lat + 0.001,
                lon=city_lon - 0.001,
                address=f"{city_name}中心区",
                tags=["balanced", "fallback"],
                source_evidence=[self._fallback_evidence(city_name, name, "酒店")],
            )
        ]

    def _fallback_evidence(self, city_name: str, title: str, kind: str) -> EvidenceItem:
        return EvidenceItem(
            title=title,
            source_url=f"https://apis.map.qq.com/",
            snippet=f"{city_name}{kind}候选（在线失败后补齐）",
            provider="tencent-map",
            provider_label="腾讯位置服务",
            evidence_type="网页检索",
            retrieved_at=datetime.now(timezone.utc).isoformat(),
        )

    def search_pois(self, city_name: str, category: str, page_size: int = 8) -> list[PointOfInterest]:
        keyword = f"{city_name}{category}"
        items = self._place_search(keyword, city_name, page_size=page_size)
        pois: list[PointOfInterest] = []
        for index, item in enumerate(items):
            item = self._enrich_place_item(item)
            lat, lon = self._item_latlon(item)
            pois.append(
                PointOfInterest(
                    name=str(item.get("title") or item.get("name") or f"{city_name}{category}{index + 1}"),
                    category=category,
                    district=self._district_from_address(str(item.get("address", city_name)), city_name),
                    description=str(item.get("detail_description") or item.get("address") or item.get("category") or f"{city_name}{category}候选点"),
                    duration_hours=self._poi_duration(category),
                    ticket_cost=self._poi_ticket(category, index),
                    lat=lat,
                    lon=lon,
                    tags=self._poi_tags(category),
                    best_time=self._poi_best_time(category),
                    estimated_visit_window=self._poi_visit_window(category, index),
                    address=str(item.get("address") or ""),
                    source_evidence=[self._poi_evidence(item, category)],
                )
            )
        return pois

    def search_poi_by_name(self, city_name: str, poi_name: str) -> PointOfInterest | None:
        query = (poi_name or "").strip()
        if not query:
            return None
        items: list[dict] = []
        for keyword in (query, f"{city_name}{query}"):
            keyword_norm = keyword.strip().lower()
            if not keyword_norm:
                continue
            current = self._place_search(keyword, city_name, page_size=6, orderby=None)
            if current:
                items = current
                break
        if not items:
            return None

        # Required POI retrieval must follow Tencent search ranking directly.
        best_item = items[0]

        enriched = self._enrich_place_item(best_item)
        lat, lon = self._item_latlon(enriched)
        raw_category = str(enriched.get("category") or enriched.get("type") or "旅游景点")
        category = "旅游景点"
        if "博物馆" in raw_category:
            category = "博物馆"
        elif "公园" in raw_category:
            category = "公园"
        elif "老街" in raw_category or "步行街" in raw_category:
            category = "老街"
        elif "夜游" in raw_category:
            category = "夜游"

        return PointOfInterest(
            name=str(enriched.get("title") or enriched.get("name") or query),
            category=category,
            district=self._district_from_address(str(enriched.get("address", city_name)), city_name),
            description=str(enriched.get("detail_description") or enriched.get("address") or raw_category or f"{city_name}{query}"),
            duration_hours=self._poi_duration(category),
            ticket_cost=self._poi_ticket(category, 0),
            lat=lat,
            lon=lon,
            tags=self._poi_tags(category),
            best_time=self._poi_best_time(category),
            estimated_visit_window=self._poi_visit_window(category, 0),
            address=str(enriched.get("address") or ""),
            source_evidence=[self._poi_evidence(enriched, category)],
        )

    def search_hotels(self, city_name: str, area_hint: str = "") -> list[HotelVenue]:
        keyword = f"{city_name}{area_hint}酒店" if area_hint else f"{city_name}酒店"
        items = self._place_search(keyword, city_name, page_size=8)
        hotels: list[HotelVenue] = []
        seen: set[str] = set()
        for index, item in enumerate(items):
            item = self._enrich_place_item(item)
            title = str(item.get("title") or item.get("name") or "").strip()
            if not title or title.lower() in seen:
                continue
            seen.add(title.lower())
            lat, lon = self._item_latlon(item)
            price = [228.0, 318.0, 468.0, 388.0][index % 4]
            hotels.append(
                HotelVenue(
                    name=title,
                    district=self._district_from_address(str(item.get("address", city_name)), city_name),
                    description=str(item.get("detail_description") or item.get("address") or f"{city_name} 酒店候选"),
                    price_per_night=price,
                    lat=lat,
                    lon=lon,
                    address=str(item.get("address") or ""),
                    tags=self._hotel_tags_from_price(price),
                    source_evidence=[self._poi_evidence(item, "酒店")],
                )
            )
        return hotels

    def search_foods(self, city_name: str, tastes: list[str]) -> list[FoodVenue]:
        raw_items: list[dict] = []
        for keyword in self._food_keywords(city_name, tastes):
            raw_items.extend(self._place_search(keyword, city_name, page_size=5))
            if len(raw_items) >= 18:
                break
        return self._food_candidates_from_items(raw_items, city_name, tastes, "both", "地方风味", limit=6)

    def place_detail(self, poi_id: str) -> dict:
        if not poi_id:
            return {}
        if poi_id in self._detail_cache:
            return self._detail_cache[poi_id]
        try:
            payload = self._get(
                "https://apis.map.qq.com/ws/place/v1/detail",
                {
                    "id": poi_id,
                    "key": self.api_key,
                },
            )
            detail = payload.get("result") or {}
        except Exception:
            detail = {}
        self._detail_cache[poi_id] = detail if isinstance(detail, dict) else {}
        return self._detail_cache[poi_id]

    def reverse_geocode(self, lat: float, lon: float) -> dict:
        cache_key = f"{lat:.6f},{lon:.6f}"
        if cache_key in self._reverse_cache:
            return self._reverse_cache[cache_key]
        try:
            payload = self._get(
                "https://apis.map.qq.com/ws/geocoder/v1/",
                {
                    "location": f"{lat},{lon}",
                    "get_poi": 0,
                    "key": self.api_key,
                },
            )
            result = payload.get("result") or {}
        except Exception:
            result = {}
        self._reverse_cache[cache_key] = result if isinstance(result, dict) else {}
        return self._reverse_cache[cache_key]

    def weather(self, city_code: str) -> dict:
        if not city_code:
            return {}
        if city_code in self._weather_cache:
            return self._weather_cache[city_code]
        try:
            payload = self._get(
                "https://apis.map.qq.com/ws/weather/v1/",
                {
                    "adcode": city_code,
                    "type": "observe",
                    "key": self.api_key,
                },
            )
            result = payload.get("result") or {}
        except Exception:
            result = {}
        weather_info = self._summarize_weather(result)
        self._weather_cache[city_code] = weather_info
        return weather_info

    def alongby_search(self, keyword: str, path: list[list[float]], city_name: str, page_size: int = 5, radius_meters: int = 2000) -> list[dict]:
        self._ensure_key()
        sampled = self._sample_polyline(path, max_points=6)
        if len(sampled) < 2:
            return []
        polyline = ";".join(f"{lat:.6f},{lon:.6f}" for lon, lat in sampled)
        try:
            payload = self._get(
                "https://apis.map.qq.com/ws/place/v1/alongby",
                {
                    "keyword": keyword,
                    "boundary": f"region({city_name},0)",
                    "polyline": polyline,
                    "radius": radius_meters,
                    "page_size": page_size,
                    "page_index": 1,
                    "key": self.api_key,
                },
            )
        except Exception:
            return []
        data = payload.get("data")
        return data if isinstance(data, list) else []

    def search_along_route_foods(self, city_name: str, path: list[list[float]], tastes: list[str], radius_meters: int = 2000) -> list[FoodVenue]:
        queries = self._food_keywords(city_name, tastes)
        route_foods: list[FoodVenue] = []
        seen: set[str] = set()
        for query in queries[:6]:
            for item in self.alongby_search(query, path, city_name, page_size=8, radius_meters=radius_meters):
                title = str(item.get("title") or item.get("name") or "").strip()
                address = str(item.get("address") or "").strip().lower()
                dedupe_key = f"{title.lower()}|{address}"
                if not title or dedupe_key in seen or not self._is_main_meal_place(title, "both"):
                    continue
                seen.add(dedupe_key)
                lat, lon = self._item_latlon(item)
                cuisine = self._infer_cuisine(title, tastes)
                route_foods.append(
                    FoodVenue(
                        name=title,
                        district=self._district_from_address(str(item.get("address", city_name)), city_name),
                        cuisine=cuisine,
                        description=str(item.get("detail_description") or item.get("address") or f"{city_name} 沿途餐饮"),
                        average_cost=self._estimate_food_cost(title, cuisine, "both", len(route_foods)),
                        tags=["food", "沿途候选", *tastes[:2]],
                        taste_profile=tastes[:2] or ["鲜"],
                        meal_suitability="both",
                        lat=lat,
                        lon=lon,
                        address=str(item.get("address") or ""),
                        source_evidence=[self._poi_evidence(item, "沿途餐饮")],
                    )
                )
                if len(route_foods) >= 12:
                    return route_foods
            if len(route_foods) >= 10:
                break
        return route_foods

    def search_nearby_foods(
        self,
        city_name: str,
        center_lat: float,
        center_lon: float,
        tastes: list[str],
        radius_meters: int,
        meal_type: str,
    ) -> list[FoodVenue]:
        raw_items: list[dict] = []
        boundary = f"nearby({center_lat:.6f},{center_lon:.6f},{radius_meters},0)"
        for keyword in self._food_keywords(city_name, tastes, meal_type)[:5]:
            try:
                raw_items.extend(self._place_search(keyword, city_name, page_size=10, boundary=boundary))
            except Exception:
                continue
            if len(raw_items) >= 24:
                break
        return self._food_candidates_from_items(raw_items, city_name, tastes, meal_type, "近邻候选", limit=16)

    def search_area_foods(self, city_name: str, area_hint: str, tastes: list[str], meal_type: str) -> list[FoodVenue]:
        area_text = (area_hint or "").strip()
        if not area_text:
            return []
        raw_items: list[dict] = []
        for keyword in self._food_keywords(city_name, tastes, meal_type, area_hint=area_text)[:5]:
            try:
                raw_items.extend(self._place_search(keyword, city_name, page_size=10))
            except Exception:
                continue
            if len(raw_items) >= 24:
                break
        return self._food_candidates_from_items(raw_items, city_name, tastes, meal_type, "区域候选", limit=16)

    def search_travel_notes(self, request, profile: CityProfile, llm_provider: object | None = None) -> list[TravelNote]:
        if llm_provider and hasattr(llm_provider, "compose_itinerary_with_notes"):
            structured = llm_provider.compose_itinerary_with_notes(
                request,
                [
                    {
                        "spots": [poi.name for poi in profile.pois[:4]],
                        "foods": [food.name for food in profile.foods[:4]],
                        "hotels": [hotel.name for hotel in profile.hotels[:3]],
                    }
                ],
                [{"title": poi.name, "summary": poi.description} for poi in profile.pois[:3]],
            )
            if isinstance(structured, dict):
                notes: list[TravelNote] = []
                summary = str(structured.get("summary", "")).strip()
                highlights = structured.get("highlights", [])
                caution = str(structured.get("caution", "")).strip()
                if summary:
                    notes.append(
                        TravelNote(
                            title=f"{profile.city} 城市漫游主线",
                            summary=summary,
                            style_tag=request.travel_note_style,
                            source_url=profile.pois[0].source_evidence[0].source_url if profile.pois else "",
                            provider="百炼整理（基于腾讯位置服务）",
                            evidence_type="大模型整理",
                        )
                    )
                if isinstance(highlights, list):
                    for item in highlights[:2]:
                        if isinstance(item, str) and item.strip():
                            notes.append(
                                TravelNote(
                                    title=f"{profile.city} 推荐亮点",
                                    summary=item.strip(),
                                    style_tag="路线亮点",
                                    source_url=profile.pois[min(1, len(profile.pois) - 1)].source_evidence[0].source_url if profile.pois else "",
                                    provider="百炼整理（基于腾讯位置服务）",
                                    evidence_type="大模型整理",
                                )
                            )
                if caution:
                    notes.append(
                        TravelNote(
                            title=f"{profile.city} 行程提醒",
                            summary=caution,
                            style_tag="避坑提醒",
                            source_url=profile.foods[0].source_evidence[0].source_url if profile.foods else "",
                            provider="百炼整理（基于腾讯位置服务）",
                            evidence_type="大模型整理",
                        )
                    )
                if notes:
                    return notes

        district = profile.pois[0].district if profile.pois else profile.city
        return [
            TravelNote(
                title=f"{profile.city} 城市漫游主线",
                summary=f"建议把 {district} 一带的核心景点串成白天主线，晚餐放在夜游或老街周边，减少跨区折返。",
                style_tag=request.travel_note_style,
                source_url=profile.pois[0].source_evidence[0].source_url if profile.pois else "",
                provider="规则整理（基于腾讯位置服务）",
                evidence_type="攻略聚合",
            ),
            TravelNote(
                title=f"{profile.city} 美食与住宿建议",
                summary=f"午餐优先安排在景点附近，晚餐放在更有代表性的本地馆子；酒店优先选择靠近景点聚集区和交通换乘点。",
                style_tag="预算友好",
                source_url=profile.hotels[0].source_evidence[0].source_url if profile.hotels else "",
                provider="规则整理（基于腾讯位置服务）",
                evidence_type="攻略聚合",
            ),
        ]

    def _collect_pois(self, destination_match: CityMatch) -> list[PointOfInterest]:
        city_name = destination_match.confirmed_name
        categories = ["旅游景点", "博物馆", "公园", "老街"]
        raw: list[PointOfInterest] = []
        for category in categories:
            raw.extend(self.search_pois(city_name, category, page_size=4))
            if len(raw) >= 10:
                break
        unique: list[PointOfInterest] = []
        seen: set[str] = set()
        for poi in raw:
            if poi.name.lower() in seen:
                continue
            seen.add(poi.name.lower())
            unique.append(poi)
            if len(unique) >= 8:
                break
        return unique

    def _enrich_place_item(self, item: dict) -> dict:
        enriched = dict(item)
        poi_id = str(enriched.get("id") or enriched.get("_id") or "")
        detail = self.place_detail(poi_id)
        detail_result = detail if isinstance(detail, dict) else {}
        location = detail_result.get("location", {}) or {}
        if location:
            enriched["location"] = {"lat": location.get("lat", enriched.get("location", {}).get("lat")), "lng": location.get("lng", enriched.get("location", {}).get("lng"))}
        address = str(detail_result.get("address") or enriched.get("address") or "")
        if address:
            enriched["address"] = address
        category = detail_result.get("category")
        if category:
            enriched["category"] = category
        detail_parts = [
            str(detail_result.get("address") or "").strip(),
            str(detail_result.get("tel") or "").strip(),
            str(detail_result.get("type") or "").strip(),
        ]
        detail_text = " | ".join(part for part in detail_parts if part)
        if detail_text:
            enriched["detail_description"] = detail_text
        if not address:
            lat, lon = self._item_latlon(enriched)
            reverse = self.reverse_geocode(lat, lon)
            formatted = str(reverse.get("address") or reverse.get("formatted_addresses", {}).get("recommend") or "").strip()
            if formatted:
                enriched["address"] = formatted
        return enriched

    def _place_search(
        self,
        keyword: str,
        city_name: str,
        page_size: int = 6,
        boundary: str | None = None,
        orderby: str | None = "_distance",
    ) -> list[dict]:
        self._ensure_key()
        params = {
            "keyword": keyword,
            "boundary": boundary or f"region({city_name},0)",
            "page_size": page_size,
            "page_index": 1,
            "key": self.api_key,
        }
        if orderby:
            params["orderby"] = orderby
        payload = self._get(
            "https://apis.map.qq.com/ws/place/v1/search",
            params,
        )
        data = payload.get("data")
        return data if isinstance(data, list) else []

    def _food_keywords(self, city_name: str, tastes: list[str], meal_type: str = "both", area_hint: str = "") -> list[str]:
        prefix = f"{city_name}{area_hint}" if area_hint else city_name
        base_keywords = [f"{prefix}餐厅", f"{prefix}美食", f"{prefix}本地菜", f"{prefix}特色菜", f"{prefix}正餐"]
        taste_queries: list[str] = []
        for taste in tastes[:3]:
            taste_queries.extend([f"{prefix}{taste}", f"{prefix}{taste}餐厅", f"{prefix}{taste}美食"])
            if any(token in taste for token in ["日料", "日本", "寿司"]):
                taste_queries.extend([f"{prefix}日料", f"{prefix}日本料理", f"{prefix}寿司"])
            if "火锅" in taste:
                taste_queries.extend([f"{prefix}火锅", f"{prefix}川味火锅"])
            if any(token in taste for token in ["烧烤", "烤肉"]):
                taste_queries.extend([f"{prefix}烧烤", f"{prefix}烤肉"])
            if "海鲜" in taste:
                taste_queries.extend([f"{prefix}海鲜", f"{prefix}海鲜餐厅"])
        if meal_type == "lunch":
            meal_queries = [f"{prefix}午餐", f"{prefix}面馆", f"{prefix}简餐", f"{prefix}本地快餐", f"{prefix}家常菜"]
        elif meal_type == "dinner":
            meal_queries = [f"{prefix}晚餐", f"{prefix}正餐", f"{prefix}本帮菜", f"{prefix}火锅", f"{prefix}烧烤", f"{prefix}酒楼", f"{prefix}私房菜", f"{prefix}特色餐厅"]
        else:
            meal_queries = [f"{prefix}火锅", f"{prefix}烧烤", f"{prefix}酒楼", f"{prefix}地方菜"]
        deduped: list[str] = []
        seen: set[str] = set()
        for keyword in base_keywords + taste_queries + meal_queries:
            key = keyword.strip().lower()
            if key and key not in seen:
                seen.add(key)
                deduped.append(keyword)
        return deduped

    def _food_candidates_from_items(
        self,
        raw_items: list[dict],
        city_name: str,
        tastes: list[str],
        meal_type: str,
        source_tag: str,
        limit: int = 8,
    ) -> list[FoodVenue]:
        foods: list[FoodVenue] = []
        seen: set[str] = set()
        for index, item in enumerate(raw_items):
            title = str(item.get("title") or item.get("name") or "").strip()
            address = str(item.get("address") or "").strip().lower()
            dedupe_key = f"{title.lower()}|{address}"
            if not title or dedupe_key in seen or not self._is_main_meal_place(title, meal_type):
                continue
            seen.add(dedupe_key)
            lat, lon = self._item_latlon(item)
            cuisine = self._infer_cuisine(title, tastes)
            avg_cost = self._estimate_food_cost(title, cuisine, meal_type, index)
            foods.append(
                FoodVenue(
                    name=title,
                    district=self._district_from_address(str(item.get("address", city_name)), city_name),
                    cuisine=cuisine,
                    description=str(item.get("detail_description") or item.get("address") or f"{city_name} 餐饮候选"),
                    average_cost=avg_cost,
                    tags=["food", source_tag, *tastes[:2]],
                    taste_profile=tastes[:2] or ["鲜"],
                    meal_suitability=meal_type if meal_type in {"lunch", "dinner"} else "both",
                    lat=lat,
                    lon=lon,
                    address=str(item.get("address") or ""),
                    source_evidence=[self._poi_evidence(item, "餐饮")],
                )
            )
            if len(foods) >= limit:
                break
        return foods

    def _get(self, url: str, params: dict) -> dict:
        return self._http.get(url, params)

    def _ensure_key(self) -> None:
        if not self.api_key:
            raise RuntimeError("缺少 TENCENT_MAP_SERVER_KEY，无法执行可靠在线搜索。")

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    def _normalize_city_name(self, city_name: str) -> str:
        raw = city_name.strip()
        return self.alias_map.get(raw.lower(), raw.replace("市", ""))

    def _city_matches_expected(self, expected: str, confirmed: str) -> bool:
        expected_norm = expected.replace("市", "").lower()
        confirmed_norm = confirmed.replace("市", "").lower()
        if expected_norm == confirmed_norm:
            return True
        # 特殊处理：县级市/区可能返回上级城市名
        # 都江堰 -> 成都（都江堰是成都市下辖县级市）
        # 下面列出常见的有从属关系的地区
        subordinate_map = {
            "都江堰": ["成都"],
            "郫都": ["成都"],
            "双流": ["成都"],
            "温江": ["成都"],
            "新都": ["成都"],
            "龙泉驿": ["成都"],
            "青白江": ["成都"],
            "彭州": ["成都"],
            "邛崃": ["成都"],
            "崇州": ["成都"],
            "大邑": ["成都"],
            "蒲江": ["成都"],
            "简阳": ["成都"],
            "延庆": ["北京"],
            "密云": ["北京"],
            "怀柔": ["北京"],
            "平谷": ["北京"],
            "门头沟": ["北京"],
            "房山": ["北京"],
            "顺义": ["北京"],
            "通州": ["北京"],
            "昌平": ["北京"],
            "大兴": ["北京"],
            "石景山": ["北京"],
            "浦东": ["上海"],
            "松江": ["上海"],
            "嘉定": ["上海"],
            "青浦": ["上海"],
            "奉贤": ["上海"],
            "闵行": ["上海"],
            "宝山": ["上海"],
            "金山": ["上海"],
            "崇明": ["上海"],
            "江宁": ["南京"],
            "浦口": ["南京"],
            "六合": ["南京"],
            "溧水": ["南京"],
            "高淳": ["南京"],
            "萧山": ["杭州"],
            "余杭": ["杭州"],
            "临平": ["杭州"],
            "富阳": ["杭州"],
            "临安": ["杭州"],
            "桐庐": ["杭州"],
            "建德": ["杭州"],
            "淳安": ["杭州"],
            "吴江": ["苏州"],
            "昆山": ["苏州"],
            "常熟": ["苏州"],
            "张家港": ["苏州"],
            "太仓": ["苏州"],
            "相城": ["苏州"],
            "吴中": ["苏州"],
        }
        # 检查输入城市是否是确认城市的下辖区县
        if expected_norm in subordinate_map:
            if confirmed_norm in subordinate_map[expected_norm]:
                return True
        # 检查确认城市是否是输入城市的上级（双向检查）
        for sub, mains in subordinate_map.items():
            if expected_norm == sub and confirmed_norm in mains:
                return True
            if expected_norm in mains and confirmed_norm == sub:
                return True
        return False

    def _poi_evidence(self, item: dict, category: str) -> EvidenceItem:
        title = str(item.get("title") or item.get("name") or category)
        return EvidenceItem(
            title=title,
            source_url=self._build_qqmap_url(item),
            snippet=str(item.get("address") or item.get("category") or title),
            provider="tencent-map",
            provider_label="腾讯位置服务",
            evidence_type="网页检索",
            retrieved_at=self._now(),
            poi_id=str(item.get("id") or item.get("_id") or ""),
        )

    def _build_qqmap_url(self, item: dict) -> str:
        lat, lon = self._item_latlon(item)
        title = quote(str(item.get("title") or item.get("name") or "位置"))
        return f"https://apis.map.qq.com/uri/v1/marker?marker=coord:{lat},{lon};title:{title}&referer=travelmind"

    def _item_latlon(self, item: dict) -> tuple[float, float]:
        location = item.get("location", {}) or {}
        lat = float(location.get("lat", 0.0))
        lon = float(location.get("lng", 0.0))
        if not lat or not lon:
            raise RuntimeError("腾讯位置服务返回的点位缺少坐标，无法生成可靠方案。")
        return lat, lon

    def _district_from_address(self, address: str, fallback_city: str) -> str:
        cleaned = re.sub(r"\([^)]*\)", "", address or "").strip()
        cleaned = re.sub(r"（[^）]*）", "", cleaned).strip()
        if not cleaned:
            return fallback_city
        municipality_match = re.search(r"^(.*?市.*?(?:区|县|旗))", cleaned)
        if municipality_match:
            return municipality_match.group(1).strip()
        city_district_match = re.search(r"^(.*?(?:区|县|旗))", cleaned)
        if city_district_match:
            return city_district_match.group(1).strip()
        parts = [part.strip() for part in cleaned.split(",") if part.strip()]
        if parts:
            return parts[0]
        return fallback_city

    def _sample_polyline(self, path: list[list[float]], max_points: int = 6) -> list[list[float]]:
        if len(path) <= max_points:
            return path
        step = max(1, len(path) // max_points)
        sampled = path[::step]
        if sampled[-1] != path[-1]:
            sampled.append(path[-1])
        return sampled[:max_points]

    def _summarize_weather(self, result: dict) -> dict:
        observe = result.get("observe_data", {}) or result.get("observe", {}) or {}
        forecast = result.get("forecast_data", []) or result.get("forecast", []) or []
        weather = str(observe.get("weather") or "").strip()
        temp = observe.get("degree")
        summary_parts = []
        if weather:
            summary_parts.append(weather)
        if temp not in (None, ""):
            summary_parts.append(f"{temp}°C")
        summary = "，".join(summary_parts)
        travel_tip = ""
        if "雨" in weather:
            travel_tip = "建议优先把室内景点安排在前半天，并携带雨具。"
        elif "晴" in weather or "多云" in weather:
            travel_tip = "适合把室外步行和城市漫游安排在白天。"
        if not summary and forecast:
            first = forecast[0] or {}
            summary = str(first.get("weather") or "")
        return {"summary": summary, "travel_tip": travel_tip}

    def _poi_duration(self, category: str) -> float:
        return {
            "博物馆": 2.5,
            "公园": 2.0,
            "老街": 2.0,
            "夜游": 2.5,
        }.get(category, 2.0)

    def _poi_ticket(self, category: str, index: int) -> float:
        if category in {"公园", "老街"}:
            return 0.0
        if category == "博物馆":
            return 30.0 + (index % 2) * 10.0
        if category == "夜游":
            return 48.0
        return 20.0 + (index % 3) * 15.0

    def _poi_tags(self, category: str) -> list[str]:
        mapping = {
            "旅游景点": ["culture", "photography", "citywalk"],
            "博物馆": ["culture", "history"],
            "公园": ["nature", "relaxed"],
            "老街": ["food", "shopping", "night"],
            "夜游": ["night", "photography", "food"],
        }
        return mapping.get(category, ["culture"])

    def _poi_best_time(self, category: str) -> str:
        mapping = {
            "博物馆": "morning",
            "公园": "afternoon",
            "老街": "evening",
            "夜游": "night",
        }
        return mapping.get(category, "afternoon")

    def _poi_visit_window(self, category: str, index: int) -> str:
        mapping = {
            "博物馆": "09:00-11:30",
            "公园": "13:30-15:30",
            "老街": "16:30-18:30",
            "夜游": "19:00-21:00",
        }
        return mapping.get(category, ["09:30-11:00", "13:00-15:00", "15:30-17:30"][index % 3])

    def _infer_cuisine(self, title: str, tastes: list[str]) -> str:
        if any(token in title for token in ["冰糖葫芦", "糖葫芦", "甜品", "蛋糕", "奶茶", "咖啡", "茶饮", "饮品"]):
            return "甜品饮品"
        if any(token in title for token in ["日料", "日本料理", "寿司", "刺身", "烧鸟"]):
            return "日料"
        if any(token in title for token in ["韩料", "韩国料理", "韩餐", "部队锅"]):
            return "韩料"
        if any(token in title for token in ["烧烤", "烤肉", "烤串"]):
            return "烧烤"
        if any(token in title for token in ["海鲜", "鱼", "虾", "蟹"]):
            return "海鲜"
        if "面" in title:
            return "面食"
        if "火锅" in title:
            return "火锅"
        if "小吃" in title:
            return "地方小吃"
        return "地方风味"

    def _is_main_meal_place(self, title: str, meal_type: str) -> bool:
        light_tokens = [
            "冰糖葫芦",
            "糖葫芦",
            "奶茶",
            "茶饮",
            "饮品",
            "咖啡",
            "甜品",
            "蛋糕",
            "面包",
            "冰淇淋",
            "果汁",
            "零食",
            "coco",
            "都可",
            "喜茶",
            "奈雪",
            "一点点",
            "蜜雪冰城",
            "茶百道",
            "古茗",
            "沪上阿姨",
            "霸王茶姬",
            "书亦烧仙草",
            "瑞幸",
            "星巴克",
        ]
        normalized = title.lower()
        if any(token in normalized for token in light_tokens):
            return False
        if meal_type == "dinner" and any(token in title for token in ["小吃", "轻食", "茶铺", "快餐"]):
            return False
        return True

    def _estimate_food_cost(self, title: str, cuisine: str, meal_type: str, index: int) -> float:
        text = f"{title} {cuisine}"
        if any(token in text for token in ["甜品", "饮品", "奶茶", "咖啡", "糖葫芦"]):
            base = 24.0
        elif any(token in text for token in ["火锅", "烧烤", "烤肉", "海鲜", "酒楼", "私房"]):
            base = 118.0
        elif any(token in text for token in ["日料", "日本料理", "寿司", "韩料"]):
            base = 108.0
        elif any(token in text for token in ["面", "粉", "快餐", "简餐"]):
            base = 46.0
        else:
            base = 76.0
        if meal_type == "dinner":
            base *= 1.18
        elif meal_type == "lunch":
            base *= 0.92
        return round(base + (index % 4) * 8.0, 2)

    def _hotel_tags_from_price(self, price: float) -> list[str]:
        if price <= 240:
            return ["budget", "地铁便捷"]
        if price <= 380:
            return ["balanced", "核心景区"]
        return ["premium", "舒适", "景观"]
