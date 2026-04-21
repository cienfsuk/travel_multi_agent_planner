from __future__ import annotations

from ..models import TravelConstraints, TripRequest


class RequirementAgent:
    def build_constraints(self, request: TripRequest, llm_provider: object | None = None) -> tuple[TravelConstraints, str]:
        request_text = (
            f"{request.origin} 到 {request.destination}，{request.days} 天，预算 {request.budget:.0f} 元，"
            f"兴趣 {', '.join(request.interests)}，节奏 {request.style}，备注 {request.additional_notes or '无'}。"
        )
        if llm_provider and hasattr(llm_provider, "generate_constraints"):
            generated = llm_provider.generate_constraints(
                request_text,
                {
                    "destination": request.destination,
                    "days": request.days,
                    "budget": request.budget,
                    "interests": request.interests,
                    "preferred_areas": request.preferred_areas,
                    "avoid_tags": request.avoid_tags,
                    "style": request.style,
                },
            )
            if generated:
                min_spots = {"relaxed": 2, "balanced": 3, "dense": 4}[request.style]
                min_transfers = {"relaxed": 2, "balanced": 3, "dense": 4}[request.style]
                generated.max_daily_spots = max(min_spots, generated.max_daily_spots)
                generated.max_daily_transport_transfers = max(min_transfers, generated.max_daily_transport_transfers)
                return generated, "百炼生成结构化约束"

        max_spots = {"relaxed": 2, "balanced": 3, "dense": 4}[request.style]
        transfers = {"relaxed": 2, "balanced": 3, "dense": 4}[request.style]
        constraints = TravelConstraints(
            max_daily_spots=max_spots,
            max_daily_transport_transfers=transfers,
            max_total_budget=request.budget,
            preferred_areas=request.preferred_areas,
            must_have_tags=request.interests,
            avoid_tags=request.avoid_tags,
            pacing_note=f"当前采用 {request.style} 节奏，优先保证城市深度游体验。",
        )
        return constraints, "本地规则生成结构化约束"
