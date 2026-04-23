from __future__ import annotations

import re

from ..models import BudgetSummary, CityMatch, DayPlan, TravelConstraints, TripRequest, ValidationIssue


class ConstraintValidatorAgent:
    def validate(
        self,
        request: TripRequest,
        constraints: TravelConstraints,
        day_plans: list[DayPlan],
        budget_summary: BudgetSummary,
        destination_match: CityMatch,
    ) -> tuple[list[ValidationIssue], float]:
        issues: list[ValidationIssue] = []

        if destination_match.country != "中国":
            issues.append(
                ValidationIssue(
                    severity="high",
                    category="city-mismatch",
                    message=f"目标城市确认失败，当前在线结果为 {destination_match.confirmed_name}。",
                    day=None,
                    suggested_fix="重新确认中文城市输入，确保命中中国城市。",
                )
            )

        if not budget_summary.is_within_budget:
            issues.append(
                ValidationIssue(
                    severity="high",
                    category="budget",
                    message=f"当前方案超出预算 {abs(budget_summary.remaining_budget):.0f} 元。",
                    day=None,
                    suggested_fix="减少付费景点、住宿或餐饮档位，优先保留核心景点。",
                )
            )

        if not any(line.category == "城际交通" for line in budget_summary.lines):
            issues.append(
                ValidationIssue(
                    severity="high",
                    category="intercity-budget-missing",
                    message="预算中缺少出发地与目的地往返交通费用。",
                    day=None,
                    suggested_fix="在预算汇总中加入城际往返交通，并与来往交通区保持一致。",
                )
            )

        all_spots = [spot for day in day_plans for spot in day.spots]
        required_names = self._required_names(constraints)
        for required_name in required_names:
            if not any(self._is_required_match(spot.name, required_name) for spot in all_spots):
                issues.append(
                    ValidationIssue(
                        severity="high",
                        category="required-poi-missing",
                        message=f"必达景点未命中：{required_name}。",
                        day=None,
                        suggested_fix="补充该景点检索并强制写入行程。",
                    )
                )

        for day, required_spots in constraints.must_include_spots_by_day.items():
            current_day = next((item for item in day_plans if item.day == day), None)
            if current_day is None:
                issues.append(
                    ValidationIssue(
                        severity="high",
                        category="required-poi-day-missing",
                        message=f"第 {day} 天未生成行程，无法满足按天必达景点要求。",
                        day=day,
                        suggested_fix="补齐该天行程并优先安排必达景点。",
                    )
                )
                continue
            for required_name in required_spots:
                if not any(self._is_required_match(spot.name, required_name) for spot in current_day.spots):
                    issues.append(
                        ValidationIssue(
                            severity="high",
                            category="required-poi-day-missing",
                            message=f"第 {day} 天未命中必达景点：{required_name}。",
                            day=day,
                            suggested_fix="将该景点固定安排到指定日期。",
                        )
                    )

        if constraints.preferred_areas:
            area_texts = [
                f"{spot.name} {spot.district} {spot.address} {spot.description}".lower()
                for spot in all_spots
            ]
            area_texts.extend(
                f"{day.hotel.name} {day.hotel.district} {day.hotel.address}".lower()
                for day in day_plans
                if day.hotel is not None
            )
            for area in constraints.preferred_areas:
                area_text = area.strip().lower()
                if not area_text:
                    continue
                if not any(area_text in candidate for candidate in area_texts):
                    issues.append(
                        ValidationIssue(
                            severity="high",
                            category="preferred-area-missing",
                            message=f"偏好区域/景点未命中：{area}。",
                            day=None,
                            suggested_fix="补充该区域相关景点或酒店并重新规划路线。",
                        )
                    )

        avoid_tags = [tag.strip().lower() for tag in constraints.avoid_tags if tag.strip()]
        seen_restaurants: set[str] = set()
        seen_pois: set[str] = set()
        meal_prices: list[float] = []
        for day in day_plans:
            if len(day.spots) > constraints.max_daily_spots:
                issues.append(
                    ValidationIssue(
                        severity="medium",
                        category="density",
                        message=f"第 {day.day} 天景点数量过多，当前 {len(day.spots)} 个。",
                        day=day.day,
                        suggested_fix="压缩同日景点数量，优先保留评分更高的点位。",
                    )
                )

            districts = {spot.district for spot in day.spots}
            if len(districts) > max(2, constraints.max_daily_transport_transfers):
                issues.append(
                    ValidationIssue(
                        severity="medium",
                        category="area-jump",
                        message=f"第 {day.day} 天涉及区域过多：{'、'.join(sorted(districts))}。",
                        day=day.day,
                        suggested_fix="减少跨区折返，将同区域景点归并到同一天。",
                    )
                )

            if day.hotel is None:
                issues.append(
                    ValidationIssue(
                        severity="high",
                        category="hotel-missing",
                        message=f"第 {day.day} 天缺少酒店安排。",
                        day=day.day,
                        suggested_fix="为当日补充酒店候选，并优先靠近景点聚集区。",
                    )
                )
            else:
                if not day.hotel.source_evidence:
                    issues.append(
                        ValidationIssue(
                            severity="high",
                            category="hotel-evidence",
                            message=f"第 {day.day} 天酒店缺少真实来源证据。",
                            day=day.day,
                            suggested_fix="重新检索真实酒店候选。",
                        )
                    )
                if request.must_have_hotel_area and not self._hotel_matches_area(day.hotel, request.must_have_hotel_area):
                    issues.append(
                        ValidationIssue(
                            severity="high",
                            category="hotel-area-mismatch",
                            message=f"第 {day.day} 天酒店未命中偏好区域：{request.must_have_hotel_area}。",
                            day=day.day,
                            suggested_fix="更换到指定区域内酒店，保持酒店区域硬约束。",
                        )
                    )

            if len(day.meals) < 2:
                issues.append(
                    ValidationIssue(
                        severity="high",
                        category="meal-missing",
                        message=f"第 {day.day} 天未完整安排午餐和晚餐。",
                        day=day.day,
                        suggested_fix="补充午餐与晚餐，避免空白餐次。",
                    )
                )
            else:
                lunch, dinner = day.meals[0], day.meals[1]
                if lunch.venue_name == dinner.venue_name:
                    issues.append(
                        ValidationIssue(
                            severity="medium",
                            category="meal-repeat",
                            message=f"第 {day.day} 天午餐和晚餐使用了同一家餐厅。",
                            day=day.day,
                            suggested_fix="替换其中一餐为不同类型的本地馆子。",
                        )
                    )
                for meal in day.meals:
                    meal_prices.append(meal.estimated_cost)
                    meal_key = self._meal_key(meal)
                    if meal_key in seen_restaurants:
                        issues.append(
                            ValidationIssue(
                                severity="medium",
                                category="cross-day-repeat",
                                message=f"{meal.venue_name} 在多天行程中重复出现。",
                                day=day.day,
                                suggested_fix="扩展候选池并优先替换为沿途可达餐饮。",
                            )
                        )
                    seen_restaurants.add(meal_key)
                    if not meal.source_evidence:
                        issues.append(
                            ValidationIssue(
                                severity="high",
                                category="meal-evidence",
                                message=f"第 {day.day} 天餐饮 {meal.venue_name} 缺少真实来源证据。",
                                day=day.day,
                                suggested_fix="重新检索真实餐饮候选。",
                            )
                        )

            if not day.transport_segments:
                issues.append(
                    ValidationIssue(
                        severity="high",
                        category="transport-missing",
                        message=f"第 {day.day} 天缺少详细交通分段。",
                        day=day.day,
                        suggested_fix="为酒店、景点和餐饮节点补齐交通方式与耗时。",
                    )
                )
            if not day.transport.route_path:
                issues.append(
                    ValidationIssue(
                        severity="medium",
                        category="map-missing",
                        message=f"第 {day.day} 天地图缺少完整路线。",
                        day=day.day,
                        suggested_fix="补充路径数据并重新生成地图。",
                    )
                )

            must_have_hits = sum(1 for spot in day.spots if set(spot.tags).intersection(constraints.must_have_tags))
            if constraints.must_have_tags and must_have_hits == 0:
                issues.append(
                    ValidationIssue(
                        severity="low",
                        category="interest-match",
                        message=f"第 {day.day} 天对用户核心兴趣覆盖不足。",
                        day=day.day,
                        suggested_fix="替换为更符合兴趣标签的景点。",
                    )
                )

            for spot in day.spots:
                if self._spot_has_avoid_tag(spot, avoid_tags):
                    issues.append(
                        ValidationIssue(
                            severity="high",
                            category="avoid-tag-violation",
                            message=f"景点 {spot.name} 命中规避标签：{'、'.join(constraints.avoid_tags)}。",
                            day=day.day,
                            suggested_fix="替换为不含规避标签的候选景点。",
                        )
                    )

                poi_key = self._poi_key(spot)
                if poi_key in seen_pois:
                    issues.append(
                        ValidationIssue(
                            severity="medium",
                            category="poi-cross-day-repeat",
                            message=f"景点 {spot.name} 在多天行程中重复出现。",
                            day=day.day,
                            suggested_fix="重新分配当日景点，优先改用同片区且未出现过的候选点。",
                        )
                    )
                seen_pois.add(poi_key)
                if not spot.source_evidence:
                    issues.append(
                        ValidationIssue(
                            severity="high",
                            category="poi-evidence",
                            message=f"景点 {spot.name} 缺少真实来源证据。",
                            day=day.day,
                            suggested_fix="重新检索真实景点候选。",
                        )
                    )

        if meal_prices and len({round(price, 1) for price in meal_prices}) <= 2:
            issues.append(
                ValidationIssue(
                    severity="medium",
                    category="meal-price-flat",
                    message="当前餐饮价格层次过于单一，不符合真实旅行消费场景。",
                    day=None,
                    suggested_fix="拉开午餐和晚餐价格梯度，并结合馆子类型调整。",
                )
            )

        score = self._score_plan(issues)
        return issues, score

    def should_revise(self, issues: list[ValidationIssue]) -> bool:
        return any(issue.severity in {"high", "medium"} for issue in issues)

    def _score_plan(self, issues: list[ValidationIssue]) -> float:
        score = 100.0
        for issue in issues:
            if issue.severity == "high":
                score -= 18
            elif issue.severity == "medium":
                score -= 10
            else:
                score -= 4
        return max(0.0, score)

    def _required_names(self, constraints: TravelConstraints) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()
        for value in constraints.must_include_spots:
            normalized = value.strip()
            if not normalized:
                continue
            key = self._normalize_text(normalized)
            if key in seen:
                continue
            seen.add(key)
            names.append(normalized)
        for values in constraints.must_include_spots_by_day.values():
            for value in values:
                normalized = value.strip()
                if not normalized:
                    continue
                key = self._normalize_text(normalized)
                if key in seen:
                    continue
                seen.add(key)
                names.append(normalized)
        return names

    def _is_required_match(self, current_name: str, required_name: str) -> bool:
        current_norm = self._normalize_text(current_name)
        required_norm = self._normalize_text(required_name)
        if not current_norm or not required_norm:
            return False
        return (
            current_norm == required_norm
            or required_norm in current_norm
            or current_norm in required_norm
        )

    def _spot_has_avoid_tag(self, spot, avoid_tags: list[str]) -> bool:
        if not avoid_tags:
            return False
        tags = {tag.strip().lower() for tag in getattr(spot, "tags", []) if str(tag).strip()}
        if tags.intersection(avoid_tags):
            return True
        text = f"{spot.name} {spot.category} {spot.description}".lower()
        return any(tag and tag in text for tag in avoid_tags)

    def _hotel_matches_area(self, hotel, area: str) -> bool:
        area_text = area.strip().lower()
        if not area_text:
            return True
        text = f"{hotel.name} {hotel.district} {hotel.address}".lower()
        return area_text in text

    def _meal_key(self, meal) -> str:
        snippet = ""
        if meal.source_evidence:
            snippet = (meal.source_evidence[0].snippet or "").strip().lower()
        district = (meal.venue_district or "").strip().lower()
        return f"{meal.venue_name.strip().lower()}|{snippet or district}"

    def _poi_key(self, spot) -> str:
        address = (spot.address or spot.description or spot.district or "").strip().lower()
        return f"{spot.name.strip().lower()}|{address}"

    def _normalize_text(self, value: str) -> str:
        return re.sub(r"[\s·・,，。；:：\\-_/()（）]+", "", value.strip().lower())
