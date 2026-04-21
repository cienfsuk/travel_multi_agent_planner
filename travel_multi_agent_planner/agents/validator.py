from __future__ import annotations

from ..models import BudgetSummary, CityMatch, DayPlan, TravelConstraints, TripRequest, ValidationIssue
from ..scheduling import (
    DINNER_START_MAX_MINUTES,
    DINNER_START_MINUTES,
    INTERCITY_ARRIVAL_BUFFER_MINUTES,
    INTERCITY_DEPARTURE_BUFFER_MINUTES,
    LUNCH_START_MAX_MINUTES,
    LUNCH_START_MINUTES,
    build_scheduled_day_timeline,
    parse_clock,
    segment_arrival_minutes,
    segment_departure_minutes,
)


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
                    suggested_fix="减少付费景点、降低住宿和餐饮档位，优先保留核心景点。",
                )
            )

        intercity_lines = [line for line in budget_summary.lines if line.category == "城际交通"]
        if not intercity_lines:
            issues.append(
                ValidationIssue(
                    severity="high",
                    category="intercity-budget-missing",
                    message="预算中缺少出发地与目的地往返交通费用。",
                    day=None,
                    suggested_fix="在预算汇总中明确加入城际往返交通，并与来往交通区保持一致。",
                )
            )

        seen_restaurants: set[str] = set()
        seen_pois: set[str] = set()
        meal_prices: list[float] = []
        for day in day_plans:
            scheduled = build_scheduled_day_timeline(day)
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
                        suggested_fix="为当天补充酒店候选，并优先靠近景点聚集区。",
                    )
                )
            elif not day.hotel.source_evidence:
                issues.append(
                    ValidationIssue(
                        severity="high",
                        category="hotel-evidence",
                        message=f"第 {day.day} 天酒店缺少真实来源证据。",
                        day=day.day,
                        suggested_fix="重新检索真实酒店候选。",
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
                continue

            lunch, dinner = day.meals[0], day.meals[1]
            if lunch.venue_name == dinner.venue_name:
                issues.append(
                    ValidationIssue(
                        severity="medium",
                        category="meal-repeat",
                        message=f"第 {day.day} 天午餐和晚餐使用了同一家餐厅。",
                        day=day.day,
                        suggested_fix="替换其中一餐为不同类型的本地馆子或夜游餐厅。",
                    )
                )

            for meal in day.meals:
                meal_prices.append(meal.estimated_cost)
                meal_key = self._meal_key(meal)
                if meal_key in seen_restaurants:
                    issues.append(
                        ValidationIssue(
                            severity="low" if meal.fallback_used else "medium",
                            category="cross-day-repeat",
                            message=f"{meal.venue_name} 在多天行程中重复出现。{'当前为候选不足后的回退结果。' if meal.fallback_used else ''}",
                            day=day.day,
                            suggested_fix="扩大沿路线餐饮候选池，优先改用路线 2 公里内的替代馆子。",
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
                if meal.fallback_used:
                    issues.append(
                        ValidationIssue(
                            severity="low",
                            category="meal-candidate-limited",
                            message=f"第 {day.day} 天 {meal.venue_name} 使用了候选不足回退策略。",
                            day=day.day,
                            suggested_fix="继续扩充沿路线餐饮候选，优先使用距离主路线 1 公里内的店铺。",
                        )
                    )
                route_limit = 1.0 if meal.meal_type == "lunch" else 1.5
                if meal.route_distance_km > route_limit:
                    issues.append(
                        ValidationIssue(
                            severity="medium" if not meal.fallback_used else "low",
                            category="meal-route-detour-too-far",
                            message=f"第 {day.day} 天 {meal.venue_name} 距离当餐主路线约 {meal.route_distance_km:.1f} km，偏离主路线较远。",
                            day=day.day,
                            suggested_fix="优先改用距主路线 1 公里内的沿途餐饮，必要时再逐级放宽半径。",
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
            total_transport_minutes = sum(segment.duration_minutes for segment in day.transport_segments)
            if total_transport_minutes > 210:
                issues.append(
                    ValidationIssue(
                        severity="high",
                        category="transport-too-long",
                        message=f"第 {day.day} 天市内交通总时长过长，当前约 {total_transport_minutes} 分钟。",
                        day=day.day,
                        suggested_fix="减少远距景点与绕路餐饮，把行程压回同一路径带内。",
                    )
                )
            elif total_transport_minutes > 150:
                issues.append(
                    ValidationIssue(
                        severity="medium",
                        category="transport-too-long",
                        message=f"第 {day.day} 天市内交通总时长偏长，当前约 {total_transport_minutes} 分钟。",
                        day=day.day,
                        suggested_fix="优先替换偏远景点，压缩同日跨区移动。",
                    )
                )
            if not day.transport.route_path:
                issues.append(
                    ValidationIssue(
                        severity="medium",
                        category="map-missing",
                        message=f"第 {day.day} 天地图缺少完整线路。",
                        day=day.day,
                        suggested_fix="补充腾讯路径返回的线路数据。",
                    )
                )
            for segment in day.transport_segments:
                if getattr(segment, "path_status", "ok") == "missing":
                    issues.append(
                        ValidationIssue(
                            severity="medium",
                            category="segment-path-missing",
                            message=f"第 {day.day} 天 {segment.from_label} -> {segment.to_label} 缺少真实路径 polyline，当前未绘制替代直线。",
                            day=day.day,
                            suggested_fix="重新请求腾讯路线；若服务端仍无 polyline，则保留文本提示但不要渲染伪路线。",
                        )
                    )
                elif len(segment.path) <= 2 and segment.distance_km > 3.0:
                    issues.append(
                        ValidationIssue(
                            severity="medium",
                            category="segment-illegal-straight-fallback",
                            message=f"第 {day.day} 天 {segment.from_label} -> {segment.to_label} 仅返回近似直连路径，当前约 {segment.distance_km:.1f} km。",
                            day=day.day,
                            suggested_fix="优先使用腾讯真实 polyline；若仍缺失，则保留节点与说明，不再绘制直连线。",
                        )
                    )
                if segment.distance_km > 25.0:
                    issues.append(
                        ValidationIssue(
                            severity="high",
                            category="segment-too-long",
                            message=f"第 {day.day} 天 {segment.from_label} -> {segment.to_label} 交通段过长，当前约 {segment.distance_km:.1f} km。",
                            day=day.day,
                            suggested_fix="替换远端点位或重新聚类当日路线，避免把孤立景点与中心城区硬串联。",
                        )
                    )
                elif segment.distance_km > 15.0:
                    issues.append(
                        ValidationIssue(
                            severity="medium",
                            category="segment-too-long",
                            message=f"第 {day.day} 天 {segment.from_label} -> {segment.to_label} 交通段偏长，当前约 {segment.distance_km:.1f} km。",
                            day=day.day,
                            suggested_fix="优先压缩跨区段，把餐饮和景点调整到同一路径带内。",
                        )
                    )

            meal_schedule = {row["kind"]: row for row in scheduled if row["kind"] in {"lunch", "dinner"}}
            lunch_row = meal_schedule.get("lunch")
            if lunch_row is not None:
                lunch_start = parse_clock(lunch_row["start_time"]) or 0
                if not (LUNCH_START_MINUTES <= lunch_start <= LUNCH_START_MAX_MINUTES):
                    issues.append(
                        ValidationIssue(
                            severity="high",
                            category="meal-window-lunch",
                            message=f"第 {day.day} 天午餐开始时间为 {lunch_row['start_time']}，不在 11:30-13:30 窗口内。",
                            day=day.day,
                            suggested_fix="减少午餐前活动或把午餐前置，确保午餐落在 11:30-13:30 之间。",
                        )
                    )
            dinner_row = meal_schedule.get("dinner")
            if dinner_row is not None:
                dinner_start = parse_clock(dinner_row["start_time"]) or 0
                if not (DINNER_START_MINUTES <= dinner_start <= DINNER_START_MAX_MINUTES):
                    issues.append(
                        ValidationIssue(
                            severity="high",
                            category="meal-window-dinner",
                            message=f"第 {day.day} 天晚餐开始时间为 {dinner_row['start_time']}，不在 17:00-19:00 窗口内。",
                            day=day.day,
                            suggested_fix="减少晚餐前活动或把夜游改到餐后，确保晚餐落在 17:00-19:00 之间。",
                        )
                    )

            arrival_minutes = segment_arrival_minutes(day.arrival_segment)
            if arrival_minutes is not None:
                earliest_play_minutes = arrival_minutes + INTERCITY_ARRIVAL_BUFFER_MINUTES
                first_spot_row = next((row for row in scheduled if row["kind"] == "spot"), None)
                if first_spot_row is not None:
                    first_spot_start = parse_clock(first_spot_row["start_time"]) or 0
                    if first_spot_start < earliest_play_minutes:
                        issues.append(
                            ValidationIssue(
                                severity="high",
                                category="arrival-buffer-conflict",
                                message=f"第 {day.day} 天首个景点开始于 {first_spot_row['start_time']}，早于入城缓冲后的可执行时间。",
                                day=day.day,
                                suggested_fix="顺延首日活动，必要时先午餐再开始下午行程。",
                            )
                        )
                if arrival_minutes >= 11 * 60 and day.spots:
                    first_spot = day.spots[0]
                    if str(first_spot.best_time).lower() == "morning":
                        issues.append(
                            ValidationIssue(
                                severity="medium",
                                category="arrival-morning-spot",
                                message=f"第 {day.day} 天到达时间较晚，但首个景点 {first_spot.name} 仍偏上午型。",
                                day=day.day,
                                suggested_fix="把首日下午优先替换为更适合下午/傍晚的景点。",
                            )
                        )

            departure_minutes = segment_departure_minutes(day.departure_segment)
            if departure_minutes is not None and scheduled:
                latest_finish = departure_minutes - INTERCITY_DEPARTURE_BUFFER_MINUTES
                last_row = scheduled[-1]
                last_end = parse_clock(last_row["end_time"]) or 0
                if last_end > latest_finish:
                    issues.append(
                        ValidationIssue(
                            severity="high",
                            category="departure-buffer-conflict",
                            message=f"第 {day.day} 天最后活动结束于 {last_row['end_time']}，已挤占返程缓冲。",
                            day=day.day,
                            suggested_fix="压缩末日活动数量，确保返程前至少保留 90 分钟缓冲。",
                        )
                    )

            for spot in day.spots:
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

            must_have_hits = sum(1 for spot in day.spots if set(spot.tags).intersection(constraints.must_have_tags))
            if constraints.must_have_tags and must_have_hits == 0:
                issues.append(
                    ValidationIssue(
                        severity="low",
                        category="interest-match",
                        message=f"第 {day.day} 天对用户核心兴趣覆盖不足。",
                        day=day.day,
                        suggested_fix="替换成更符合用户兴趣标签的景点。",
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

    def _meal_key(self, meal) -> str:
        snippet = ""
        if meal.source_evidence:
            snippet = (meal.source_evidence[0].snippet or "").strip().lower()
        district = (meal.venue_district or "").strip().lower()
        return f"{meal.venue_name.strip().lower()}|{snippet or district}"

    def _poi_key(self, spot) -> str:
        address = (spot.address or spot.description or spot.district or "").strip().lower()
        return f"{spot.name.strip().lower()}|{address}"
