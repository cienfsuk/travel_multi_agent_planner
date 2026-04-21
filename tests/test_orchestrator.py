import unittest
import shutil
import json
from pathlib import Path

from travel_multi_agent_planner import persistence
from travel_multi_agent_planner.app import _build_animation_bundle, _build_day_timeline, _build_map_view_model, _build_scheduled_day_timeline
from travel_multi_agent_planner.agents.planner import PlannerAgent
from travel_multi_agent_planner.agents.transport import TransportAgent
from travel_multi_agent_planner.config import AppConfig
from travel_multi_agent_planner.models import CityMatch, EvidenceItem, FoodVenue, HotelVenue, IntercityOption, PointOfInterest, TravelConstraints, TravelNote, TripRequest
from travel_multi_agent_planner.orchestrator import TravelPlanningOrchestrator
from travel_multi_agent_planner.providers.map_provider import TencentMapProvider


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
        evidence = lambda title: [
            EvidenceItem(
                title=title,
                source_url=f"https://example.com/{city}/{title}",
                snippet=f"{city} 的真实在线候选：{title}",
                provider="tencent-map",
                provider_label="腾讯位置服务",
                evidence_type="网页检索",
                retrieved_at="2026-04-12T00:00:00Z",
            )
        ]
        pois = [
            PointOfInterest(f"{city}博物馆", "博物馆", city, f"{city} 博物馆候选", 2.5, 30.0, lat + 0.01, lon + 0.01, ["culture", "history"], "morning", estimated_visit_window="09:00-11:30", source_evidence=evidence(f"{city}博物馆")),
            PointOfInterest(f"{city}公园", "公园", city, f"{city} 公园候选", 2.0, 0.0, lat - 0.01, lon + 0.01, ["nature", "relaxed"], "afternoon", estimated_visit_window="13:30-15:30", source_evidence=evidence(f"{city}公园")),
            PointOfInterest(f"{city}老街", "老街", city, f"{city} 老街候选", 2.0, 0.0, lat + 0.006, lon - 0.008, ["food", "night"], "evening", estimated_visit_window="16:30-18:30", source_evidence=evidence(f"{city}老街")),
            PointOfInterest(f"{city}夜游区", "夜游", city, f"{city} 夜游候选", 2.0, 48.0, lat - 0.004, lon - 0.006, ["night", "photography"], "night", estimated_visit_window="19:00-21:00", source_evidence=evidence(f"{city}夜游区")),
        ]
        foods = [
            FoodVenue(f"{city}面馆", city, "面食", "午餐候选", 42.0, ["food", *(tastes[:1] or ["鲜"])], taste_profile=tastes[:1] or ["鲜"], meal_suitability="lunch", lat=lat + 0.003, lon=lon + 0.002, source_evidence=evidence(f"{city}面馆")),
            FoodVenue(f"{city}小吃店", city, "小吃", "午餐候选", 58.0, ["food", *(tastes[:1] or ["鲜"])], taste_profile=tastes[:1] or ["鲜"], meal_suitability="lunch", lat=lat - 0.002, lon=lon + 0.004, source_evidence=evidence(f"{city}小吃店")),
            FoodVenue(f"{city}本帮菜", city, "本帮菜", "晚餐候选", 96.0, ["food", *(tastes[:2] or ["鲜", "辣"])], taste_profile=tastes[:2] or ["鲜", "辣"], meal_suitability="dinner", lat=lat + 0.004, lon=lon - 0.003, source_evidence=evidence(f"{city}本帮菜")),
            FoodVenue(f"{city}夜宵馆", city, "夜宵", "晚餐候选", 118.0, ["food", *(tastes[:2] or ["鲜", "辣"])], taste_profile=tastes[:2] or ["鲜", "辣"], meal_suitability="dinner", lat=lat - 0.003, lon=lon - 0.004, source_evidence=evidence(f"{city}夜宵馆")),
        ]
        hotels = [
            HotelVenue(f"{city}地铁酒店", city, f"{city} 地铁酒店", 228.0, lat + 0.001, lon - 0.001, tags=["budget"], source_evidence=evidence(f"{city}地铁酒店")),
            HotelVenue(f"{city}城市酒店", city, f"{city} 城市酒店", 338.0, lat + 0.004, lon + 0.001, tags=["balanced"], source_evidence=evidence(f"{city}城市酒店")),
        ]
        from travel_multi_agent_planner.models import CityProfile

        profile = CityProfile(
            city=city,
            aliases=[city.lower()],
            intro=f"{city} 的行程数据来自腾讯位置服务真实检索结果。",
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

    def weather(self, city_code: str):
        return {"summary": "晴，21°C", "travel_tip": "适合安排户外城市漫游。"}

    def search_along_route_foods(self, city_name: str, path, tastes: list[str], radius_meters: int = 2000):
        if not path:
            return []
        foods = []
        for index, point in enumerate(path[:6], start=1):
            lat = point[1]
            lon = point[0]
            foods.append(
                FoodVenue(
                    f"{city_name}沿途餐馆{index}",
                    city_name,
                    "地方风味",
                    f"沿途补充餐饮候选，半径 {radius_meters} 米",
                    62.0 + index * 7.0,
                    ["food", "沿途候选"],
                    taste_profile=tastes[:1] or ["鲜"],
                    meal_suitability="both",
                    lat=lat,
                    lon=lon,
                    address=f"{city_name}沿途商圈{index}",
                    source_evidence=[
                        EvidenceItem(
                            title=f"{city_name}沿途餐馆{index}",
                            source_url=f"https://example.com/{city_name}/route-food-{index}",
                            snippet=f"{city_name}沿途商圈{index}",
                            provider="tencent-map",
                            provider_label="腾讯位置服务",
                            evidence_type="网页检索",
                            retrieved_at="2026-04-12T00:00:00Z",
                        )
                    ],
                )
            )
        return foods

    def search_nearby_foods(self, city_name: str, center_lat: float, center_lon: float, tastes: list[str], radius_meters: int, meal_type: str):
        suffix = "午" if meal_type == "lunch" else "晚"
        return [
            FoodVenue(
                f"{city_name}{suffix}近邻馆{radius_meters}-{index}",
                city_name,
                "地方风味",
                f"近邻补充候选，半径 {radius_meters} 米",
                48.0 + index * 9.0,
                ["food", "近邻候选"],
                taste_profile=tastes[:2] or ["鲜", "辣"],
                meal_suitability=meal_type,
                lat=center_lat + 0.0007 * index,
                lon=center_lon + 0.0006 * index,
                address=f"{city_name}近邻商圈{radius_meters}-{index}",
                source_evidence=[
                    EvidenceItem(
                        title=f"{city_name}{suffix}近邻馆{radius_meters}-{index}",
                        source_url=f"https://example.com/{city_name}/nearby-food-{meal_type}-{radius_meters}-{index}",
                        snippet=f"{city_name}近邻商圈{radius_meters}-{index}",
                        provider="tencent-map",
                        provider_label="腾讯位置服务",
                        evidence_type="网页检索",
                        retrieved_at="2026-04-12T00:00:00Z",
                    )
                ],
            )
            for index in range(1, 3)
        ]

    def search_area_foods(self, city_name: str, area_hint: str, tastes: list[str], meal_type: str):
        area_label = area_hint.replace(city_name, "").replace("市", "").strip() or "中心区"
        suffix = "午" if meal_type == "lunch" else "晚"
        return [
            FoodVenue(
                f"{city_name}{area_label}{suffix}区域馆{index}",
                city_name,
                "地方风味",
                f"{area_hint} 区域补充候选",
                52.0 + index * 12.0,
                ["food", "区域候选"],
                taste_profile=tastes[:2] or ["鲜", "辣"],
                meal_suitability=meal_type,
                lat=self.city_coords[city_name][0] + 0.0012 * index,
                lon=self.city_coords[city_name][1] - 0.001 * index,
                address=f"{area_hint}区域馆{index}",
                source_evidence=[
                    EvidenceItem(
                        title=f"{city_name}{area_label}{suffix}区域馆{index}",
                        source_url=f"https://example.com/{city_name}/area-food-{meal_type}-{index}",
                        snippet=f"{area_hint}区域馆{index}",
                        provider="tencent-map",
                        provider_label="腾讯位置服务",
                        evidence_type="网页检索",
                        retrieved_at="2026-04-12T00:00:00Z",
                    )
                ],
            )
            for index in range(1, 3)
        ]

    def search_travel_notes(self, request, profile, llm_provider=None):
        return [
            TravelNote(
                title=f"{profile.city} 攻略主线",
                summary=f"建议围绕 {profile.city} 的核心文化景点和夜游片区展开。",
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

    def test_online_plan_contains_real_city_match(self) -> None:
        orchestrator = self.build_orchestrator()
        plan = orchestrator.create_plan(TripRequest(destination="Nanjing", days=3, budget=1500, origin="Shanghai"))
        self.assertEqual(plan.mode, "online")
        self.assertEqual(plan.destination_match.confirmed_name, "南京")
        self.assertEqual(plan.destination_match.country, "中国")
        self.assertGreaterEqual(len(plan.trace), 8)

    def test_plan_contains_real_source_evidence(self) -> None:
        orchestrator = self.build_orchestrator()
        plan = orchestrator.create_plan(TripRequest(destination="Suzhou", days=2, budget=1000, origin="Shanghai"))
        first_spot = plan.day_plans[0].spots[0]
        self.assertTrue(first_spot.source_evidence)
        self.assertEqual(first_spot.source_evidence[0].provider, "tencent-map")
        self.assertNotIn("系统合成", plan.evidence_mode_summary)

    def test_intercity_segment_prefers_queried_train_data(self) -> None:
        orchestrator = self.build_orchestrator()
        plan = orchestrator.create_plan(TripRequest(destination="Nanjing", days=3, budget=1500, origin="Shanghai", departure_date="2026-04-14"))
        arrival = plan.day_plans[0].arrival_segment
        self.assertIsNotNone(arrival)
        self.assertEqual(arrival.transport_code[:1], "G")
        self.assertEqual(arrival.depart_time, "08:15")
        self.assertEqual(arrival.arrive_time, "10:05")
        self.assertEqual(arrival.source_name, "中国铁路12306")
        self.assertEqual(arrival.confidence, "queried")

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

    def test_meal_prices_show_layering_between_days_and_meal_types(self) -> None:
        orchestrator = self.build_orchestrator()
        plan = orchestrator.create_plan(TripRequest(destination="Nanjing", days=3, budget=1500, origin="Shanghai"))
        lunch_prices = []
        dinner_prices = []
        for day in plan.day_plans:
            lunch = next(meal for meal in day.meals if meal.meal_type == "lunch")
            dinner = next(meal for meal in day.meals if meal.meal_type == "dinner")
            lunch_prices.append(lunch.estimated_cost)
            dinner_prices.append(dinner.estimated_cost)
            self.assertLess(lunch.estimated_cost, dinner.estimated_cost)
            self.assertLessEqual(lunch.route_distance_km, 1.0)
            self.assertLessEqual(dinner.route_distance_km, 1.5)
            self.assertTrue(lunch.selection_tier)
            self.assertTrue(dinner.selection_tier)
        self.assertGreater(len(set(round(price, 0) for price in lunch_prices + dinner_prices)), 3)
        chosen_names = [meal.venue_name for day in plan.day_plans for meal in day.meals]
        self.assertEqual(len(chosen_names), len(set(chosen_names)))
        self.assertTrue(any(line.category == "城际交通" for line in plan.budget_summary.lines))
        self.assertTrue(any("候选池去重后" in note for day in plan.day_plans for note in day.notes))

    def test_day_plan_avoids_cross_day_duplicate_spots(self) -> None:
        orchestrator = self.build_orchestrator()
        plan = orchestrator.create_plan(TripRequest(destination="Nanjing", days=3, budget=1500, origin="Shanghai"))
        chosen_spots = [spot.name for day in plan.day_plans for spot in day.spots]
        self.assertEqual(len(chosen_spots), len(set(chosen_spots)))

    def test_travel_notes_and_evidence_types_exist(self) -> None:
        orchestrator = self.build_orchestrator()
        plan = orchestrator.create_plan(TripRequest(destination="Suzhou", days=2, budget=1000, origin="Shanghai"))
        self.assertGreaterEqual(len(plan.travel_notes), 1)
        first_note = plan.travel_notes[0]
        self.assertTrue(first_note.evidence_type)
        self.assertIn(first_note.evidence_type, {"攻略聚合", "大模型整理"})

    def test_no_degrade_strings_in_summary_or_notes(self) -> None:
        orchestrator = self.build_orchestrator()
        plan = orchestrator.create_plan(TripRequest(destination="Nanjing", days=2, budget=1200, origin="Shanghai"))
        self.assertNotIn("系统合成", plan.summary_markdown)
        self.assertFalse(any("降级" in note for note in plan.search_notes))

    def test_day_timeline_places_lunch_before_dinner(self) -> None:
        orchestrator = self.build_orchestrator()
        plan = orchestrator.create_plan(TripRequest(destination="Hangzhou", days=2, budget=1200, origin="Shanghai"))
        timeline = _build_day_timeline(plan.day_plans[0])
        slots = [node["slot"] for node in timeline]
        self.assertEqual(slots[0], "酒店")
        self.assertIn("午餐", slots)
        self.assertIn("晚餐", slots)
        self.assertLess(slots.index("午餐"), slots.index("晚餐"))

    def test_map_view_model_supports_all_and_single_day(self) -> None:
        orchestrator = self.build_orchestrator()
        plan = orchestrator.create_plan(TripRequest(destination="Nanjing", days=3, budget=1500, origin="Shanghai"))
        model_all = _build_map_view_model(plan, "全部")
        model_day_1 = _build_map_view_model(plan, "第 1 天")
        self.assertGreater(model_all.total_frames, model_day_1.total_frames)
        self.assertTrue(all(node.day == 1 for node in model_day_1.nodes))
        self.assertGreaterEqual(len(model_day_1.segments), 1)
        self.assertEqual(model_day_1.nodes[0].kind, "hotel")

    def test_animation_bundle_contains_steps_and_segments(self) -> None:
        orchestrator = self.build_orchestrator()
        plan = orchestrator.create_plan(TripRequest(destination="Nanjing", days=3, budget=1500, origin="Shanghai"))
        bundle = _build_animation_bundle(plan, "demo-case")
        self.assertEqual(bundle.case_id, "demo-case")
        self.assertGreater(len(bundle.nodes), 0)
        self.assertGreater(len(bundle.steps), 0)
        self.assertGreater(bundle.total_frames, 0)
        self.assertIn("origin", bundle.request_summary)
        node_map = {(node.day, node.step_index): node for node in bundle.nodes}
        for segment in bundle.segments:
            from_node = node_map[(segment.day, segment.step_index)]
            self.assertEqual(segment.path[0], [from_node.lon, from_node.lat])

    def test_animation_bundle_keeps_missing_segment_path_empty(self) -> None:
        orchestrator = self.build_orchestrator()
        orchestrator.map_provider.missing_segment_indexes = {0}
        plan = orchestrator.create_plan(TripRequest(destination="Nanjing", days=2, budget=1200, origin="Shanghai"))
        bundle = _build_animation_bundle(plan, "missing-path-case")
        missing_segments = [segment for segment in bundle.segments if segment.path_status == "missing"]
        self.assertTrue(missing_segments)
        self.assertTrue(all(segment.path == [] for segment in missing_segments))

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
            listed = persistence.list_saved_cases()
            self.assertEqual(len(listed), 1)
            self.assertEqual(listed[0].case_id, "test-case")
            loaded = persistence.load_latest_case()
            self.assertIsNotNone(loaded)
            loaded_plan, loaded_bundle, loaded_record = loaded  # type: ignore[misc]
            self.assertEqual(loaded_record.case_id, record.case_id)
            self.assertEqual(loaded_plan.request.destination, "Suzhou")
            self.assertEqual(loaded_bundle.case_id, "test-case")
            self.assertTrue("腾讯 JS 地图播放器" in persistence.load_player_html(loaded_record))
            self.assertGreaterEqual(loaded_record.bundle_version, persistence.BUNDLE_VERSION)
            self.assertGreaterEqual(loaded_record.player_version, persistence.PLAYER_VERSION)
        finally:
            persistence.OUTPUTS_DIR = original_outputs_dir
            persistence.LATEST_CASE_PATH = original_latest_case
            if temp_dir.exists():
                shutil.rmtree(temp_dir)

    def test_outdated_saved_case_requires_rebuild(self) -> None:
        orchestrator = self.build_orchestrator()
        plan = orchestrator.create_plan(TripRequest(destination="Nanjing", days=2, budget=1200, origin="Shanghai"))
        bundle = _build_animation_bundle(plan, "old-case")
        temp_dir = Path(__file__).resolve().parent / "_tmp_outputs_old"
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        temp_dir.mkdir(parents=True, exist_ok=True)
        original_outputs_dir = persistence.OUTPUTS_DIR
        original_latest_case = persistence.LATEST_CASE_PATH
        try:
            persistence.OUTPUTS_DIR = temp_dir
            persistence.LATEST_CASE_PATH = persistence.OUTPUTS_DIR / "latest_case.json"
            record = persistence.save_case(plan, bundle, "fake-js-key")
            animation_path = Path(record.animation_path)
            animation_payload = json.loads(animation_path.read_text(encoding="utf-8"))
            animation_payload["bundle_version"] = 1
            animation_path.write_text(json.dumps(animation_payload, ensure_ascii=False, indent=2), encoding="utf-8")
            player_path = Path(record.player_path)
            player_path.write_text(player_path.read_text(encoding="utf-8").replace(f'data-player-version="{persistence.PLAYER_VERSION}"', 'data-player-version="1"'), encoding="utf-8")
            loaded_plan, loaded_bundle, outdated = persistence.load_case_record(record)
            self.assertEqual(loaded_plan.request.destination, "Nanjing")
            self.assertTrue(persistence.case_requires_rebuild(outdated, loaded_bundle))
        finally:
            persistence.OUTPUTS_DIR = original_outputs_dir
            persistence.LATEST_CASE_PATH = original_latest_case
            if temp_dir.exists():
                shutil.rmtree(temp_dir)

    def test_scheduled_timeline_contains_explicit_times(self) -> None:
        orchestrator = self.build_orchestrator()
        plan = orchestrator.create_plan(TripRequest(destination="Hangzhou", days=2, budget=1200, origin="Shanghai"))
        timeline = _build_scheduled_day_timeline(plan.day_plans[0])
        self.assertGreater(len(timeline), 0)
        for item in timeline:
            self.assertIn("start_time", item)
            self.assertIn("end_time", item)
            self.assertGreater(item["duration_minutes"], 0)

    def test_first_day_schedule_respects_arrival_and_meal_windows(self) -> None:
        orchestrator = self.build_orchestrator()
        plan = orchestrator.create_plan(TripRequest(destination="Hangzhou", days=2, budget=1200, origin="Shanghai", departure_date="2026-04-14"))
        timeline = _build_scheduled_day_timeline(plan.day_plans[0])
        lunch_row = next(item for item in timeline if item["kind"] == "lunch")
        dinner_row = next(item for item in timeline if item["kind"] == "dinner")
        first_spot = next(item for item in timeline if item["kind"] == "spot")
        self.assertGreaterEqual(first_spot["start_time"], "11:20")
        self.assertGreaterEqual(lunch_row["start_time"], "11:30")
        self.assertLessEqual(lunch_row["start_time"], "13:30")
        self.assertGreaterEqual(dinner_row["start_time"], "17:00")
        self.assertLessEqual(dinner_row["start_time"], "19:00")

    def test_map_provider_uses_realistic_duration_floor(self) -> None:
        provider = TencentMapProvider("fake-server-key")
        provider._request_best_route = lambda current, nxt, preferred_mode: (  # type: ignore[method-assign]
            [[current["lon"], current["lat"]], [nxt["lon"], nxt["lat"]]],
            1,
            8.6,
            "metro",
            "ok",
        )
        segment = provider.route_segments(
            [
                {"label": "酒店", "lat": 39.9, "lon": 116.3, "kind": "hotel"},
                {"label": "博物馆", "lat": 39.91, "lon": 116.38, "kind": "spot"},
            ]
        )[0]
        self.assertGreaterEqual(segment.duration_minutes, 6)
        self.assertIn("腾讯路径返回约", segment.description)

    def test_balanced_planner_fills_beyond_two_spots_when_candidates_suffice(self) -> None:
        planner = PlannerAgent()
        pois = [
            PointOfInterest(
                name=f"景点{i}",
                category="博物馆",
                district="核心区" if i <= 5 else "老城区",
                description="测试景点",
                duration_hours=2.0,
                ticket_cost=20.0,
                lat=32.0 + i * 0.001,
                lon=118.7 + i * 0.001,
                tags=["culture"],
                best_time="morning",
            )
            for i in range(1, 9)
        ]
        plan = planner.create_daily_spot_plan(
            TripRequest(destination="Nanjing", days=3, budget=1500, origin="Shanghai", style="balanced"),
            pois,
            TravelConstraints(max_daily_spots=3, max_daily_transport_transfers=3, max_total_budget=1500, must_have_tags=["culture"]),
            llm_provider=None,
        )[0]
        self.assertGreaterEqual(max(len(day["spots"]) for day in plan), 3)

    def test_planner_clusters_nearby_spots_into_same_day(self) -> None:
        planner = PlannerAgent()
        pois = [
            PointOfInterest(name="A1", category="博物馆", district="核心区", description="A1", duration_hours=2.0, ticket_cost=20.0, lat=32.001, lon=118.701, tags=["culture"], best_time="morning"),
            PointOfInterest(name="A2", category="博物馆", district="核心区", description="A2", duration_hours=2.0, ticket_cost=20.0, lat=32.003, lon=118.703, tags=["culture"], best_time="morning"),
            PointOfInterest(name="A3", category="公园", district="核心区", description="A3", duration_hours=2.0, ticket_cost=0.0, lat=32.005, lon=118.705, tags=["nature"], best_time="afternoon"),
            PointOfInterest(name="B1", category="博物馆", district="东郊", description="B1", duration_hours=2.0, ticket_cost=20.0, lat=32.081, lon=118.781, tags=["culture"], best_time="morning"),
            PointOfInterest(name="B2", category="博物馆", district="东郊", description="B2", duration_hours=2.0, ticket_cost=20.0, lat=32.083, lon=118.783, tags=["culture"], best_time="morning"),
            PointOfInterest(name="B3", category="公园", district="东郊", description="B3", duration_hours=2.0, ticket_cost=0.0, lat=32.085, lon=118.785, tags=["nature"], best_time="afternoon"),
        ]
        daily_plan, _ = planner.create_daily_spot_plan(
            TripRequest(destination="Nanjing", days=2, budget=1200, origin="Shanghai", style="balanced"),
            pois,
            TravelConstraints(max_daily_spots=3, max_daily_transport_transfers=3, max_total_budget=1200, must_have_tags=["culture", "nature"]),
            llm_provider=None,
        )
        self.assertEqual(len(daily_plan), 2)
        for day in daily_plan:
            lat_span = max(spot.lat for spot in day["spots"]) - min(spot.lat for spot in day["spots"])
            lon_span = max(spot.lon for spot in day["spots"]) - min(spot.lon for spot in day["spots"])
            self.assertLess(lat_span, 0.02)
            self.assertLess(lon_span, 0.02)

    def test_player_template_contains_current_day_panel_controls(self) -> None:
        orchestrator = self.build_orchestrator()
        plan = orchestrator.create_plan(TripRequest(destination="Hangzhou", days=2, budget=1200, origin="Shanghai"))
        bundle = _build_animation_bundle(plan, "player-case")
        html = persistence.render_player_html(bundle, "fake-js-key")
        self.assertIn("当前日步骤面板", html)
        self.assertIn("总进度", html)
        self.assertIn("动态跟随", html)
        self.assertIn(".steps{overflow:auto;min-height:0", html)
        self.assertIn("该段真实路径缺失，未绘制替代直线", html)


if __name__ == "__main__":
    unittest.main()
