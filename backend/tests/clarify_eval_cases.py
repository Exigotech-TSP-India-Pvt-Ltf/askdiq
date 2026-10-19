"""Evaluation set for clarifying questions (app.harness.clarify).

AMBIGUOUS_QUERIES are short, underspecified questions a visitor to this
knowledge base really types; with evidence spread over several documents
they may get questions back. CLEAR_QUERIES are specific and must always be
answered directly. tests/test_clarify.py asserts the trigger decision;
scripts can replay both lists against the real model to review option quality.
"""

AMBIGUOUS_QUERIES: tuple[str, ...] = (
    "do you buy?",
    "pricing?",
    "how long?",
    "is it safe?",
    "zero trust?",
    "E8?",
    "do you support Azure?",
    "cost",
    "plans",
    "support",
    "compliance?",
    "integrations?",
    "onboarding",
    "data?",
    "governance",
    "certifications?",
    "trial?",
    "partners",
)

CLEAR_QUERIES: tuple[str, ...] = (
    "How does DeployIQ implement Essential Eight application control for Windows endpoints?",
    "What does the Zero Trust Professional plan include for identity protection?",
    "How long does a typical Essential Eight maturity uplift engagement take?",
    "Where is customer data stored and is it kept in Australian data centres?",
    "Does DeployIQ integrate with Microsoft Sentinel for security monitoring alerts?",
    "What are the steps to onboard a new tenant to the DeployIQ platform?",
    "How does the human in the loop review work for Responsible AI decisions?",
    "Which ISO certifications does DeployIQ currently hold for its managed services?",
    "Can partners resell DeployIQ services under their own brand to customers?",
    "What happens to our data when we cancel the DeployIQ subscription contract?",
    "pricing? and support?",
)
