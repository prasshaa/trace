"""HELD-OUT lines (2026-11-22), written before the interpreter was tuned and scored ONCE after the freeze
(`python -m evaluation.capability_interpretation --holdout`). Never used for tuning; failures are reported, not fixed
against this set. Same author as the interpreter (not independent). Synthetic, fictional content only.

Each item: (text, clear areas, related-only areas). Anything not listed must not be read as clear evidence.
"""
H = [
    # explicit
    ("Owned the quarterly roadmap for the payments checkout team and presented it to the leadership group.",
     {"roadmap_ownership", "executive_communication"}, set()),
    ("Wrote PRDs and user stories for the onboarding revamp and groomed the backlog with two scrum teams.",
     {"requirements_analysis", "prioritisation_backlog"}, {"delivery_execution"}),
    ("Ran 25 customer interviews to understand why small clinics churned in their first month.",
     {"product_discovery"}, {"customer_account_management", "outcome_ownership"}),
    # semantic equivalents, no keyword
    ("Decided which three bets the lending team would fund next half after comparing their expected impact.",
     {"prioritisation_backlog"}, {"strategy_portfolio"}),
    ("Set the direction for the seller app for the year and sequenced the releases with engineering.",
     {"roadmap_ownership", "delivery_execution", "stakeholder_management"}, set()),
    ("Made the case to the founders for entering the SME segment, sizing the market and the expected revenue.",
     {"strategy_portfolio", "business_case_economics", "executive_communication"}, set()),
    ("Chose the success measures for the referral feature and used them to decide whether to expand it.",
     {"outcome_ownership"}, {"data_analysis"}),
    # outcomes
    ("Grew weekly active sellers by 18% by redesigning the listing flow.", {"design_research"}, {"outcome_ownership"}),
    ("Cut average order processing time from 3 days to 1 day by automating manual checks.",
     {"operations_process"}, {"outcome_ownership"}),
    ("Defined the north-star metric for the learning app and steered the roadmap with it, lifting retention by 9 points.",
     {"outcome_ownership", "roadmap_ownership", "go_to_market_growth"}, set()),
    # spread / multiple
    ("Led a team of 6 engineers and 2 designers, mentoring two junior product managers.",
     {"people_leadership"}, set()),
    ("Coordinated launches across 4 teams, managing dependencies and release sequencing.",
     {"delivery_execution", "stakeholder_management"}, set()),
    ("Designed the public APIs for partner integrations and documented them for developers.",
     {"technical_engineering"}, set()),
    ("Shipped an LLM-based summarisation feature for support agents with an offline evaluation set.",
     {"ai_product_work", "delivery_execution"}, {"quality_testing"}),
    ("Built dashboards in SQL to track funnel conversion for the growth team.",
     {"data_analysis"}, {"go_to_market_growth", "outcome_ownership"}),
    ("Ran the pricing experiment for the premium tier and recommended a price change that leadership approved.",
     {"business_case_economics", "data_analysis"}, {"executive_communication", "outcome_ownership"}),
    ("Established data-quality checks and a governance process for customer records.",
     {"data_governance_quality", "operations_process"}, set()),
    # ambiguous / weak
    ("Supported the product manager in preparing roadmap slides.", set(), {"roadmap_ownership"}),
    ("Participated in sprint planning and backlog refinement.", set(), {"prioritisation_backlog", "delivery_execution"}),
    ("Exposure to stakeholder management with regional sales heads.", set(), {"stakeholder_management", "executive_communication"}),
    ("Assisted the analytics team with weekly reporting.", set(), {"data_analysis"}),
    # unsupported claims / negatives
    ("Completed a course on product strategy and roadmapping.", set(), set()),
    ("Did not own the roadmap; implemented features from the backlog.", {"delivery_execution"}, set()),
    ("Answered customer tickets by email and phone.", set(), {"customer_account_management"}),
    ("Prepared monthly invoices for clients.", set(), set()),
    ("Member of the product team at a fintech startup.", set(), set()),
    ("Worked on the mobile app.", set(), set()),
    ("Wrote automated regression tests for the billing service.", {"quality_testing"}, set()),
    ("Managed vendor contracts and office facilities.", {"operations_process"}, set()),
    ("Taught mathematics to grade 9 students.", set(), set()),
]
