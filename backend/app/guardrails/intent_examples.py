"""Labelled example utterances for the nearest-neighbour intent classifier.

Each row is ``(intent, reply_key, text)``. ``reply_key`` names the canned
reply ``app.guardrails.intent`` may give when the message is *purely* that
intent (``None``: no canned reply, the message goes to retrieval).

QUESTION rows matter as much as the others: they pull real product
questions that share words with support/complaint phrasing ("what happens to
my data if I cancel", "does it help when MFA is not working") away from the
canned replies. Add rows here, not regexes, when a phrasing is misrouted.
"""

from __future__ import annotations

import re

from app.harness.decision import Intent

_CATALOGUE_MAX_CHARS = 1000

GREETING = "greeting"
THANKS = "thanks"
FAREWELL = "farewell"
CAPABILITY = "capability"
IDENTITY = "identity"
ACK = "ack"
CONTACT = "contact"
CALLBACK = "callback"
INCIDENT = "incident"
CANCEL = "cancel"
BROKEN = "broken"
MANAGER = "manager"
FRUSTRATION = "frustration"
ASSURANCE = "assurance"
# Persona reply keys ("persona_<intent>", app.harness.prompt_catalog).
PERSONA_CREATOR = "persona_creator"
PERSONA_MODEL = "persona_model"
PERSONA_LANGUAGES = "persona_languages"
PERSONA_PRIVACY = "persona_privacy"


def _rows(intent: Intent, reply: str | None, *texts: str) -> list[tuple]:
    return [(intent, reply, t) for t in texts]


EXAMPLES: tuple[tuple[Intent, str | None, str], ...] = tuple(
    _rows(
        Intent.GREETING,
        GREETING,
        "hi",
        "hello",
        "hey there",
        "good morning",
        "good afternoon team",
        "hiya",
        "g'day",
        "howdy folks",
        "hello, anyone there?",
        "hey, how are you?",
        "hi, how's it going",
        "greetings",
    )
    + _rows(
        Intent.THANKS,
        THANKS,
        "thanks",
        "thank you so much",
        "cheers mate",
        "thanks for the help",
        "appreciate it",
        "that was helpful, thanks",
        "great, thank you",
        "many thanks",
        "ta",
        "thanks heaps",
        "you're awesome",
        "you rock",
        "great job, well done",
    )
    + _rows(
        Intent.FAREWELL,
        FAREWELL,
        "bye",
        "goodbye",
        "see you later",
        "catch you later",
        "that's all for now, bye",
        "have a good day",
        "talk soon",
        "I'm done, bye",
        "good night",
    )
    + _rows(
        Intent.CAPABILITY,
        CAPABILITY,
        "what can you do?",
        "how can you help me?",
        "what can I ask you?",
        "what do you know about?",
        "what topics can you help with?",
        "what are you able to do",
        "what kind of questions can you answer",
    )
    + _rows(
        Intent.IDENTITY,
        IDENTITY,
        "who are you",
        "what is your name",
        "are you a bot?",
        "am I talking to a real person?",
        "are you human",
        "what AI model are you?",
        "are you chatgpt",
        "who made you",
        "is this an AI?",
        "are you a real person?",
        "do you have feelings?",
        "do you have emotions",
        "do you have opinions?",
        "do you remember me?",
        "are you alive?",
    )
    # PERSONA paraphrases the anchored patterns miss
    # (app.guardrails.conversational.detect_persona).
    + _rows(
        Intent.IDENTITY,
        PERSONA_CREATOR,
        "who made this chatbot",
        "who do you work for",
        "which company is behind this assistant",
        "who programmed this bot",
    )
    + _rows(
        Intent.IDENTITY,
        PERSONA_MODEL,
        "what large language model powers you",
        "are you built on openai",
        "which AI company made your model",
    )
    + _rows(
        Intent.CAPABILITY,
        PERSONA_LANGUAGES,
        "what languages do you speak",
        "can I chat with you in Spanish",
        "do you understand other languages",
    )
    + _rows(
        Intent.CAPABILITY,
        PERSONA_PRIVACY,
        "is this chat private",
        "is our conversation confidential",
        "does anyone read what I type here",
        "are my messages to you stored",
    )
    + _rows(
        Intent.ACKNOWLEDGEMENT,
        ACK,
        "ok",
        "okay",
        "sure",
        "cool",
        "got it",
        "alright",
        "yes",
        "no",
        "hmm",
        "lol",
        "test",
        "nice",
    )
    + _rows(
        Intent.CONTACT,
        CONTACT,
        # Human hand-off only: contact details (phone, email, offices, the
        # contact form) are on the Contact Us page and are retrieved.
        "I want to talk to a human",
        "can I speak to someone",
        "let me chat with a real person",
    )
    + _rows(
        Intent.CONTACT,
        CALLBACK,
        "call me",
        "can someone call me back",
        "please ring me",
        "give me a call on 0400 000 000",
        "call me back tomorrow",
        "phone me please",
        "can your team call me this afternoon",
        "I'd like a callback",
        "please have someone ring me back",
        "my number is 0400 111 222, call me",
    )
    + _rows(
        Intent.INCIDENT,
        INCIDENT,
        "we got hacked",
        "we've been breached",
        "our email was compromised",
        "we have ransomware",
        "someone hacked our accounts",
        "we're under attack right now",
        "I think we've been phished",
        "our systems have been infected with malware",
        "a hacker got into our network",
        "help, we've had a cyber attack",
        "our accounts were taken over last night",
    )
    + _rows(
        Intent.ACCOUNT_SUPPORT,
        CANCEL,
        "I want to cancel my subscription",
        "cancel my account",
        "please cancel my plan",
        "I want a refund",
        "I'd like my money back",
        "how do I unsubscribe",
        "close my account",
        "stop billing me",
        "I want to end my contract with you",
        "cancellation please",
    )
    + _rows(
        Intent.ACCOUNT_SUPPORT,
        BROKEN,
        "your product broke my tenant",
        "it stopped working",
        "the thing you set up is broken",
        "nothing works since you made changes",
        "your changes broke everything",
        "the chatbot isn't working",
        "this page keeps crashing",
    )
    + _rows(
        Intent.ACCOUNT_SUPPORT,
        MANAGER,
        "I want to speak to a manager",
        "let me talk to your supervisor",
        "escalate this to a manager",
        "get me your boss",
    )
    + _rows(
        Intent.COMPLAINT,
        FRUSTRATION,
        "this is useless",
        "this bot is rubbish",
        "you're not helpful at all",
        "I'm not happy",
        "I'm really disappointed with you",
        "I want to make a complaint",
        "this is a waste of time",
        "I'm fed up",
        "terrible service",
        "you are pathetic",
        "worst chatbot ever",
        "I'm so frustrated with this",
    )
    + _rows(
        Intent.CAPABILITY,
        ASSURANCE,
        "is it 100% secure?",
        "can you guarantee we will never be hacked?",
        "do you guarantee security",
        "promise me it's safe",
        "is it unhackable",
        "is it hack proof",
        "will we be completely safe",
        "can you make us bulletproof",
    )
    + _rows(
        Intent.BUYING,
        None,
        "how much does it cost?",
        "what are your prices",
        "can I get a quote",
        "I want to buy",
        "sign me up",
        "book a demo",
        "I'd like to purchase a package",
        "how do I get started with you",
        "what's the price of the Foundation package",
        "how much is the Essential Eight assessment",
        "can I book a consultation",
        "we want to engage you for a project",
        "is there a free trial",
        "we'd like to purchase the Strengthen package",
        "how do we sign up for a Zero Trust assessment",
        "what does a CIS assessment cost",
        "I want to buy your product",
        "we are ready to go ahead, what are the next steps to buy",
    )
    + _rows(
        Intent.QUESTION,
        None,
        # product / service questions
        "what is DeployIQ?",
        "what is Zero Trust",
        "what is the Essential Eight",
        "what does the CIS benchmark assessment cover",
        "how does the Security Baseline Assessment work",
        "do you help with ISO 27001",
        "what is ISO 42001",
        "what packages do you offer",
        "what is included in the Strengthen package",
        "does DeployIQ support Intune",
        "can you secure Microsoft Copilot",
        "how do you handle MFA in Entra",
        "how long does an assessment take",
        "what services do you provide",
        "who is Exigo Tech",
        "do you need admin access to my tenant",
        "is there human approval before changes",
        "what evidence do I get at the end",
        "how many controls does the baseline check",
        "what maturity level can we reach",
        # questions that share words with support / complaint / assurance
        "if we stop using your service, what happens to our data?",
        "what happens to our files when the contract ends",
        "what is your termination policy",
        "can you terminate the service",
        "how do you handle my personal information",
        "can it revoke old sign-in sessions",
        "our multi-factor sign-in keeps failing, is that something you fix?",
        "our conditional access is broken, does your service fix that?",
        "we are not happy with our current IT provider, could you take over?",
        "can you replace our managed service provider?",
        "does the review check every single control, all 100%?",
        "what does 100% approval mean?",
        "how secure is my data with you",
        "do you guarantee compliance with the Essential Eight?",
        "how do you handle complaints?",
        "can I call your team to discuss Zero Trust?",
        "what should we do after a breach to improve security",
        "how does DeployIQ help after a ransomware attack",
        "can you help us recover from being hacked?",
        "what are the terms of service",
        "can I upgrade to a higher package later",
        "who do I contact for a security assessment",
        # contact details and offices (answered from the Contact Us page)
        "how do I contact you?",
        "what is your phone number",
        "what's your email address",
        "how can I get in touch with your team",
        "where is your office",
        "do you have a contact form",
        "which cities are you located in",
        # bare topics and follow-up fragments
        "security",
        "cyber security",
        "what about pricing?",
        # off-topic questions (retrieval gives the off-topic reply)
        "what is the weather today",
        "banana bread recipe",
        "what is 2+2",
        "who won the football",
        "write me a poem",
        "what is the capital of France",
        "tell me a joke",
        "how do I fix my car",
    )
)


# ---------------------------------------------------------------------------
# Knowledge-base catalogue: naming any of these makes a message a product
# question (never a canned reply).
# Bounded, word-bounded alternations only; input is capped first.
# ---------------------------------------------------------------------------
_CATALOGUE_RE = re.compile(
    r"\b(?:deploy\s{0,2}iq|askdiq|exigo(?:\s{0,2}tech)?|zero\s{1,3}trust|zt|"
    r"essential\s{1,3}(?:eight|8)|essential8|e8|ml[1-3]|acsc|asd|cis|sba|"
    r"professional|"
    r"iso(?:\s{0,2}\d{4,5})?|27001|42001|nist|soc\s{0,2}2|"
    r"security\s{1,3}baseline|baselines?|benchmarks?|packages?|tiers?|"
    r"foundation|strengthen|frontier|starter|"
    r"microsoft(?:\s{1,3}365)?|m365|o365|office\s{0,2}365|intune|entra|azure|"
    r"mfa|multi[\s-]{0,2}factor|2fa|copilot|sharepoint|onedrive|defender|"
    r"purview|sentinel|conditional\s{1,3}access|mdr|edr|siem|"
    r"managed\s{1,3}(?:detection|services?(?:\s{1,3}providers?)?)|msps?|"
    r"it\s{1,3}providers?|assessments?|audits?|frameworks?|compliance|"
    r"compliant|controls?|maturity|tenants?|admins?|sessions?|data|privacy|"
    r"personal\s{1,3}information|polic(?:y|ies)|terms|responsible\s{1,3}ai|"
    r"secure\s{1,3}ai|ai\s{1,3}agents?|agents?|platform|governance|"
    r"licen[cs]e[sd]?|licensing)\b",
    re.IGNORECASE,
)


def names_catalogue_term(text: str) -> bool:
    """True when the message names a product, service, framework or KB topic."""
    return _CATALOGUE_RE.search(text[:_CATALOGUE_MAX_CHARS]) is not None


# Contact details, offices and locations are published on the Contact Us page,
# so a message naming one is answered from the knowledge base. Kept apart from
# the catalogue so "our email was compromised" stays an incident.
_CONTACT_DETAIL_RE = re.compile(
    r"\b(?:e-?mails?|e-?mail\s{1,3}address(?:es)?|address(?:es)?|"
    r"(?:phone|telephone|mobile|contact|landline)\s{1,3}(?:numbers?|details?|info)|"
    r"telephone|landline|offices?|locations?|located|headquarters|hq|branch(?:es)?|"
    r"sydney|melbourne|brisbane|perth|adelaide|canberra|hobart|darwin|norwest|"
    r"australia|contact(?:\s{1,3}(?:us|you|form|page|details?))?|"
    r"get\s{1,3}in\s{1,3}touch|reach\s{1,3}(?:you|your\s{1,3}team)|"
    r"opening\s{1,3}hours|business\s{1,3}hours|abn)\b",
    re.IGNORECASE,
)
# "what about email?" / "and pricing?" / "how about Melbourne": a follow-up to
# the previous answer, never a stand-alone small-talk message.
_FOLLOW_UP_RE = re.compile(
    r"^\s{0,3}(?:(?:what|how)\s{1,3}about|and(?:\s{1,3}(?:what|how)\s{1,3}about)?|"
    r"what\s{1,3}else\s{1,3}about)\s{1,3}\S",
    re.IGNORECASE,
)


def names_contact_detail(text: str) -> bool:
    """True when the message names a contact detail, office or location."""
    return _CONTACT_DETAIL_RE.search(text[:_CATALOGUE_MAX_CHARS]) is not None


def is_follow_up_fragment(text: str) -> bool:
    """True for "what about X?"-style follow-ups (they need retrieval)."""
    return _FOLLOW_UP_RE.match(text[:_CATALOGUE_MAX_CHARS]) is not None
