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
    "how much?",
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
    "price",
    "what are your prices?",
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

# Company facts and named entities: answered from the knowledge base, never
# turned into a "which DeployIQ service do you mean?" card.
COMPANY_FACT_QUERIES: tuple[str, ...] = (
    "Where is Exigo Tech headquartered?",
    "who is exigo?",
    "Who is the CEO of Exigo Tech?",
    "What is Exigo Tech's phone number?",
    "contact",
    "Where are you located?",
    "When was Exigo Tech founded?",
    "what is your address?",
    "How do I contact DeployIQ?",
    "DeployIQ pricing",
)

# One-product assistant, no history: answered generally about DeployIQ.
GENERIC_PRODUCT_QUERIES: tuple[str, ...] = (
    "how does it work",
    "What does it do?",
    "Is it safe?",
    "is it free?",
)

# Pointers back at the previous answer (ask which one unless it named one).
BACK_REFERENCE_QUERIES: tuple[str, ...] = (
    "Tell me more about the one you mentioned above",
    "What about the previous one?",
    "How much is the second one?",
    "How much does that product cost?",
    "and the one above?",
    "What did you say earlier?",
)

# Vague goals with no framework named: ask Framework (+ Maturity) questions.
VAGUE_GOAL_QUERIES: tuple[str, ...] = (
    "I need compliance",
    "we need to improve our security posture, where do we start",
    "we want to get certified",
    "how do I secure my company",
    "We have an audit coming up",
    "help us get ready for our audit",
    "We need to get complience fast, what should we do?",
    "we need to be complaint, where do we start",
    "we have an audti coming up",
)
