# pylint: disable=too-many-lines
"""Deterministic pre-retrieval guardrails (length, injection, secrets, junk).

Every regex that runs on visitor text uses only bounded quantifiers or
single-token loops (no adjacent/overlapping unbounded repeats), and every
check caps its input first, so none is open to polynomial ReDoS.
"""

from __future__ import annotations

import re
import string
import unicodedata
from functools import cache

import tiktoken

from app.guardrails.normalize import fold_accents, strip_format_chars

# Input is length-capped by the API layer; cap again before any regex work.
_MATCH_MAX_CHARS = 4000

# ---------------------------------------------------------------------------
# Prompt-injection / jailbreak / leak detection
# ---------------------------------------------------------------------------

# Verbs that, next to "instructions"/"prompt", are an injection on their own.
_STRONG_VERB = (
    r"(?:ignor(?:e|es|ed|ing)|disregard(?:s|ed|ing)?|forget(?:s|ting)?|forgot)"
)
# Verbs with legitimate uses ("override the default rules in the engine"):
# only an injection when aimed at the assistant's own instructions.
_WEAK_VERB = (
    r"(?:bypass(?:es|ed|ing)?|overrid(?:e|es|ing)|overrode|"
    r"overwrit(?:e|es|ing)|circumvent(?:s|ed|ing)?)"
)
_DET = (
    r"(?:the|all|any|of|every|these|those|your|my|previous|prior|above|earlier|"
    r"preceding|initial|original|system|safety|ethical|content|given|former|and)"
)
_FILLER = r"(?:(?:the|all|any|of|every|these|those)\s{1,3}){0,3}"
_CTX = (
    r"(?:your|my|previous|prior|above|earlier|preceding|initial|original|"
    r"system|safety|ethical)"
)
_RULE_NOUN = (
    r"(?:rules|guidelines|policies|restrictions|constraints|guardrails|"
    r"safeguards|filters|limitations)"
)
_INSTR_NOUN = r"(?:instructions?|prompts?|directives?|directions|programming)"
# Rules that are the assistant's own ("your safety rules"), not a product's.
_OWN_CTX = r"(?:your|ur|safety|ethical)"

# Public: also used to quarantine ingested documents, so it holds only the
# core injection phrasing (no broad leak/jailbreak patterns).
UNSAFE_PATTERNS = [
    rf"\b{_STRONG_VERB}\s{{1,3}}(?:{_DET}\s{{1,3}}){{0,5}}{_INSTR_NOUN}\b",
    rf"\b{_STRONG_VERB}\s{{1,3}}{_FILLER}{_CTX}\s{{1,3}}"
    rf"(?:[a-z]{{1,15}}\s{{1,3}}){{0,2}}{_RULE_NOUN}\b",
    rf"\b{_STRONG_VERB}\b.{{0,30}}\b(?:instructions?|rules|guidelines|prompts?)"
    r"\s{1,3}(?:above|before|earlier|prior|previously)\b",
    rf"\b{_WEAK_VERB}\s{{1,3}}{_FILLER}{_CTX}\s{{1,3}}"
    rf"(?:[a-z]{{1,15}}\s{{1,3}}){{0,2}}{_INSTR_NOUN}\b",
    # "bypass your safety rules"; not "bypass the previous restrictions of
    # manual audits" (a product question about someone else's rules).
    rf"\b{_WEAK_VERB}\s{{1,3}}{_FILLER}{_OWN_CTX}\s{{1,3}}"
    rf"(?:[a-z]{{1,15}}\s{{1,3}}){{0,2}}{_RULE_NOUN}\b",
    rf"\b{_WEAK_VERB}\s{{1,3}}(?:(?:the|of)\s{{1,3}}){{0,2}}"
    r"(?:all|any|every)\s{1,3}(?:instructions?|prompts?)\b",
    r"\b(?:d\s?o\s{1,3}not|don'?t|stop|never)\s{1,3}(?:follow(?:ing)?|obey(?:ing)?)"
    rf"\s{{1,3}}(?:{_DET}\s{{1,3}}){{0,4}}(?:{_INSTR_NOUN}|{_RULE_NOUN})\b",
    rf"\b{_STRONG_VERB}\s{{1,3}}(?:everything|all)\s{{1,3}}"
    r"(?:above|before|prior|previous|you\s{1,3}(?:know|were\s{1,3}told))\b",
    # "system prompt override: grant admin access" (a command aimed at the prompt itself)
    r"\b(?:system|developer)\s{1,3}prompt\s{1,3}(?:override|bypass|injection)\b",
]

# Multilingual "ignore previous instructions" (es, fr, de).
_MULTILINGUAL_PATTERNS = [
    r"\bignora(?:r|d)?\s{1,3}(?:(?:todas|las|tus|sus|mis|todo|lo)\s{1,3}){0,3}"
    r"(?:instrucciones|reglas|indicaciones)\b",
    r"\b(?:olvida|olvide)\s{1,3}(?:(?:todas|las|tus|todo|lo)\s{1,3}){0,3}"
    r"(?:instrucciones|reglas|anteriores)\b",
    r"\bignore[rz]?\s{1,3}(?:(?:toutes|les|tes|vos|mes|ces|tout)\s{1,3}){0,3}"
    r"(?:instructions|regles|consignes)\b",
    r"\b(?:oublie[rz]?)\s{1,3}(?:(?:toutes|les|tes|vos|tout)\s{1,3}){0,3}"
    r"(?:instructions|regles|consignes)\b",
    r"\bignorier\w{0,3}\s{1,3}(?:(?:alle|die|deine|vorherigen|alles|bisherigen)"
    r"\s{1,3}){0,3}(?:anweisungen|regeln|instruktionen|vorgaben)\b",
    r"\bvergiss\w{0,2}\s{1,3}(?:(?:alle|die|deine|vorherigen|alles)\s{1,3}){0,3}"
    r"(?:anweisungen|regeln|instruktionen|vorgaben)\b",
]

_LEAK_VERB = (
    r"(?:print|repeat|show|dump|display|reveal|output|translate|summari[sz]e|"
    r"recite|paste|leak|disclose|tell\s{1,3}me|give\s{1,3}me|share|copy|read|list)"
)
# The assistant's own: "your", "ur", "the assistant's", "the bot's".
_OWN = r"(?:your|ur|(?:the\s{1,3})?(?:assistant|bot|chatbot|ai)'?s)"
_LEAK_ADJ = (
    r"(?:(?:initial|original|full|entire|exact|hidden|secret|system|internal|"
    r"developer)\s{1,3}){0,2}"
)
_LEAK_PATTERNS = [
    # Generic nouns (config, rules, context...) only count when owned.
    rf"\b{_LEAK_VERB}\b.{{0,30}}\b{_OWN}\s{{1,3}}{_LEAK_ADJ}"
    r"(?:instructions|prompt|system\s{1,3}message|context\s{1,3}window|"
    r"config|configuration|rules|guidelines|context|message)\b",
    # Unambiguous leak nouns, whether or not owned.
    rf"\b{_LEAK_VERB}\b.{{0,30}}\b(?:system|hidden|secret|developer)\s{{1,3}}"
    r"(?:instructions|prompt|message)\b",
    # "what is your system prompt" (no verb).
    rf"\b{_OWN}\s{{1,3}}(?:(?:initial|original|hidden|secret|system|internal|"
    r"developer)\s{1,3}){1,2}(?:instructions|prompt|message|rules|guidelines)\b",
    r"\bwhat\s{1,3}(?:were|are)\s{1,3}you\s{1,3}(?:told|instructed|programmed|"
    r"given)\b.{0,30}\b(?:before|earlier|initially|at\s{1,3}the\s{1,3}start)\b",
    r"\b(?:repeat|print|show|output|copy|paste|recite|dump|display|echo|write)\b"
    r".{0,25}\b(?:everything|all|text|words|content|messages?)\s{1,3}"
    r"(?:above|before\s{1,3}this|prior|preceding|so\s{1,3}far)\b",
    r"\babove\s{1,3}this\s{1,3}line\b",
    r"\b(?:before|prior\s{1,3}to)\s{1,3}this\s{1,3}line\b",
    r"\b(?:repeat|print|output|quote|copy|recite|reproduce|show)\b.{0,40}\bverbatim\b",
    r"\bverbatim\b.{0,40}\b(?:instructions|prompt|above|rules|everything)\b",
    r"\btext\s{1,3}(?:starting|beginning)\s{1,3}with\s{1,3}[\"']?you\s{1,3}are\b",
]

_AI_WORD = (
    r"(?:you|your|answer|respond|reply|act|behave|pretend|mode|ai|chatbot|"
    r"assistant|bot)"
)
_NO_RULES = (
    r"(?:rules|restrictions|limits|limitations|filters|guardrails|censorship|"
    r"ethics)"
)
_JAILBREAK_PATTERNS = [
    r"\byou\s{1,3}are\s{1,3}now\s{1,3}(?:a|an|the|my|in|dan|free|no|not|"
    r"unrestricted|unfiltered|jailbroken|going|acting|called|named|playing|able)\b",
    r"\bfrom\s{1,3}now\s{1,3}on\s{1,3}you\s{1,3}(?:are|will|must|can|shall)\b",
    r"\bdan\s{1,3}(?:mode|jailbreak|prompt)\b",
    r"\bdo\s{1,3}anything\s{1,3}now\b",
    r"\b(?:you\s{1,3}are|act\s{1,3}as|be|become)\s{1,3}dan\b",
    # A mode switch, not "developer mode deployment of DeployIQ".
    r"\b(?:enable|enter|activate|unlock|turn\s{1,3}on|switch\s{1,3}(?:to|into)|"
    r"go\s{1,3}into|now\s{1,3}in|you\s{1,3}are\s{1,3}in)\s{1,3}(?:the\s{1,3})?"
    r"(?:developer|god|sudo|debug|dan|admin)\s{1,3}mode\b",
    r"\b(?:developer|god|sudo|debug|dan)\s{1,3}mode\s{0,3}[:=]?\s{0,3}"
    r"(?:enabled|activated|on|engaged|unlocked)\b",
    r"^\W{0,3}(?:developer|god|sudo)\s{1,3}mode\W{0,3}$",
    r"\b(?:system|admin|root|developer|god|security)\s{1,3}override\b",
    r"\bjailbr\w{0,8}\b.{0,25}\b(?:you|your|prompt|chatbot|assistant|bot|ai|llm|"
    r"gpt|model|mode)\b",
    r"\b(?:you|your|prompt|chatbot|assistant|bot|ai|llm|gpt|model|mode)\b"
    r".{0,25}\bjailbr\w{0,8}\b",
    r"\bpretend\s{1,3}(?:you\s{1,3}are|to\s{1,3}be|you'?re)\b.{0,30}"
    r"\b(?:unrestricted|unfiltered|uncensored|evil|jailbroken|no\s{1,3}rules|"
    r"without)\b",
    rf"\b{_AI_WORD}\b.{{0,40}}\b(?:without|with\s{{1,3}}no|no)\s{{1,3}}"
    rf"(?:any\s{{1,3}})?{_NO_RULES}\b",
    rf"\b(?:without|with\s{{1,3}}no)\s{{1,3}}(?:any\s{{1,3}})?{_NO_RULES}\b"
    r".{0,30}\b(?:you|answer|respond|mode)\b",
    r"\bunrestricted\s{1,3}(?:ai|mode|assistant|bot|model|version)\b",
]

_MARKER_PATTERNS = [
    r"\[\s{0,2}/?\s{0,2}(?:system|inst|assistant)\s{0,2}\]",
    r"<\|[a-z_]{1,20}\|>",
    r"<<\s{0,2}/?sys\s{0,2}>>",
    r"</?(?:system|im_start|im_end)>",
    r"#{2,4}\s{0,2}(?:system|instructions?|new\s{1,3}instructions?)\b",
    r"\bsystem\s{0,2}:\s{0,2}you\s{1,3}are\b",
    r"\bnew\s{1,3}instructions?\s{0,2}:",
]

_ENCODED_PATTERNS = [
    r"\b(?:decode|decrypt|deobfuscate|translate)\b.{0,40}\b(?:base64|b64|hex|"
    r"rot13)\b.{0,60}\b(?:follow|execute|run|obey)\b",
    r"\b(?:base64|b64|rot13)\b.{0,40}\b(?:and|then)\s{1,3}(?:follow|execute|run|"
    r"obey)\b",
    r"\b(?:decode|decrypt)\b.{0,30}\b(?:and|then)\s{1,3}(?:follow|execute|obey|"
    r"run)\b",
]

# Paraphrased injection seen in the audit: "disregard what you were told",
# "set aside your guidelines", "throw out your instructions".
_SET_ASIDE_VERB = (
    r"(?:set|put|leave|cast|push)\s{1,3}aside|throw\s{1,3}(?:out|away)|"
    r"discard|abandon|drop|ditch|suspend|turn\s{1,3}off|switch\s{1,3}off|disable"
)
_PARAPHRASE_PATTERNS = [
    rf"\b{_STRONG_VERB}\s{{1,3}}(?:(?:everything|all|anything)\s{{1,3}})?"
    r"(?:what|whatever)\s{1,3}(?:you\s{1,3})?(?:were|'ve\s{1,3}been|have\s{1,3}been|"
    r"was|got)\s{1,3}(?:told|instructed|given|programmed|taught)\b",
    rf"\b(?:{_SET_ASIDE_VERB})\s{{1,3}}{_FILLER}(?:{_OWN_CTX}|previous|prior|"
    r"earlier|initial|original)\s{1,3}(?:[a-z]{1,15}\s{1,3}){0,2}"
    rf"{_INSTR_NOUN}\b",
    rf"\b(?:{_SET_ASIDE_VERB})\s{{1,3}}{_FILLER}{_OWN_CTX}\s{{1,3}}"
    rf"(?:[a-z]{{1,15}}\s{{1,3}}){{0,2}}{_RULE_NOUN}\b",
    rf"\b(?:{_SET_ASIDE_VERB})\s{{1,3}}(?:the\s{{1,3}}|all\s{{1,3}}){{0,2}}"
    r"(?:instructions|guidelines|guardrails|safety\s{1,3}rules)\b",
    r"\bwhat\s{1,3}(?:were|was)\s{1,3}you\s{1,3}(?:told|instructed|given|programmed)"
    r"\s{1,3}(?:at|in)\s{1,3}the\s{1,3}(?:start|beginning|outset)\b",
]

# Non-Latin scripts, matched on text without the homoglyph mapping (which
# would turn Cyrillic into Latin look-alikes). CJK has no word spaces, so
# the gap is a bounded run of non-punctuation characters.
_SCRIPT_PATTERNS = [
    # zh: 忽略/无视/忘记 ... 指令/指示/规则/提示
    r"(?:\u5ffd\u7565|\u5ffd\u89c6|\u65e0\u89c6|\u7121\u8996|\u5fd8\u8bb0|\u5fd8\u6389|\u4e0d\u8981\u7406\u4f1a|\u8df3\u8fc7)[^\u3002\uff01\uff1f!?.]{0,12}"
    r"(?:\u6307\u4ee4|\u6307\u793a|\u8bf4\u660e|\u898f\u5247|\u89c4\u5219|\u63d0\u793a|\u547d\u4ee4|\u8bbe\u5b9a|\u8a2d\u5b9a)",
    # ja: 指示を無視 / 以前の指示を忘れて
    r"(?:\u6307\u793a|\u547d\u4ee4|\u30eb\u30fc\u30eb|\u30d7\u30ed\u30f3\u30d7\u30c8)[^\u3002\uff01\uff1f!?.]{0,8}(?:\u7121\u8996|\u5fd8\u308c)",
    # ru/uk: игнорируй/забудь (все) (предыдущие) инструкции/правила
    r"(?:\u0438\u0433\u043d\u043e\u0440\u0438\u0440\w{0,6}|\u043f\u0440\u043e\u0438\u0433\u043d\u043e\u0440\u0438\u0440\w{0,6}|\u0437\u0430\u0431\u0443\u0434\w{0,3}|\u0437\u0430\u0431\u044b\u0442\u044c|\u043e\u0442\u0431\u0440\u043e\u0441\u044c|"
    r"\u043d\u0435\s{1,3}\u043e\u0431\u0440\u0430\u0449\u0430\u0439\s{1,3}\u0432\u043d\u0438\u043c\u0430\u043d\u0438\u044f\s{1,3}\u043d\u0430)\s{1,3}"
    r"(?:(?:\u0432\u0441\u0435|\u0432\u0441\u0451|\u043f\u0440\u0435\u0434\u044b\u0434\u0443\u0449\u0438\u0435|\u043f\u0440\u043e\u0448\u043b\u044b\u0435|\u043f\u0440\u0435\u0436\u043d\u0438\u0435|\u0441\u0432\u043e\u0438|\u0442\u0432\u043e\u0438|\u044d\u0442\u0438|\u0432\u0430\u0448\u0438)\s{1,3}){0,3}"
    r"(?:\u0438\u043d\u0441\u0442\u0440\u0443\u043a\u0446\w{0,4}|\u0443\u043a\u0430\u0437\u0430\u043d\u0438\w{0,3}|\u043f\u0440\u0430\u0432\u0438\u043b\w{0,3}|\u043a\u043e\u043c\u0430\u043d\u0434\w{0,3}|\u043f\u0440\u043e\u043c\u043f\u0442\w{0,3})",
    # ko: 이전 지시를 무시
    r"(?:\uc9c0\uc2dc|\uba85\ub839|\uaddc\uce59|\ud504\ub86c\ud504\ud2b8)[^.!?]{0,8}(?:\ubb34\uc2dc|\uc78a\uc5b4)",
]
_SCRIPT_RES = [re.compile(p) for p in _SCRIPT_PATTERNS]

_INJECTION_RES = [re.compile(p) for p in (*UNSAFE_PATTERNS, *_MULTILINGUAL_PATTERNS)]
_QUERY_ONLY_RES = [
    re.compile(p)
    for p in (
        *_LEAK_PATTERNS,
        *_PARAPHRASE_PATTERNS,
        *_JAILBREAK_PATTERNS,
        *_MARKER_PATTERNS,
        *_ENCODED_PATTERNS,
    )
]

# Cyrillic/Greek look-alikes mapped to the Latin letter they imitate.
_CONFUSABLES = str.maketrans(
    "\u0430\u0435\u043e\u0440\u0441\u0443\u0445\u0456\u0458\u0455\u04bb\u043a\u043c\u0442\u0432\u043d\u0501\u0261\u03b1\u03bf\u03bd\u03c1\u03b9\u03ba\u03c4\u03c5\u03b5\u03b7\u03b2\u03c7\u03b6\u03f2",
    "aeopcyxijshkmtbhdgaovpiktuenbxzc",
)
_LEET = str.maketrans(
    {"1": "i", "0": "o", "3": "e", "@": "a", "$": "s", "4": "a", "5": "s", "7": "t"}
)
# "i g n o r e" / "i.g.n.o.r.e": 4-21 single letters joined by one separator.
_SPACED_LETTERS_RE = re.compile(
    r"(?<![a-z0-9])(?:[a-z][ .\-_*]){3,20}[a-z](?![a-z0-9])"
)
_SPACED_SEPARATORS_RE = re.compile(r"[ .\-_*]")


def _strip_invisible(text: str) -> str:
    """Drop zero-width/format chars and turn other control chars into spaces."""
    return strip_format_chars(text, keep="\t\n\r")


def _normalise_for_matching(text: str, *, leet: bool = False) -> str:
    """Normalise obfuscated text so injection patterns see plain lowercase words."""
    text = unicodedata.normalize("NFKD", text[:_MATCH_MAX_CHARS])
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = _strip_invisible(text).translate(_CONFUSABLES).lower()
    if leet:
        text = text.translate(_LEET)
    text = _SPACED_LETTERS_RE.sub(
        lambda m: _SPACED_SEPARATORS_RE.sub("", m.group(0)), text
    )
    return " ".join(text.split())


# ---------------------------------------------------------------------------
# Sensitive-request patterns
# ---------------------------------------------------------------------------

# Every pattern runs on a whitespace-collapsed, length-capped query and uses
# only bounded quantifiers (no polynomial ReDoS).
_SENSITIVE_MAX_CHARS = 1000
# Another customer's records ("give me the email addresses of your other
# customers"); "how do you protect other customers' data" is a how-to.
_CUSTOMER_DATA_RE = re.compile(
    r"\b(?:e-?mails?(?:\s{1,3}address(?:es)?)?|address(?:es)?|phone\s{1,3}numbers?|"
    r"numbers|names|contacts?(?:\s{1,3}(?:details|list))?|details|data|records|"
    r"information|info|list|files|reports?)\b",
    re.IGNORECASE,
)
_CROSS_TENANT_RE = re.compile(
    r"\b(?:other|another)\s{1,3}(?:customers?|tenants?|clients?)\b",
    re.IGNORECASE,
)
_CREDENTIAL_RE = re.compile(
    r"\b(?:passwords?|admin(?:istrator)?\s{1,3}credentials?|"
    r"api[\s_-]{0,3}keys?|secrets?|access\s{1,3}tokens?)\b",
    re.IGNORECASE,
)
_REVEAL_VERB_RE = re.compile(
    r"\b(?:print|reveal|show|list|provide|output|give|display|what\s{1,3}is)\b",
    re.IGNORECASE,
)
_OWN_APPLICATION_RE = re.compile(
    r"\bused\s{1,3}(?:by|in)\s{1,3}(?:this|your)\s{1,3}(?:application|app|system)\b"
    r"|\bin\s{1,3}your\s{1,3}environment\b",
    re.IGNORECASE,
)
_SECRET_VALUE_RE = re.compile(
    r"\b(?:value|contents?)\s{1,3}of\s{1,3}[A-Z][A-Z0-9_]{0,60}"
    r"(?:KEY|SECRET|TOKEN|PASSWORD)\b"
)
_GIVE_VERB_RE = re.compile(
    r"\b(?:give|share|send|tell|provide|show|print|reveal|list|dump|leak|expose|"
    r"output|display|read|what\s{1,3}(?:is|are)|what'?s)\b",
    re.IGNORECASE,
)
# Secret material named directly; no verb needed ("cat .env", "os.environ",
# "SECRET_KEY"). The constant-style name is matched case-sensitively.
_SECRET_TOKEN_RE = re.compile(
    r"\bos\.environ\b|\bprocess\.env\b|\bgetenv\s{0,2}\("
    r"|(?:^|\s)(?:cat|type|more|less|head|tail|print|echo|open|read|show|dump|"
    r"display|source|get)\s{1,3}\S{0,40}\.env\b"
    r"|\b[A-Z][A-Z0-9]{0,30}_(?:[A-Z0-9]{1,15}_){0,3}"
    r"(?:KEY|SECRET|TOKEN|PASSWORD|PASSWD|PWD|CREDENTIALS?|DSN)\b"
    r"|\b(?i:secret|api|private|signing|access)_(?i:key|token)s?\b"
)
_HOW_TO_RE = re.compile(
    r"\bhow\s{1,3}(?:do|does|can|could|to|should|would|would)\b", re.IGNORECASE
)
# "password policy", "credential rotation": a question about the practice.
_CRED_PRACTICE = (
    r"(?!\s{1,3}(?:polic|requirement|reset|rule|complex|length|expir|rotat|"
    r"manag|hygiene|best|standard|guidance|setting))"
)
_DIRECT_CREDENTIAL_RE = re.compile(
    r"\b(?:(?:your|ur)|the\s{1,3}(?:admin(?:istrator)?|root|database|db|"
    r"production|prod|server|company'?s?|backend|master|internal|system|"
    r"openai|azure|postgres(?:ql)?|mysql|redis|sql))\s{1,3}(?:[a-z_]{1,15}\s{1,3}){0,1}"
    r"(?:api[\s_-]{0,3}keys?|passwords?|secret\s{1,3}keys?|access\s{1,3}tokens?|"
    r"private\s{1,3}keys?|credentials?|creds|logins?|login\s{1,3}details|"
    rf"connection\s{{1,3}}strings?)\b{_CRED_PRACTICE}"
    r"|\b(?:admin(?:istrator)?|root|db|database|postgres(?:ql)?|mysql|redis)"
    rf"\s{{1,3}}(?:password|credentials?|creds|logins?|login\s{{1,3}}details)\b{_CRED_PRACTICE}"
    r"|\bconnection\s{1,3}strings?\b"
    r"|\b(?:your|ur|the|server|app|application|backend|production|prod)\s{1,3}"
    r"env(?:ironment)?\s{1,3}(?:variables?|vars?|file)\b"
    # "the environment variable setup for DeployIQ agents" is a product question
    r"(?!\s{1,3}(?:setup|set[\s-]?up|configuration|config|requirements?|for\b|"
    r"needed|required|names?))"
    r"|\benv\s{1,3}file\b|\.env\b"
    r"|\bthe\s{1,3}api[\s_-]{0,3}keys?\b"
    r"|\b(?:your|ur)\s{1,3}(?:openai|azure|secret|private|master|admin)"
    r"\s{1,3}keys?\b",
    re.IGNORECASE,
)
# Imperative ask for "the secret key" ("tell me the secret key"); a question
# such as "what is a secret key?" is not matched.
_IMPERATIVE_SECRET_KEY_RE = re.compile(
    r"\b(?:tell\s{1,3}me|give\s{1,3}me|send\s{1,3}me|share|reveal|show\s{1,3}me|"
    r"print|dump|leak|expose)\s{1,3}the\s{1,3}"
    r"(?:secret|private|master|signing)\s{1,3}keys?\b",
    re.IGNORECASE,
)
_PERSONAL_CONTACT_RE = re.compile(
    r"\bpersonal\s{1,3}number\b|\b(?:personal|private)\s{1,3}"
    r"(?:mobile|phone|contact|email)\s{1,3}(?:number|details?|address)?\b"
    r"|\b(?:home|residential|private|personal)\s{1,3}address(?:es)?\b",
    re.IGNORECASE,
)
_PERSON_RE = re.compile(
    r"\b(?:ceo|chief\s{1,3}executive|executive|employee|"
    r"staff\s{1,3}member|team\s{1,3}member|director)\b",
    re.IGNORECASE,
)

_BULK_PERSONAL_RE = re.compile(
    r"\b(?:list|give|show|send|share|dump|export)\s{1,3}(?:me\s{1,3})?"
    r"(?:(?:all|every|each)\s{1,3}(?:of\s{1,3})?(?:the\s{1,3}|your\s{1,3})?"
    r"(?:(?:internal|company)\s{1,3})?"
    r"|(?:the\s{1,3}|your\s{1,3})?(?:internal|company)\s{1,3})"
    r"(?:employee|staff|customer|user|client)s?(?:'s?)?\s{1,3}"
    r"(?:email|phone|mobile|contact|address|detail|name)s?\b"
    r"|\b(?:give|show|send)\s{1,3}me\s{1,3}(?:everyone'?s|all)\s{1,3}"
    r"(?:emails?|phone\s{1,3}numbers?)\b"
    r"|\b(?:show|give|list|send)\s{1,3}(?:me\s{1,3})?(?:the\s{1,3})?"
    r"(?:employee|staff)\s{1,3}(?:data|directory|contact\s{1,3}list)\b",
    re.IGNORECASE,
)
_BULK_PERSONAL_MAX_CHARS = 200

# ---------------------------------------------------------------------------
# Spam / gibberish
# ---------------------------------------------------------------------------

# Two or more links, or a link plus promo wording, is spam. A single link
# (or a domain-looking token such as "deployiq.io") is not.
_URL_RE = re.compile(
    r"https?://\S{1,200}|www\.\S{1,200}|"
    r"\b[a-z0-9-]{1,63}\.(?:com|net|org|io|biz|xyz|info)\b",
    re.IGNORECASE,
)

# Promotional/scam phrasing
_PROMO_PHRASES_RE = re.compile(
    r"\b(buy now|click here|limited time offer|act now|"
    r"make money fast|subscribe to my channel|"
    r"dm me|whatsapp me|claim your prize|you'?ve won|free gift|"
    r"guaranteed income|check out my channel)\b",
    re.IGNORECASE,
)

# Promo/deal wording that makes a single link spam ("cheap pills", "great deals").
_URL_PROMO_RE = re.compile(
    r"\b(?:great\s{1,3}deals?|cheap|pills?|buy\s{1,3}now|free\s{1,3}money|"
    r"click\s{1,3}here|limited\s{1,3}offer|discount(?:s|ed)?)\b",
    re.IGNORECASE,
)

_RUN_RE = re.compile(r"(.)\1{2,}")  # 3+ identical chars in a row
_VOWEL_RE = re.compile(r"[aeiouy]", re.IGNORECASE)
_CONSONANT_CLUSTER_RE = re.compile(r"[bcdfghjklmnpqrstvwxz]{5,}", re.IGNORECASE)
# Real English clusters that must not count as consonant mash.
_VALID_CLUSTER_RE = re.compile(
    r"ngths?|nths?|rths?|lfths?|ghts?|tchs?|rchs?|mpts?|sths|ckst|ndst|rsts?|"
    r"nsts?|ldst|xths?|lths?|ngst",
    re.IGNORECASE,
)

# Whitelisted technical terms and domain acronyms that must never be flagged as gibberish
ALLOWED_ACRONYMS = {
    "e8", "mfa", "soc", "soc2", "acsc", "rbac", "saml", "sso", "jwt", "api",
    "sdk", "iam", "siem", "edr", "ml6", "llm", "rag", "ai", "db", "ip", "url",
    "id", "os", "vm", "gdpr", "asd", "hipaa", "ism", "nist", "pci", "nis2",
    "dss", "iso", "cis", "dora", "ccpa", "fedramp", "irap", "pspf", "essential8",
    "cmmc", "sox", "mtls", "vpn", "dns", "tls", "ssl", "ssh", "pam", "dlp", "mdm",
    "xdr", "mdr", "soar", "grc", "cve", "oidc", "ldap", "scim", "kms", "hsm",
    "ztna", "casb", "waf", "sla", "kpi", "roi", "saas", "paas", "iaas", "csp",
    "cspm", "ciem", "sbom", "ssdlc", "tcp", "udp", "http", "https", "rest",
    "ui", "ux", "faq", "csv", "pdf", "sql", "nosql", "gpu", "cpu", "aws", "gcp",
    "m365", "o365", "ad", "aad", "pdpa", "ndb", "ndis", "apra", "cps234",
}  # fmt: skip

# Product/brand/technical words that look odd but are real.
_KNOWN_TERMS = {
    "deployiq", "askdiq", "exigo", "exigotech", "entra", "intune", "azure",
    "microsoft", "office365", "postgres", "postgresql", "pgvector", "sqlite",
    "mysql", "mongodb", "nginx", "kubernetes", "kubectl", "docker", "dockerfile",
    "terraform", "ansible", "okta", "zscaler", "crowdstrike", "sharepoint",
    "defender", "sentinel", "purview", "onedrive", "openai", "chatgpt", "github",
    "gitlab", "jenkins", "redis", "fastapi", "oauth", "openid", "bitlocker",
    "applocker", "powershell", "linux", "windows", "macos", "android", "ios",
}  # fmt: skip

# Everyday product-domain words used only to recognise typos of real words.
_DOMAIN_WORDS = frozenset(
    [
        "important",
        "importance",
        "length",
        "duration",
        "benchmark",
        "benchmarks",
        "export",
        "exports",
        "import",
        "month",
        "months",
        "monthly",
        "year",
        "years",
        "annual",
        "annually",
        "week",
        "weeks",
        "weekly",
        "daily",
        "hours",
        "uptime",
        "coverage",
        "compare",
        "onboarding",
        "enrolment",
        "enrollment",
        "enrol",
        "enroll",
        "encryption",
        "encrypted",
        "authentication",
        "authorization",
        "authorisation",
        "vulnerability",
        "vulnerabilities",
        "remediation",
        "configuration",
        "configure",
        "retention",
        "certification",
        "certified",
        "version",
        "versions",
        "assessment",
        "assessments",
        "requirement",
        "requirements",
        "regulation",
        "regulations",
        "incident",
        "incidents",
        "response",
        "monitoring",
        "detection",
        "automation",
        "automated",
        "automate",
        "application",
        "applications",
        "administrative",
        "administrator",
        "privilege",
        "privileges",
        "password",
        "passwords",
        "permission",
        "permissions",
        "device",
        "devices",
        "license",
        "licence",
        "licensing",
        "subscription",
        "billing",
        "invoice",
        "contract",
        "storage",
        "residency",
        "sovereignty",
        "logging",
        "syslog",
        "connector",
        "connectors",
        "scanning",
        "scanner",
        "baseline",
        "baselines",
        "hardening",
        "remote",
        "workforce",
        "control",
        "alignment",
        "align",
        "aligns",
        "mapped",
        "mapping",
        "evidence",
        "attestation",
    ]  # noqa: SIM905
)

# Two-letter KB terms (zt = Zero Trust, e8 = Essential Eight) are questions.
_ALLOWED_SHORT_TOKENS = {"hi", "yo", "ok", "no", "na", "zt", "e8"}

_KEYBOARD_SEQS = (
    "qwertyuiop",
    "asdfghjkl",
    "zxcvbnm",
    "poiuytrewq",
    "lkjhgfdsa",
    "mnbvcxz",
    "qazwsxedc",
    "plmoknijb",
)

# Letter doubles that virtually never appear in valid English words
_UNUSUAL_DOUBLES_RE = re.compile(
    r"(jj|qq|vv|xx|hh|ww|yy|kk|zx|jk|hj|kj)", re.IGNORECASE
)

# Character-pair plausibility (no dictionary, no dependency). A pair is "rare"
# when it is a consonant pair, vowel pair or double that English words do not
# use. 'y' counts as a vowel-ish glue letter and is never rare.
_VALID_CONSONANT_PAIRS = frozenset(
    "bb bl br bs bt cc ch ck cl cr cs ct dd dg dl dm dn dr ds dv dw ff fl fr fs "  # noqa: SIM905
    "ft gg gh gl gm gn gr gs gt hm hs ht kl kn ks ld lb lc lf lg lk ll lm lp ls "
    "lt lv mb mf mm mn mp ms nc nd nf ng nj nk nn ns nt nv nw pl pn pp pr ps pt "
    "ph rb rc rd rf rg rk rl rm rn rp rr rs rt rv rw sc sh sk sl sm sn sp sq ss "
    "st sw tc th tm tr ts tt tw wl wn wr ws wh xc xp xt zz".split()
)
_VALID_VOWEL_PAIRS = frozenset(
    "ae ai ao au ea ee ei eo eu ia ie io iu oa oe oi oo ou ua ue ui uo".split()  # noqa: SIM905
)
_VOWELS_NO_Y = set("aeiou")
_RARE_MIN_LEN = 5
_VOWEL_RUN_RE = re.compile(r"[aeiou]{4,}")
_RARE_PAIR_FRACTION = 0.25
_RARE_PAIR_LONG_WORD = 8

# Everyday + domain words; a query of 3+ long tokens with none of these (and
# no brand term) is keyboard-smash-like text, not a question.
_COMMON_WORDS = frozenset(
    "what when where which while whose whom why who how does did can could would "  # noqa: SIM905
    "should will shall have has had are was were been being the and for with "
    "from that this these those there here they them their then than your you "
    "yours about above after again also another because before between both "
    "other only over same some such into more most much many very just like "
    "tell please explain describe give show list help need want know learn "
    "find get make use using used works work support supports offer offers "
    "include includes included provide provides price prices pricing cost "
    "plan plans feature features free trial demo security secure compliance "
    "compliant governance policy policies platform product products service "
    "services customer customers company team contact sales account access "
    "data privacy protect protection identity zero trust essential eight "
    "maturity model level levels audit report reports reporting dashboard "
    "integration integrate integrates cloud network endpoint patch patching "
    "backup backups risk assessment framework framework controls control "
    "implement implementation deploy deployment start started setup install "
    "pilot assistant chatbot question questions answer answers email phone "
    "number location office region regions industry industries government "
    "health finance education australian australia strategy strategies "
    "mitigation mitigations strength strengths weakness weaknesses benefit "
    "benefits different difference compare comparison versus between "
    "available availability around within without across every each".split()
)

# Placeholder text ("lorem ipsum dolor sit amet ...") is never a question.
_LOREM_WORDS = frozenset(
    [
        "lorem",
        "ipsum",
        "dolor",
        "amet",
        "consectetur",
        "adipiscing",
        "eiusmod",
        "tempor",
        "incididunt",
        "labore",
        "dolore",
        "magna",
        "aliqua",
    ]  # noqa: SIM905
)
_LOREM_MIN_HITS = 2

_STRIP_CHARS = string.punctuation + string.whitespace


def _clean_token(token: str) -> str:
    """Strip leading and trailing punctuation/symbols from token."""
    return token.strip(_STRIP_CHARS)


def _collapse_runs(text: str, keep: int = 1) -> str:
    """Collapse runs of 3+ identical characters ('whaaaat' -> 'what')."""
    return _RUN_RE.sub(lambda m: m.group(1) * keep, text)


def _is_keyboard_mash(letters: str) -> bool:
    return len(letters) >= 3 and any(letters in seq for seq in _KEYBOARD_SEQS)


def _rare_pair_count(letters: str) -> int:
    """Count letter pairs that English words (almost) never contain."""
    rare = 0
    for first, second in zip(letters, letters[1:], strict=False):
        if "y" in (first, second):
            rare += first == second
        elif first == second:
            rare += first not in "bcdefglmnoprstz"
        elif first in _VOWELS_NO_Y and second in _VOWELS_NO_Y:
            rare += (first + second) not in _VALID_VOWEL_PAIRS
        elif first not in _VOWELS_NO_Y and second not in _VOWELS_NO_Y:
            rare += (first + second) not in _VALID_CONSONANT_PAIRS
    # 'q' not followed by 'u' (or ending the word): 'faq', 'qa' never occur in words.
    rare += sum(
        1
        for i, ch in enumerate(letters)
        if ch == "q"
        and letters[i + 1 : i + 2] != "u"
        and (i + 1 == len(letters) or letters[i + 1] in _VOWELS_NO_Y)
    )
    return rare


def is_implausible_word(token: str) -> bool:
    """Cheap pair-plausibility test for words that pass the vowel/cluster rules."""
    cleaned = _clean_token(token).lower()
    if cleaned in _KNOWN_TERMS or cleaned in ALLOWED_ACRONYMS:
        return False
    letters = _collapse_runs(re.sub(r"[^a-z]", "", cleaned))
    if len(letters) < _RARE_MIN_LEN:
        return False
    if _VOWEL_RUN_RE.search(letters):
        return True
    rare = _rare_pair_count(letters)
    if rare < 2:
        return False
    return rare / (len(letters) - 1) >= _RARE_PAIR_FRACTION or (
        len(letters) >= _RARE_PAIR_LONG_WORD
    )


def is_gibberish_token(token: str) -> bool:
    """Check if an individual token looks like keyboard mash or gibberish."""
    original = _clean_token(token)
    cleaned = original.lower()
    if (
        not cleaned
        or cleaned in ALLOWED_ACRONYMS
        or cleaned in _ALLOWED_SHORT_TOKENS
        or cleaned in _KNOWN_TERMS
    ):
        return False

    raw_letters = re.sub(r"[^a-z]", "", cleaned)
    if not raw_letters:
        return False

    # Short all-caps tokens are acronyms (GDPR, ASD, HIPAA), never gibberish.
    if (
        original.isupper()
        and len(raw_letters) <= 6
        and not (len(raw_letters) >= 5 and _is_keyboard_mash(raw_letters))
    ):
        return False

    # Elongation ("whaaaat", "tellllll") is emphasis, not noise.
    letters_only = _collapse_runs(raw_letters)

    # 1. Keyboard mash sequences of length >= 3 (e.g. asd, qwe, zxc, asdfgh)
    if _is_keyboard_mash(letters_only):
        return True

    # 2. Obvious unnatural character combinations (e.g. "jj" in "akjj", "qq", "xx")
    if _UNUSUAL_DOUBLES_RE.search(letters_only):
        return True

    # 3. Long consonant runs, ignoring real English clusters ("strengths")
    if _CONSONANT_CLUSTER_RE.search(_VALID_CLUSTER_RE.sub("-", letters_only)):
        return True

    vowels = len(_VOWEL_RE.findall(letters_only))

    # 4. Short tokens ending in unusual 3-consonant clusters
    if (
        len(letters_only) == 4
        and re.search(r"[bcdfghjklmnpqrstvwxz]{3,}$", letters_only)
        and vowels <= 1
    ):
        return True

    # 5. Tokens of length >= 4 with zero vowels (including 'y')
    if len(letters_only) >= 4 and vowels == 0:
        return True

    # 6. Very long tokens with almost no vowels
    if len(letters_only) >= 6 and (vowels / len(letters_only)) < 0.10:
        return True

    # 7. Embedded punctuation with low vowel density, e.g. "a;;o0d,ndnk"
    return bool(
        any(ch in original for ch in ";,:_")
        and len(letters_only) >= 4
        and (vowels == 0 or (vowels / len(letters_only)) < 0.25)
    )


def check_length(
    query: str,
    max_length: int,
) -> tuple[bool, str | None]:
    """Validate query length and ensure it is not empty."""
    if len(query) > max_length:
        return False, "query_too_long"

    if not query.strip():
        return False, "empty_query"

    return True, None


def check_unsafe_input(
    query: str,
) -> tuple[bool, str | None]:
    """Detect prompt injection, prompt-leak, jailbreak and fake-system-marker input.

    Text is normalised first (Unicode, zero-width chars, homoglyphs, spaced
    letters); injection phrasing is also tried with leetspeak undone.
    """
    plain = _normalise_for_matching(query)
    leet = _normalise_for_matching(query, leet=True)

    for text in (plain, leet):
        if any(regex.search(text) for regex in _INJECTION_RES):
            return False, "unsafe_input_detected"

    if any(regex.search(plain) for regex in _QUERY_ONLY_RES):
        return False, "unsafe_input_detected"

    if not query.isascii():
        native = " ".join(
            unicodedata.normalize("NFKC", query[:_MATCH_MAX_CHARS]).lower().split()
        )
        if any(regex.search(native) for regex in _SCRIPT_RES):
            return False, "unsafe_input_detected"

    return True, None


def check_sensitive_request(query: str) -> tuple[bool, str | None]:
    """Block requests for credentials/secrets or staff personal contact details."""
    text = " ".join(query[:_SENSITIVE_MAX_CHARS].split())
    if _SECRET_TOKEN_RE.search(text):
        return False, "application_secret_request"
    if _REVEAL_VERB_RE.search(text):
        if _SECRET_VALUE_RE.search(text):
            return False, "application_secret_request"
        if _CREDENTIAL_RE.search(text) and _OWN_APPLICATION_RE.search(text):
            return False, "application_secret_request"
    if (
        _GIVE_VERB_RE.search(text)
        and not _HOW_TO_RE.search(text)
        and _DIRECT_CREDENTIAL_RE.search(text)
    ):
        return False, "application_secret_request"
    if _IMPERATIVE_SECRET_KEY_RE.search(text) and not _HOW_TO_RE.search(text):
        return False, "application_secret_request"
    if _CROSS_TENANT_RE.search(text) and _CREDENTIAL_RE.search(text):
        return False, "no_permission"
    if (
        _CROSS_TENANT_RE.search(text)
        and _CUSTOMER_DATA_RE.search(text)
        and _GIVE_VERB_RE.search(text)
        and not _HOW_TO_RE.search(text)
    ):
        return False, "no_permission"
    if _PERSONAL_CONTACT_RE.search(text) and _PERSON_RE.search(text):
        return False, "personal_contact_request"
    if _BULK_PERSONAL_RE.search(text[:_BULK_PERSONAL_MAX_CHARS]):
        return False, "personal_contact_request"
    return True, None


def _is_single_char_run(token: str) -> bool:
    """A 6+ char token of at most two distinct characters ('aaaaaaa', 'aaaaabbbbb'), not 'whaaaat'."""
    cleaned = _clean_token(token)
    return len(cleaned) >= 6 and len(set(cleaned.lower())) <= 2


@cache
def _word_encoding():  # noqa: ANN202 - tiktoken Encoding, optional dependency
    """cl100k BPE (already used for token counts); None when unavailable."""
    try:
        return tiktoken.get_encoding("cl100k_base")
    # No cached BPE file (offline): fall back to the word lists.
    except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        return None


def _is_vocabulary_word(word: str) -> bool:
    """A real English word is one BPE token (' banana'); 'blorf' is two."""
    encoding = _word_encoding()
    return encoding is not None and len(encoding.encode(" " + word)) == 1


def _has_no_recognisable_words(words: list[str]) -> bool:
    """3+ long ASCII alphabetic tokens, none a real word (e.g. 'blorf zindle').

    'banana bread recipe' is real words (off-topic, not spam); text in another
    script is never judged by these English heuristics.
    """
    if len(words) < 3:
        return False
    lowered = [_clean_token(w).lower() for w in words]
    if not all(
        w.isascii() and w.isalpha() and len(w) >= _RARE_MIN_LEN for w in lowered
    ):
        return False
    return not any(
        w in _COMMON_WORDS
        or w in _KNOWN_TERMS
        or w in ALLOWED_ACRONYMS
        or (w not in _LOREM_WORDS and _is_vocabulary_word(w))
        for w in lowered
    )


def _is_typo_of_known_word(token: str) -> bool:
    """'benchmrk', 'exxport': a near-miss of a real word, not mash or noise."""
    letters = _collapse_runs(re.sub(r"[^a-z]", "", _clean_token(token).lower()))
    if len(letters) < _FUZZY_MIN_WORD_LEN or _is_keyboard_mash(letters):
        return False
    if _STRICT_DOUBLES_RE.search(letters) or _VOWEL_RUN_RE.search(letters):
        return False
    return _is_near_catalogue_word(letters)


# A bare calculation: "2+2", "1+1=?", "what is 15% of 200", "sqrt(16)".
# A bare number ("42", "160") is not one; it may be a figure from the KB.
_MATH_MAX_CHARS = 80
_MATH_LEAD_RE = re.compile(
    r"^(?:(?:what\s{1,3}is|what'?s|whats|calculate|compute|solve|evaluate|"
    r"how\s{1,3}much\s{1,3}is|work\s{1,3}out)\s{1,3})?",
    re.IGNORECASE,
)
_MATH_EXPR_RE = re.compile(
    r"^[(\s]{0,3}-?\d{1,12}(?:[.,]\d{1,6})?\s{0,3}%?[)\s]{0,3}"
    r"(?:(?:[-+*/x\u00d7\u00f7^]|\*\*|mod|plus|minus|times|divided\s{1,3}by|"
    r"multiplied\s{1,3}by)[(\s]{0,3}-?\d{1,12}(?:[.,]\d{1,6})?\s{0,3}%?[)\s]{0,3}){1,8}"
    r"(?:=\s{0,3}\??)?[\s?=.!]{0,4}$",
    re.IGNORECASE,
)
_MATH_FUNC_RE = re.compile(
    r"^(?:sqrt|square\s{1,3}root\s{1,3}of|cube\s{1,3}root\s{1,3}of|sin|cos|tan|"
    r"log|ln|abs|exp|factorial(?:\s{1,3}of)?)\s{0,3}\(?\s{0,3}-?\d{1,12}"
    r"(?:[.,]\d{1,6})?\s{0,3}\)?[\s?=.!]{0,4}$",
    re.IGNORECASE,
)
_MATH_PERCENT_RE = re.compile(
    r"^\d{1,12}(?:[.,]\d{1,6})?\s{0,3}(?:%|percent|per\s{1,3}cent)\s{1,3}of\s{1,3}"
    r"\d{1,12}(?:[.,]\d{1,6})?[\s?=.!]{0,4}$",
    re.IGNORECASE,
)


# "what time is it in Sydney?", "what's today's date": the clock, not us.
_CLOCK_RE = re.compile(
    r"^(?:what\s{1,3}time\s{1,3}is\s{1,3}it|what\s{1,3}day\s{1,3}is\s{1,3}(?:it|today)|"
    r"what(?:'s|\s{1,3}is)\s{1,3}(?:the\s{1,3})?(?:current\s{1,3})?(?:time|date)|"
    r"what(?:'s|\s{1,3}is)\s{1,3}today'?s\s{1,3}date|what\s{1,3}date\s{1,3}is\s{1,3}"
    r"(?:it|today))(?:\s{1,3}(?:now|right\s{1,3}now|today|(?:in|at)\s{1,3}"
    r"[a-z]{1,20}(?:\s{1,3}[a-z]{1,20}){0,2}))?[\s?.!]{0,4}$",
    re.IGNORECASE,
)


def check_math(query: str) -> tuple[bool, str | None]:
    """A bare calculation or clock/date ask is off-topic (the same reply as
    any other off-topic ask)."""
    text = " ".join(query[: _MATH_MAX_CHARS + 1].split())
    if not text or len(text) > _MATH_MAX_CHARS:
        return True, None
    if _CLOCK_RE.match(text):
        return False, "off_topic"
    body = text[_MATH_LEAD_RE.match(text).end() :]
    if any(r.match(body) for r in (_MATH_EXPR_RE, _MATH_FUNC_RE, _MATH_PERCENT_RE)):
        return False, "off_topic"
    return True, None


def check_spam(
    query: str,
) -> tuple[bool, str | None]:
    """Detect junk input: gibberish, keyboard mash, ultra-short noise, or promo spam.

    Elongated but real wording ("whaaaat is deployiq", "zero trust??????") is
    normalised before the checks and is never spam by itself.
    """
    # Accents are folded first: "Zéro Trüst" is real wording, not mash.
    text = fold_accents(unicodedata.normalize("NFKC", query[:_MATCH_MAX_CHARS]))
    text = text.strip()
    if not text:
        return True, None

    # Scam wording alone, 2+ links, or a link plus promo wording is spam; a bare
    # single link is not.
    urls = _URL_RE.findall(text)
    if (
        _PROMO_PHRASES_RE.search(text)
        or len(urls) >= 2
        or (urls and _URL_PROMO_RE.search(text))
    ):
        return False, "spam_detected"

    # Punctuation or symbols only (e.g. "???", "---", "!?!?")
    if re.match(r"^[\s\W_]+$", text):
        return False, "unclear_input"

    raw_words = text.split()

    # A token that is nothing but one repeated letter/digit ("aaaaaaaa")
    if any(_is_single_char_run(w) for w in raw_words):
        return False, "spam_detected"

    # Elongation is emphasis: normalise before every remaining check.
    normalised = _collapse_runs(text)
    words = normalised.split()

    # Ultra-short inputs (e.g. "a", "y", "k") that are not whitelisted
    alphanumeric_chars = re.sub(r"[\W_]", "", normalised).lower()
    if (
        len(alphanumeric_chars) <= 2
        # a bare number ("42") may be a figure from the knowledge base
        and not alphanumeric_chars.isdigit()
        and alphanumeric_chars not in ALLOWED_ACRONYMS
        and alphanumeric_chars not in _ALLOWED_SHORT_TOKENS
    ):
        return False, "unclear_input"

    # Repetitive words (e.g. "buy buy buy buy buy")
    if len(words) >= 4:
        lowered_words = [w.lower() for w in words]
        most_common_count = max(lowered_words.count(w) for w in set(lowered_words))
        if most_common_count >= 4 and most_common_count / len(words) >= 0.5:
            return False, "spam_detected"

    # Gibberish words: mash/vowel/cluster rules plus pair plausibility
    gibberish_words = [
        w
        for w in words
        if (is_gibberish_token(w) or is_implausible_word(w))
        and not _is_typo_of_known_word(w)
    ]

    # If single-word query is gibberish -> spam
    if len(words) == 1 and gibberish_words:
        return False, "spam_detected"

    # If 50% or more words are gibberish -> spam
    if gibberish_words and (len(gibberish_words) / len(words)) >= 0.5:
        return False, "spam_detected"

    if sum(_clean_token(w).lower() in _LOREM_WORDS for w in words) >= _LOREM_MIN_HITS:
        return False, "spam_detected"

    # Pronounceable nonsense ("blorf zindle quarpt"): no recognisable word at all
    if _has_no_recognisable_words(words):
        return False, "spam_detected"

    return True, None


_SANITIZE_MIN_VALID_WORDS = 2
_FUZZY_MIN_WORD_LEN = 4
_FUZZY_LONG_TOKEN_LEN = 5
_FUZZY_VERY_LONG_TOKEN_LEN = 8
_FUZZY_MAX_TOKEN_LEN = 40


def _edit_distance_within(a: str, b: str, limit: int) -> bool:
    """True when the Levenshtein distance of a and b is at most limit."""
    if abs(len(a) - len(b)) > limit:
        return False
    previous = list(range(len(b) + 1))
    for i, ch_a in enumerate(a, start=1):
        current = [i]
        for j, ch_b in enumerate(b, start=1):
            current.append(
                min(
                    previous[j] + 1,
                    current[j - 1] + 1,
                    previous[j - 1] + (ch_a != ch_b),
                )
            )
        previous = current
    return previous[-1] <= limit


def _is_near_catalogue_word(letters: str, extra_limit: int = 0) -> bool:
    """A typo of a real word ('trst' -> 'trust') is kept, never stripped."""
    if len(letters) > _FUZZY_MAX_TOKEN_LEN:
        return False
    limit = 2 if len(letters) >= _FUZZY_LONG_TOKEN_LEN else 1
    limit += (len(letters) >= _FUZZY_VERY_LONG_TOKEN_LEN) * extra_limit
    vocabulary = _COMMON_WORDS | _KNOWN_TERMS | ALLOWED_ACRONYMS | _DOMAIN_WORDS
    return any(
        len(word) >= _FUZZY_MIN_WORD_LEN and _edit_distance_within(letters, word, limit)
        for word in vocabulary
    )


_STRICT_DOUBLES_RE = re.compile(r"jj|qq|zx|jk|hj|kj")
_STRIP_MIN_CLUSTER = 6
_STRIP_MIN_VOWELLESS = 5


def _is_clear_junk_shape(letters: str) -> bool:
    """Shapes a typo of a real word cannot produce: mash, jj/qq, long clusters."""
    return bool(
        _is_keyboard_mash(letters)
        or _STRICT_DOUBLES_RE.search(letters)
        or re.search(rf"[bcdfghjklmnpqrstvwxz]{{{_STRIP_MIN_CLUSTER},}}", letters)
        or (len(letters) >= _STRIP_MIN_VOWELLESS and not _VOWEL_RE.search(letters))
    )


def _is_strippable_junk(token: str) -> bool:
    """True only for tokens that are clearly junk, not words, acronyms or typos."""
    original = _clean_token(token)
    if not is_gibberish_token(original):
        return False
    # Acronym/technical casing: mTLS, GDPR, SIEM, DeployIQ.
    if any(ch.isupper() for ch in original[1:]):
        return False
    letters = _collapse_runs(re.sub(r"[^a-z]", "", original.lower()))
    if not _is_clear_junk_shape(letters):
        return False
    # English-plausible spelling ('owns'): vowel present, no impossible pairs.
    if _VOWEL_RE.search(letters) and _rare_pair_count(letters) == 0:
        return False
    return not _is_near_catalogue_word(letters, extra_limit=1)


def _is_strippable_word(token: str) -> bool:
    folded = fold_accents(token)
    if any(ch.isalpha() and not ch.isascii() for ch in folded):
        return False
    return _is_strippable_junk(folded)


def sanitize_query(query: str, flags: list[str] | None = None) -> tuple[str, list[str]]:
    """Strip stray junk tokens when valid question content exists.

    Conservative: a token is removed only when it is clearly junk (not an
    English-plausible word, acronym/brand term, or near-typo of a known word)
    and at least two other words remain. Otherwise the query is kept as typed.

    Accents are folded before the letter heuristics ("Zéro" is "Zero"), and a
    token holding any non-ASCII letter ("Größe", "безопасность", "安全") is
    never stripped: the English shape rules cannot judge it.

    ``flags``: the pre-check flags the caller already computed (pre-checks
    then run only once per request); computed here when omitted.

    Returns (sanitized_query, pre_flags).
    """
    if flags is None:
        flags = run_pre_checks(query, 2000)
    if flags:
        return query, flags

    # e.g. "what is deployiq? akjj" -> "what is deployiq?"
    words = query.strip().split()
    valid_words = [w for w in words if not _is_strippable_word(w)]

    if _SANITIZE_MIN_VALID_WORDS <= len(valid_words) < len(words):
        return " ".join(valid_words).strip(), []

    return query.strip(), []


_JUNK_FLAGS = frozenset({"spam_detected", "unclear_input", "off_topic"})


def _security_check(check, query: str, raw: str | None) -> tuple[bool, str | None]:  # noqa: ANN001
    """Run a security check on the normalised text and, if given, the raw text.

    The raw text matters: normalising strips markup such as ``</system>`` and
    turns ``SECRET_KEY`` into ``SECRET KEY``.
    """
    passed, flag = check(query)
    if passed and raw is not None and raw != query:
        passed, flag = check(raw[:_MATCH_MAX_CHARS])
    return passed, flag


def run_pre_checks(
    query: str,
    max_length: int,
    raw: str | None = None,
) -> list[str]:
    """Run checks that do not require retrieval.

    ``query`` is the normalised text (``app.guardrails.normalize``); ``raw``
    (optional) is the text as typed, also screened by the security checks.

    Flag order: length, sensitive-request, unsafe-input, then junk / maths.
    When a security flag (sensitive/unsafe) fires, the junk flags
    (spam_detected / unclear_input / off_topic) are dropped, so the visitor
    sees the security response and attack strings are never mislabelled as
    spam. Length flags always remain. A bare calculation ("2+2", "sqrt(16)",
    "15% of 200") is ``off_topic``, the same reply as any other off-topic ask.
    """
    flags: list[str] = []
    capped = query[:_MATCH_MAX_CHARS]

    checks = (
        (check_length, (raw if raw is not None else query, max_length)),
        (_security_check, (check_sensitive_request, capped, raw)),
        (_security_check, (check_unsafe_input, capped, raw)),
        (check_math, (capped,)),
    )

    for check, args in checks:
        passed, flag = check(*args)
        if not passed and flag:
            flags.append(flag)
    if "off_topic" not in flags:
        passed, flag = check_spam(capped)
        if not passed and flag:
            flags.append(flag)

    security_hit = any(
        f in flags
        for f in (
            "unsafe_input_detected",
            "application_secret_request",
            "no_permission",
            "personal_contact_request",
        )
    )
    if security_hit:
        flags = [f for f in flags if f not in _JUNK_FLAGS]

    return flags
