import json
import shutil
import unittest
from pathlib import Path

from travel_multi_agent_planner import persistence
from travel_multi_agent_planner.agents.transport import TransportAgent
from travel_multi_agent_planner.app import _build_animation_bundle
from travel_multi_agent_planner.config import AppConfig
from travel_multi_agent_planner.models import (
    CityMatch,
    CityProfile,
    EvidenceItem,
    FoodVenue,
    HotelVenue,
    IntercityOption,
    PointOfInterest,
    TravelNote,
    TripRequest,
)
from travel_multi_agent_planner.orchestrator import TravelPlanningOrchestrator


class FakeTencentSearchProvider:
    name = "tencent-map"

    def __init__(self) -> None:
        self.city_aliases = {
            "上海": "上海",
            "shanghai": "上海",
            "南京": "南京",
            "nanjing": "南京",
            "杭州": "杭州",
            "hangzhou": "杭州",
            "苏州": "苏州",
            "suzhou": "苏州",
            "成都": "成都",
            "chengdu": "成都",
        }
        self.city_coords = {
            "上海": (31.2304, 121.4737),
            "南京": (32.0603, 118.7969),
            "杭州": (30.2741, 120.1551),
            "苏州": (31.2990, 120.5853),
            "成都": (30.5728, 104.0668),
        }

    def _evidence(self, city: str, title: str) -> list[EvidenceItem]:
        return [
            EvidenceItem(
                title=title,
                source_url=f"https://example.com/{city}/{title}",
                snippet=f"{city} 在线候选：{title}",
                provider="tencent-map",
                provider_label="腾讯位置服务",
                evidence_type="网页检索",
                retrieved_at="2026-04-12T00:00:00Z",
            )
        ]

    def is_available(self) -> bool:
        return True

    def confirm_city(self, city_name: str) -> CityMatch:
        normalized = self.city_aliases.get(city_name.lower(), self.city_aliases.get(city_name, city_name))
        if normalized not in self.city_coords:
            raise RuntimeError(f"未能确认城市：{city_name}")
        lat, lon = self.city_coords[normalized]
        return CityMatch(
            input_name=city_name,
            normalized_query=normalized,
            confirmed_name=normalized,
            region=f"{normalized}市",
            country="中国",
            provider="腾讯位置服务",
            lat=lat,
            lon=lon,
            city_code=f"{normalized}-001",
        )

    def build_city_profile(self, destination_match: CityMatch, tastes: list[str]):
        city = destination_match.confirmed_name
        lat, lon = destination_match.lat, destination_match.lon
        pois = [
            PointOfInterest(
                f"{city}博物馆",
                "博物馆",
                city,
                f"{city} 博物馆候选",
                2.5,
                30.0,
                lat + 0.010,
                lon + 0.010,
                ["culture", "history"],
                "morning",
                estimated_visit_window="09:00-11:30",
                source_evidence=self._evidence(city, f"{city}博物馆"),
            ),
            PointOfInterest(
                f"{city}公园",
                "公园",
                city,
                f"{city} 公园候选",
                2.0,
                0.0,
                lat - 0.010,
                lon + 0.010,
                ["nature", "relaxed"],
                "afternoon",
                estimated_visit_window="13:30-15:30",
                source_evidence=self._evidence(city, f"{city}公园"),
            ),
            PointOfInterest(
                f"{city}老街",
                "老街",
                city,
                f"{city} 老街候选",
                2.0,
                0.0,
                lat + 0.006,
                lon - 0.008,
                ["food", "night"],
                "evening",
                estimated_visit_window="16:30-18:30",
                source_evidence=self._evidence(city, f"{city}老街"),
            ),
            PointOfInterest(
                f"{city}夜游区",
                "夜游",
                city,
                f"{city} 夜游候选",
                2.0,
                48.0,
                lat - 0.004,
                lon - 0.006,
                ["night", "photography"],
                "night",
                estimated_visit_window="19:00-21:00",
                source_evidence=self._evidence(city, f"{city}夜游区"),
            ),
        ]
        foods = [
            FoodVenue(
                f"{city}面馆",
                city,
                "面食",
                "午餐候选",
                42.0,
                ["food", *(tastes[:1] or ["鲜"])],
                taste_profile=tastes[:1] or ["鲜"],
                meal_suitability="lunch",
                lat=lat + 0.003,
                lon=lon + 0.002,
                source_evidence=self._evidence(city, f"{city}面馆"),
            ),
            FoodVenue(
                f"{city}小吃店",
                city,
                "小吃",
                "午餐候选",
                58.0,
                ["food", *(tastes[:1] or ["鲜"])],
                taste_profile=tastes[:1] or ["鲜"],
                meal_suitability="lunch",
                lat=lat - 0.002,
                lon=lon + 0.004,
                source_evidence=self._evidence(city, f"{city}小吃店"),
            ),
            FoodVenue(
                f"{city}本帮菜",
                city,
                "本帮菜",
                "晚餐候选",
                96.0,
                ["food", *(tastes[:2] or ["鲜", "辣"])],
                taste_profile=tastes[:2] or ["鲜", "辣"],
                meal_suitability="dinner",
                lat=lat + 0.004,
                lon=lon - 0.003,
                source_evidence=self._evidence(city, f"{city}本帮菜"),
            ),
            FoodVenue(
                f"{city}夜宵馆",
                city,
                "夜宵",
                "晚餐候选",
                118.0,
                ["food", *(tastes[:2] or ["鲜", "辣"])],
                taste_profile=tastes[:2] or ["鲜", "辣"],
                meal_suitability="dinner",
                lat=lat - 0.003,
                lon=lon - 0.004,
                source_evidence=self._evidence(city, f"{city}夜宵馆"),
            ),
        ]
        hotels = [
            HotelVenue(
                f"{city}地铁酒店",
                "新街口",
                f"{city} 地铁酒店",
                228.0,
                lat + 0.001,
                lon - 0.001,
                tags=["budget"],
                source_evidence=self._evidence(city, f"{city}地铁酒店"),
            ),
            HotelVenue(
                f"{city}城市酒店",
                "玄武湖",
                f"{city} 城市酒店",
                338.0,
                lat + 0.004,
                lon + 0.001,
                tags=["balanced"],
                source_evidence=self._evidence(city, f"{city}城市酒店"),
            ),
        ]
        profile = CityProfile(
            city=city,
            aliases=[city.lower()],
            intro=f"{city} 的行程数据来自腾讯位置服务在线检索结果。",
            local_transport_tip=f"{city} 已按真实在线点位生成城市内路线。",
            daily_local_transport_cost=28.0,
            accommodation_budget={"budget": 220.0, "balanced": 360.0, "premium": 520.0},
            intercity_transport={},
            recommended_seasons=["spring", "autumn"],
            pois=pois,
            foods=foods,
            hotels=hotels,
        )
        return profile, [f"已确认 {city} 并生成真实在线候选数据。"]

    def search_poi_by_name(self, city_name: str, poi_name: str):
        if city_name not in self.city_coords:
            return None
        lat, lon = self.city_coords[city_name]
        keyword = (poi_name or "").strip()
        poi_catalog = {
            "玄武湖": (lat + 0.008, lon + 0.006, ["nature", "photography"], "afternoon"),
            "钟山风景区": (lat + 0.012, lon + 0.018, ["nature", "culture"], "morning"),
        }
        matched_name = None
        for name in poi_catalog:
            if keyword and (keyword in name or name in keyword):
                matched_name = name
                break
        if matched_name is None:
            return None
        poi_lat, poi_lon, tags, best_time = poi_catalog[matched_name]
        return PointOfInterest(
            matched_name,
            "旅游景点",
            city_name,
            f"{city_name} 必达景点补检索：{matched_name}",
            2.5,
            0.0,
            poi_lat,
            poi_lon,
            tags,
            best_time,
            estimated_visit_window="09:30-12:00",
            source_evidence=self._evidence(city_name, matched_name),
        )

    def search_hotels(self, city_name: str, area_hint: str = ""):
        if city_name not in self.city_coords:
            return []
        lat, lon = self.city_coords[city_name]
        hotels = [
            HotelVenue(
                f"{city_name}玄武湖酒店",
                "玄武湖",
                f"{city_name} 玄武湖酒店",
                368.0,
                lat + 0.003,
                lon + 0.004,
                tags=["balanced"],
                source_evidence=self._evidence(city_name, f"{city_name}玄武湖酒店"),
            ),
            HotelVenue(
                f"{city_name}新街口酒店",
                "新街口",
                f"{city_name} 新街口酒店",
                248.0,
                lat + 0.001,
                lon - 0.001,
                tags=["budget"],
                source_evidence=self._evidence(city_name, f"{city_name}新街口酒店"),
            ),
        ]
        area = (area_hint or "").strip().lower()
        if not area:
            return hotels
        return [hotel for hotel in hotels if area in f"{hotel.name} {hotel.district}".lower()]

    def weather(self, city_code: str):
        return {"summary": "晴，21°C", "travel_tip": "适合安排户外城市漫游。"}

    def search_along_route_foods(self, city_name: str, path, tastes: list[str], radius_meters: int = 2000):
        if not path:
            return []
        taste_label = str(tastes[0]).strip() if tastes else "地方风味"
        foods = []
        for index, point in enumerate(path[:6], start=1):
            lat = point[1]
            lon = point[0]
            foods.append(
                FoodVenue(
                    f"{city_name}{taste_label}沿途餐馆{index}",
                    city_name,
                    taste_label,
                    f"沿途补充餐饮候选，半径 {radius_meters} 米",
                    62.0 + index * 7.0,
                    ["food", "沿途候选"],
                    taste_profile=[taste_label],
                    meal_suitability="both",
                    lat=lat,
                    lon=lon,
                    address=f"{city_name}沿途商圈{index}",
                    source_evidence=self._evidence(city_name, f"{city_name}{taste_label}沿途餐馆{index}"),
                )
            )
        return foods

    def search_nearby_foods(
        self,
        city_name: str,
        center_lat: float,
        center_lon: float,
        tastes: list[str],
        radius_meters: int,
        meal_type: str,
    ):
        taste_label = str(tastes[0]).strip() if tastes else "地方风味"
        return [
            FoodVenue(
                f"{city_name}{taste_label}附近餐馆{radius_meters}-{index}",
                city_name,
                taste_label,
                f"附近补充候选，半径 {radius_meters} 米",
                48.0 + index * 9.0,
                ["food", "附近候选"],
                taste_profile=tastes[:2] or [taste_label],
                meal_suitability=meal_type if meal_type in {"lunch", "dinner"} else "both",
                lat=center_lat + 0.0007 * index,
                lon=center_lon + 0.0006 * index,
                address=f"{city_name}附近商圈{radius_meters}-{index}",
                source_evidence=self._evidence(city_name, f"{city_name}{taste_label}附近餐馆{radius_meters}-{index}"),
            )
            for index in range(1, 3)
        ]

    def search_area_foods(self, city_name: str, area_hint: str, tastes: list[str], meal_type: str):
        area = area_hint.strip() or "中心区"
        taste_label = str(tastes[0]).strip() if tastes else "地方风味"
        return [
            FoodVenue(
                f"{city_name}{area}{taste_label}区域餐馆{index}",
                city_name,
                taste_label,
                f"{area} 区域补充候选",
                52.0 + index * 12.0,
                ["food", "区域候选"],
                taste_profile=tastes[:2] or [taste_label],
                meal_suitability=meal_type if meal_type in {"lunch", "dinner"} else "both",
                lat=self.city_coords[city_name][0] + 0.0012 * index,
                lon=self.city_coords[city_name][1] - 0.001 * index,
                address=f"{area}区域餐馆{index}",
                source_evidence=self._evidence(city_name, f"{city_name}{area}{taste_label}区域餐馆{index}"),
            )
            for index in range(1, 3)
        ]

    def search_travel_notes(self, request, profile, llm_provider=None):
        return [
            TravelNote(
                title=f"{profile.city} 攻略主线",
                summary=f"围绕 {profile.city} 的核心文化景点和夜游片区展开。",
                style_tag=request.travel_note_style,
                source_url=profile.pois[0].source_evidence[0].source_url,
                provider="百炼整理（基于腾讯位置服务）",
                evidence_type="大模型整理",
            )
        ]


class FakeTencentMapProvider:
    name = "tencent-map"

    def __init__(self) -> None:
        self.missing_segment_indexes: set[int] = set()

    def is_available(self) -> bool:
        return True

    def route_segments(self, day_nodes):
        from travel_multi_agent_planner.models import TransportSegment

        segments = []
        for index, (current, nxt) in enumerate(zip(day_nodes, day_nodes[1:])):
            kind = "walk" if current["kind"] == "spot" and nxt["kind"] == "spot" else "taxi"
            distance_km = round((((current["lat"] - nxt["lat"]) ** 2 + (current["lon"] - nxt["lon"]) ** 2) ** 0.5) * 111, 2)
            path = [] if index in self.missing_segment_indexes else [
                [current["lon"], current["lat"]],
                [(current["lon"] + nxt["lon"]) / 2 + 0.0015, (current["lat"] + nxt["lat"]) / 2 + 0.001],
                [nxt["lon"], nxt["lat"]],
            ]
            segments.append(
                TransportSegment(
                    segment_type=kind,  # type: ignore[arg-type]
                    from_label=current["label"],
                    to_label=nxt["label"],
                    duration_minutes=18 if kind == "walk" else 24,
                    estimated_cost=0.0 if kind == "walk" else 16.0,
                    description=f"{current['label']} 到 {nxt['label']} 的腾讯路径结果",
                    path=path,
                    distance_km=distance_km,
                    path_status="missing" if index in self.missing_segment_indexes else "ok",
                )
            )
        return segments

    def reorder_spots(self, hotel, spots):
        return list(spots)


class FakeIntercityProvider:
    name = "china-railway-12306"

    def is_available(self) -> bool:
        return True

    def query_options(self, origin_city: str, destination_city: str, travel_date: str, limit: int = 5):
        return [
            IntercityOption(
                mode="rail",
                transport_code=f"G{abs(hash((origin_city, destination_city))) % 9000 + 1000}",
                from_station=f"{origin_city}站",
                to_station=f"{destination_city}站",
                depart_time="08:15",
                arrive_time="10:05",
                duration_minutes=110,
                price_cny=279.0,
                seat_label="二等座",
                queried_at="2026-04-13T15:20:00+08:00",
                source_name="中国铁路12306",
                source_url=f"https://kyfw.12306.cn/otn/leftTicket/init?date={travel_date}",
                travel_date=travel_date,
            )
        ][:limit]


class EarlyDepartureIntercityProvider(FakeIntercityProvider):
    def query_options(self, origin_city: str, destination_city: str, travel_date: str, limit: int = 5):
        return [
            IntercityOption(
                mode="rail",
                transport_code="G1001",
                from_station=f"{origin_city}站",
                to_station=f"{destination_city}站",
                depart_time="06:05",
                arrive_time="07:40",
                duration_minutes=95,
                price_cny=98.0,
                seat_label="二等座",
                queried_at="2026-04-13T15:20:00+08:00",
                source_name="中国铁路12306",
                source_url=f"https://kyfw.12306.cn/otn/leftTicket/init?date={travel_date}",
                travel_date=travel_date,
            ),
            IntercityOption(
                mode="rail",
                transport_code="G2002",
                from_station=f"{origin_city}站",
                to_station=f"{destination_city}站",
                depart_time="09:12",
                arrive_time="10:52",
                duration_minutes=100,
                price_cny=102.0,
                seat_label="二等座",
                queried_at="2026-04-13T15:20:00+08:00",
                source_name="中国铁路12306",
                source_url=f"https://kyfw.12306.cn/otn/leftTicket/init?date={travel_date}",
                travel_date=travel_date,
            ),
        ][:limit]


class NonMatchingSupplementalFoodProvider(FakeTencentSearchProvider):
    def _supplemental_taste(self, tastes: list[str]) -> str:
        joined = " ".join(str(item) for item in tastes)
        return "日料" if "日料" in joined or "日本" in joined or "寿司" in joined else "地方风味"

    def search_along_route_foods(self, city_name: str, path, tastes: list[str], radius_meters: int = 2000):
        taste = self._supplemental_taste(tastes)
        foods = super().search_along_route_foods(city_name, path, [taste], radius_meters)
        for food in foods:
            food.name = food.name.replace(taste, f"{taste}补充")
            food.cuisine = taste
            food.taste_profile = [taste]
        return foods

    def search_nearby_foods(
        self,
        city_name: str,
        center_lat: float,
        center_lon: float,
        tastes: list[str],
        radius_meters: int,
        meal_type: str,
    ):
        taste = self._supplemental_taste(tastes)
        foods = super().search_nearby_foods(city_name, center_lat, center_lon, [taste], radius_meters, meal_type)
        for food in foods:
            food.name = food.name.replace(taste, f"{taste}补充")
            food.cuisine = taste
            food.taste_profile = [taste]
        return foods

    def search_area_foods(self, city_name: str, area_hint: str, tastes: list[str], meal_type: str):
        taste = self._supplemental_taste(tastes)
        foods = super().search_area_foods(city_name, area_hint, [taste], meal_type)
        for food in foods:
            food.name = food.name.replace(taste, f"{taste}补充")
            food.cuisine = taste
            food.taste_profile = [taste]
        return foods


class SnackHeavyFoodProvider(FakeTencentSearchProvider):
    def search_nearby_foods(
        self,
        city_name: str,
        center_lat: float,
        center_lon: float,
        tastes: list[str],
        radius_meters: int,
        meal_type: str,
    ):
        if meal_type != "dinner":
            return super().search_nearby_foods(city_name, center_lat, center_lon, tastes, radius_meters, meal_type)
        return [
            FoodVenue(
                f"{city_name}冰糖葫芦旗舰店",
                city_name,
                "甜品饮品",
                "晚餐附近高曝光零食候选",
                500.0,
                ["food", "附近候选", "甜"],
                taste_profile=["甜"],
                meal_suitability="dinner",
                lat=center_lat,
                lon=center_lon,
                address=f"{city_name}景区门口",
                source_evidence=self._evidence(city_name, f"{city_name}冰糖葫芦旗舰店"),
            ),
            FoodVenue(
                f"{city_name}本地正餐馆",
                city_name,
                "本地菜",
                "适合晚餐的正餐候选",
                92.0,
                ["food", "附近候选", "鲜"],
                taste_profile=["鲜"],
                meal_suitability="dinner",
                lat=center_lat + 0.0005,
                lon=center_lon + 0.0005,
                address=f"{city_name}商圈正餐",
                source_evidence=self._evidence(city_name, f"{city_name}本地正餐馆"),
            ),
        ]


class TravelPlannerTests(unittest.TestCase):
    def build_orchestrator(self, with_keys: bool = True) -> TravelPlanningOrchestrator:
        config = AppConfig(
            dashscope_api_key=None,
            bailian_model="qwen-plus",
            requested_mode="online",
            tencent_map_server_key="fake-server-key" if with_keys else None,
            tencent_map_js_key="fake-js-key" if with_keys else None,
        )
        orchestrator = TravelPlanningOrchestrator(config=config)
        orchestrator.search_provider = FakeTencentSearchProvider()
        orchestrator.map_provider = FakeTencentMapProvider()
        orchestrator.intercity_provider = FakeIntercityProvider()
        orchestrator.transport = TransportAgent(intercity_provider=orchestrator.intercity_provider, llm_provider=orchestrator.llm_provider)
        return orchestrator

    def test_online_plan_contains_city_match_and_trace(self) -> None:
        orchestrator = self.build_orchestrator()
        plan = orchestrator.create_plan(TripRequest(destination="Nanjing", days=3, budget=1500, origin="Shanghai"))
        self.assertEqual(plan.mode, "online")
        self.assertEqual(plan.destination_match.confirmed_name, "南京")
        self.assertEqual(plan.destination_match.country, "中国")
        self.assertGreaterEqual(len(plan.trace), 8)

    def test_plan_contains_source_evidence(self) -> None:
        orchestrator = self.build_orchestrator()
        plan = orchestrator.create_plan(TripRequest(destination="Suzhou", days=2, budget=1000, origin="Shanghai"))
        first_spot = plan.day_plans[0].spots[0]
        self.assertTrue(first_spot.source_evidence)
        self.assertEqual(first_spot.source_evidence[0].provider, "tencent-map")

    def test_intercity_segment_prefers_queried_train_data(self) -> None:
        orchestrator = self.build_orchestrator()
        plan = orchestrator.create_plan(TripRequest(destination="Nanjing", days=3, budget=1500, origin="Shanghai", departure_date="2026-04-14"))
        arrival = plan.day_plans[0].arrival_segment
        self.assertIsNotNone(arrival)
        self.assertTrue(arrival.transport_code.startswith("G"))  # type: ignore[union-attr]
        self.assertEqual(arrival.depart_time, "08:15")  # type: ignore[union-attr]
        self.assertEqual(arrival.arrive_time, "10:05")  # type: ignore[union-attr]
        self.assertEqual(arrival.source_name, "中国铁路12306")  # type: ignore[union-attr]
        self.assertEqual(arrival.confidence, "queried")  # type: ignore[union-attr]

    def test_missing_tencent_keys_fails_fast(self) -> None:
        config = AppConfig(
            dashscope_api_key=None,
            bailian_model="qwen-plus",
            requested_mode="online",
            tencent_map_server_key=None,
            tencent_map_js_key=None,
        )
        orchestrator = TravelPlanningOrchestrator(config=config)
        with self.assertRaises(RuntimeError):
            orchestrator.create_plan(TripRequest(destination="Nanjing", days=2, budget=1200, origin="Shanghai"))

    def test_day_plan_contains_hotel_meals_and_transport_segments(self) -> None:
        orchestrator = self.build_orchestrator()
        plan = orchestrator.create_plan(TripRequest(destination="Hangzhou", days=3, budget=1500, origin="Shanghai"))
        for day in plan.day_plans:
            self.assertIsNotNone(day.hotel)
            self.assertGreaterEqual(len(day.meals), 2)
            self.assertNotEqual(day.meals[0].venue_name, day.meals[1].venue_name)
            self.assertGreater(len(day.transport_segments), 0)
            self.assertTrue(all(segment.segment_type != "intercity" for segment in day.transport_segments))

    def test_day_plan_avoids_cross_day_duplicate_spots(self) -> None:
        orchestrator = self.build_orchestrator()
        plan = orchestrator.create_plan(TripRequest(destination="Nanjing", days=3, budget=1500, origin="Shanghai"))
        chosen_spots = [spot.name for day in plan.day_plans for spot in day.spots]
        self.assertEqual(len(chosen_spots), len(set(chosen_spots)))

    def test_required_spots_from_preferred_area_are_enforced_and_notes_only_schedule_day(self) -> None:
        orchestrator = self.build_orchestrator()
        plan = orchestrator.create_plan(
            TripRequest(
                destination="Nanjing",
                days=3,
                budget=1800,
                origin="Shanghai",
                preferred_areas=["玄武湖", "钟山风景区"],
                additional_notes="第一天必须包含钟山风景区和玄武湖。",
            )
        )
        all_spot_names = [spot.name for day in plan.day_plans for spot in day.spots]
        self.assertTrue(any("玄武湖" in name for name in all_spot_names))
        self.assertTrue(any("钟山风景区" in name for name in all_spot_names))
        day_one = next((day for day in plan.day_plans if day.day == 1), None)
        self.assertIsNotNone(day_one)
        self.assertTrue(any("玄武湖" in spot.name for spot in day_one.spots))  # type: ignore[union-attr]
        self.assertTrue(any("钟山风景区" in spot.name for spot in day_one.spots))  # type: ignore[union-attr]
        self.assertTrue(any("必达景点已命中" in note for note in plan.search_notes))

    def test_hotel_area_preference_is_hard_constraint(self) -> None:
        orchestrator = self.build_orchestrator()
        plan = orchestrator.create_plan(
            TripRequest(
                destination="Nanjing",
                days=2,
                budget=1200,
                origin="Shanghai",
                must_have_hotel_area="玄武湖",
            )
        )
        for day in plan.day_plans:
            self.assertIsNotNone(day.hotel)
            hotel_text = f"{day.hotel.name} {day.hotel.district} {day.hotel.address}".lower()  # type: ignore[union-attr]
            self.assertIn("玄武湖", hotel_text)

    def test_avoid_tags_are_not_selected_in_final_spots(self) -> None:
        orchestrator = self.build_orchestrator()
        plan = orchestrator.create_plan(
            TripRequest(
                destination="Nanjing",
                days=2,
                budget=1200,
                origin="Shanghai",
                avoid_tags=["night"],
            )
        )
        for day in plan.day_plans:
            for spot in day.spots:
                spot_text = f"{spot.name} {spot.category} {spot.description}".lower()
                self.assertNotIn("night", spot.tags)
                self.assertNotIn("night", spot_text)

    def test_animation_bundle_contains_nodes_and_segments(self) -> None:
        orchestrator = self.build_orchestrator()
        plan = orchestrator.create_plan(TripRequest(destination="Nanjing", days=3, budget=1500, origin="Shanghai"))
        bundle = _build_animation_bundle(plan, "demo-case")
        self.assertEqual(bundle.case_id, "demo-case")
        self.assertGreater(len(bundle.nodes), 0)
        self.assertGreater(len(bundle.steps), 0)
        self.assertGreater(bundle.total_frames, 0)

    def test_saved_case_round_trip_loads_latest(self) -> None:
        orchestrator = self.build_orchestrator()
        plan = orchestrator.create_plan(TripRequest(destination="Suzhou", days=2, budget=1000, origin="Shanghai"))
        bundle = _build_animation_bundle(plan, "test-case")
        temp_dir = Path(__file__).resolve().parent / "_tmp_outputs"
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        temp_dir.mkdir(parents=True, exist_ok=True)
        original_outputs_dir = persistence.OUTPUTS_DIR
        original_latest_case = persistence.LATEST_CASE_PATH
        try:
            persistence.OUTPUTS_DIR = temp_dir
            persistence.LATEST_CASE_PATH = persistence.OUTPUTS_DIR / "latest_case.json"
            record = persistence.save_case(plan, bundle, "fake-js-key")
            self.assertTrue((persistence.OUTPUTS_DIR / "test-case" / "plan.json").exists())
            self.assertTrue((persistence.OUTPUTS_DIR / "test-case" / "animation.json").exists())
            self.assertTrue((persistence.OUTPUTS_DIR / "test-case" / "player.html").exists())
            loaded = persistence.load_latest_case()
            self.assertIsNotNone(loaded)
            loaded_plan, loaded_bundle, loaded_record = loaded  # type: ignore[misc]
            self.assertEqual(loaded_record.case_id, record.case_id)
            self.assertEqual(loaded_plan.request.destination, "Suzhou")
            self.assertEqual(loaded_bundle.case_id, "test-case")
            self.assertGreaterEqual(loaded_record.bundle_version, persistence.BUNDLE_VERSION)
            self.assertGreaterEqual(loaded_record.player_version, persistence.PLAYER_VERSION)
            animation_payload = json.loads(Path(record.animation_path).read_text(encoding="utf-8"))
            self.assertEqual(animation_payload["case_id"], "test-case")
        finally:
            persistence.OUTPUTS_DIR = original_outputs_dir
            persistence.LATEST_CASE_PATH = original_latest_case
            if temp_dir.exists():
                shutil.rmtree(temp_dir)

    def test_intercity_selection_avoids_over_early_departures(self) -> None:
        transport = TransportAgent(intercity_provider=EarlyDepartureIntercityProvider())
        choice = transport._resolve_intercity_option("Shanghai", "Nanjing", "2026-04-14")
        self.assertIsNotNone(choice)
        self.assertEqual(choice.transport_code, "G2002")  # type: ignore[union-attr]
        self.assertEqual(choice.depart_time, "09:12")  # type: ignore[union-attr]

    def test_summary_markdown_contains_chinese_preferences_and_personalized_notes(self) -> None:
        orchestrator = self.build_orchestrator()
        plan = orchestrator.create_plan(
            TripRequest(
                destination="Nanjing",
                days=2,
                budget=1500,
                origin="Shanghai",
                interests=["culture", "food", "nature"],
                food_tastes=["鲜", "辣"],
                preferred_areas=["玄武湖"],
                must_have_hotel_area="玄武湖",
                additional_notes="第一天晚饭想吃火锅，第二天晚上想吃烧烤，出发不要太早。",
            )
        )
        self.assertIn("兴趣偏好：文化、美食、自然", plan.summary_markdown)
        self.assertIn("补充要求：第一天晚饭想吃火锅，第二天晚上想吃烧烤，出发不要太早。", plan.summary_markdown)
        self.assertIn("酒店区域偏好：玄武湖", plan.summary_markdown)
        self.assertIn("理由：", plan.summary_markdown)

        self.assertIn("## 个性化餐饮落地", plan.summary_markdown)
        self.assertIn("## 个性化交通落地", plan.summary_markdown)

    def test_transport_personalization_pushes_departure_later_in_final_plan(self) -> None:
        orchestrator = self.build_orchestrator()
        plan = orchestrator.create_plan(
            TripRequest(
                destination="Nanjing",
                days=2,
                budget=1500,
                origin="Shanghai",
                additional_notes="第一天出发不要太早。",
            )
        )
        arrival = plan.day_plans[0].arrival_segment
        self.assertIsNotNone(arrival)
        self.assertEqual(arrival.depart_time, "09:00")  # type: ignore[union-attr]
        self.assertEqual(arrival.arrive_time, "10:50")  # type: ignore[union-attr]
        self.assertEqual(arrival.confidence, "personalized")  # type: ignore[union-attr]
        self.assertIn("已避开过早车次", arrival.description)  # type: ignore[union-attr]
        self.assertTrue(any("个性化" in note for note in plan.day_plans[0].notes))

    def test_personalized_meals_use_real_candidates_with_evidence(self) -> None:
        orchestrator = self.build_orchestrator()
        plan = orchestrator.create_plan(
            TripRequest(
                destination="Nanjing",
                days=2,
                budget=1500,
                origin="Shanghai",
                interests=["culture", "food", "nature"],
                additional_notes="第一天晚上想吃火锅，第二天晚上想吃烧烤。",
            )
        )
        dinner_meals = [
            meal
            for day in plan.day_plans
            for meal in day.meals
            if meal.meal_type == "dinner"
        ]
        self.assertEqual(len(dinner_meals), 2)
        for meal in dinner_meals:
            self.assertNotEqual(meal.selection_tier, "personalized-placeholder")
            self.assertTrue(meal.source_evidence)
        self.assertEqual(len({meal.venue_name for meal in dinner_meals}), len(dinner_meals))

        meal_evidence_issues = [
            issue for issue in plan.validation_issues if issue.category == "meal-evidence"
        ]
        self.assertFalse(meal_evidence_issues)

    def test_generic_multi_day_meal_preferences_are_applied(self) -> None:
        orchestrator = self.build_orchestrator()
        plan = orchestrator.create_plan(
            TripRequest(
                destination="Nanjing",
                days=3,
                budget=1800,
                origin="Shanghai",
                interests=["culture", "food", "nature"],
                additional_notes="第一天想吃海鲜，第二天想吃日料，第三天中午想吃面。",
            )
        )
        day1_dinner = next(meal for meal in plan.day_plans[0].meals if meal.meal_type == "dinner")
        day2_dinner = next(meal for meal in plan.day_plans[1].meals if meal.meal_type == "dinner")
        day3_lunch = next(meal for meal in plan.day_plans[2].meals if meal.meal_type == "lunch")
        self.assertEqual(day1_dinner.selection_tier, "personalized")
        self.assertEqual(day2_dinner.selection_tier, "personalized")
        self.assertEqual(day3_lunch.selection_tier, "personalized")
        self.assertTrue("海鲜" in day1_dinner.venue_name or "海鲜" in day1_dinner.cuisine)
        self.assertTrue("日料" in day2_dinner.venue_name or "日料" in day2_dinner.cuisine)
        self.assertTrue("面" in day3_lunch.venue_name or "面" in day3_lunch.cuisine)

    def test_unmatched_personalized_meal_keeps_real_meal_and_lists_supplemental_candidates(self) -> None:
        orchestrator = self.build_orchestrator()
        orchestrator.search_provider = NonMatchingSupplementalFoodProvider()
        plan = orchestrator.create_plan(
            TripRequest(
                destination="Nanjing",
                days=2,
                budget=1800,
                origin="Shanghai",
                interests=["culture", "food", "nature"],
                additional_notes="第二天晚上想吃日料。",
            )
        )
        day2_dinner = next(meal for meal in plan.day_plans[1].meals if meal.meal_type == "dinner")
        self.assertNotEqual(day2_dinner.selection_tier, "personalized-placeholder")
        self.assertTrue(day2_dinner.source_evidence)
        day2_notes = "\n".join(plan.day_plans[1].notes)
        self.assertIn("原真实餐饮候选", day2_notes)
        self.assertIn("腾讯API附近个性化候选", day2_notes)
        self.assertIn("日料", day2_notes)
        self.assertIn("## 个性化餐饮落地", plan.summary_markdown)
        self.assertIn("腾讯API附近个性化候选", plan.summary_markdown)
        self.assertIn("日料", plan.summary_markdown)

    def test_dinner_does_not_use_snack_or_dessert_candidates(self) -> None:
        orchestrator = self.build_orchestrator()
        orchestrator.search_provider = SnackHeavyFoodProvider()
        plan = orchestrator.create_plan(
            TripRequest(
                destination="Nanjing",
                days=2,
                budget=1800,
                origin="Shanghai",
                interests=["food", "night"],
                food_tastes=["甜"],
            )
        )
        dinners = [meal for day in plan.day_plans for meal in day.meals if meal.meal_type == "dinner"]
        self.assertTrue(dinners)
        for meal in dinners:
            self.assertNotIn("冰糖葫芦", meal.venue_name)
            self.assertNotIn("糖葫芦", meal.venue_name)
            self.assertNotEqual(meal.cuisine, "甜品饮品")
            self.assertGreaterEqual(meal.estimated_cost, 68.0)


if __name__ == "__main__":
    unittest.main()
