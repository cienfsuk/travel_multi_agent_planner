from __future__ import annotations

from ..models import BudgetLine, BudgetSummary, CityProfile, DayPlan, TripRequest


class BudgetAgent:
    def build_budget(
        self,
        request: TripRequest,
        profile: CityProfile,
        day_plans: list[DayPlan],
        round_trip_transport_cost: float,
        round_trip_note: str,
    ) -> BudgetSummary:
        hotel_prices = [day.hotel.price_per_night for day in day_plans if day.hotel]
        nights = max(request.days - 1, 1)
        if hotel_prices:
            accommodation_total = round(sum(hotel_prices[:nights]), 2)
            accommodation_note = "基于 Hotel Agent 选中的酒店候选估算"
        else:
            accommodation_rate = profile.accommodation_budget[self._accommodation_tier(request)]
            accommodation_total = round(accommodation_rate * nights, 2)
            accommodation_note = f"按 {self._accommodation_tier(request)} 档估算"

        local_transport_total = round(
            sum(day.transport.estimated_cost for day in day_plans) + profile.daily_local_transport_cost * 0.35 * request.days,
            2,
        )
        tickets_total = round(sum(spot.ticket_cost for day in day_plans for spot in day.spots), 2)
        food_total = round(sum(meal.estimated_cost for day in day_plans for meal in day.meals), 2)
        contingency = round((accommodation_total + local_transport_total + tickets_total + food_total) * 0.1, 2)

        lines = [
            BudgetLine("城际交通", round_trip_transport_cost, round_trip_note),
            BudgetLine("住宿", accommodation_total, accommodation_note),
            BudgetLine("市内交通", local_transport_total, "基于逐段交通方式和本地机动成本估算"),
            BudgetLine("门票", tickets_total, "基于已选景点门票求和"),
            BudgetLine("餐饮", food_total, "基于每日午餐和晚餐估算"),
            BudgetLine("机动预留", contingency, "预留 10% 机动预算"),
        ]
        total_estimated = round(sum(line.amount for line in lines), 2)
        remaining_budget = round(request.budget - total_estimated, 2)
        return BudgetSummary(
            total_estimated=total_estimated,
            remaining_budget=remaining_budget,
            is_within_budget=remaining_budget >= 0,
            lines=lines,
        )

    def _accommodation_tier(self, request: TripRequest) -> str:
        if request.hotel_budget_preference in {"budget", "balanced", "premium"}:
            return request.hotel_budget_preference
        if request.budget / max(request.days, 1) < 350:
            return "budget"
        if request.budget / max(request.days, 1) < 700:
            return "balanced"
        return "premium"
