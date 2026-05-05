"""Personalization demo script - 测试个性化扩展功能"""

import asyncio
from pathlib import Path
from personalization import PersonalizationEngine


async def demo():
    print("=" * 60)
    print("个性化扩展模块演示")
    print("=" * 60)

    engine = PersonalizationEngine(Path("."))

    # 演示用例
    test_cases = [
        "不需要吃早饭",
        "开车需要停车",
        "景点安排更分散",
        "行程放松一点",
    ]

    for i, requirement in enumerate(test_cases, 1):
        print(f"\n{'=' * 60}")
        print(f"测试 {i}: {requirement}")
        print("-" * 60)

        result = await engine.process_requirement(requirement)

        print(f"解析结果:")
        print(f"  - 原始需求: {result.parsed_requirement.raw_text}")
        print(f"  - 修改类型: {result.parsed_requirement.modification_type}")
        print(f"  - 目标文件: {result.parsed_requirement.target_files}")
        print(f"  - 置信度: {result.parsed_requirement.confidence:.2f}")

        if result.impact_report:
            print(f"\n影响分析:")
            print(f"  - 风险等级: {result.impact_report.risk_level.value}")
            print(f"  - 影响文件: {result.impact_report.impacted_files}")
            print(f"  - 影响Agent: {result.impact_report.impacted_agents}")
            print(f"  - 摘要: {result.impact_report.summary}")

        if result.review_result:
            print(f"\n代码审查:")
            print(f"  - 通过: {result.review_result.passed}")
            print(f"  - 建议: {result.review_result.recommendation}")
            print(f"  - 问题数: {len(result.review_result.issues)}")

        if result.modification_patch:
            print(f"\n补丁信息:")
            print(f"  - 补丁ID: {result.modification_patch.patch_id}")
            print(f"  - 修改文件数: {len(result.modification_patch.patches)}")
            for patch in result.modification_patch.patches:
                print(f"    • {patch.file_path} ({patch.operation.value})")

        print(f"\n状态: {result.status}")
        print(f"需要用户确认: {result.requires_confirmation}")

    # 交互式测试
    print("\n" + "=" * 60)
    print("交互式测试")
    print("=" * 60)
    print("\n输入你的个性化需求（或输入 'quit' 退出）:")

    while True:
        user_input = input("\n> ").strip()
        if user_input.lower() == "quit":
            print("退出演示")
            break
        if not user_input:
            continue

        print("\n处理中...")
        result = await engine.process_requirement(user_input)

        print(f"\n解析: {result.parsed_requirement.modification_type} - {result.parsed_requirement.target_files}")

        if result.impact_report:
            print(f"风险: {result.impact_report.risk_level.value} | {result.impact_report.summary}")

        if result.review_result:
            print(f"审查: {'通过' if result.review_result.passed else '未通过'} | 建议: {result.review_result.recommendation}")

        if result.modification_patch:
            confirm = input("确认执行此修改? (y/n): ").strip().lower()
            if confirm == "y":
                apply_result = await engine.apply_modification(
                    result.parsed_requirement.requirement_id,
                    approved=True
                )
                print(f"应用结果: {apply_result.status} - {apply_result.apply_message}")
                if apply_result.snapshot_id:
                    print(f"快照ID: {apply_result.snapshot_id}")
            else:
                print("已取消")


if __name__ == "__main__":
    asyncio.run(demo())