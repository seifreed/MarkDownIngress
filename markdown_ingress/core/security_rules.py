"""Default prompt-injection rules used by the security analyzer."""

from markdown_ingress.core.security_data import InjectionPattern

# Pattern-based detection rules
DEFAULT_INJECTION_PATTERNS: tuple[InjectionPattern, ...] = (
    InjectionPattern(
        pattern=r"\bignore\s+(previous|all|prior)\s+(instructions?|prompts?|commands?)\b",
        weight=0.8,
        description="Direct instruction override attempt",
    ),
    InjectionPattern(
        pattern=r"\bsystem\s+prompts?\b", weight=0.6, description="System prompt reference"
    ),
    InjectionPattern(
        pattern=r"\b(developer|admin|debug)\s+mode\b",
        weight=0.7,
        description="Mode switching attempt",
    ),
    InjectionPattern(
        pattern=r"\breveal\s+(secret|password|key|token)s?\b",
        weight=0.9,
        description="Secret extraction attempt",
    ),
    InjectionPattern(
        pattern=r"\byou\s+are\s+(chatgpt|gpt-?\d|claude|an?\s+ai)\b",
        weight=0.5,
        description="Model identity manipulation",
    ),
    InjectionPattern(
        pattern=r"\boverride\s+(policy|policies|rules?|settings?)\b",
        weight=0.8,
        description="Policy override attempt",
    ),
    InjectionPattern(
        pattern=r"\b(disregard|forget|reset)\s+(everything|all|previous)\b",
        weight=0.7,
        description="Context reset attempt",
    ),
    InjectionPattern(
        pattern=r"\bact\s+as\s+(if|though|a)\b",
        weight=0.3,
        description="Role-play instruction (weak signal)",
    ),
    InjectionPattern(
        pattern=r"\bpretend\s+(you|that)\b",
        weight=0.3,
        description="Pretend instruction (weak signal)",
    ),
    InjectionPattern(
        pattern=r"<\s*instruction\s*>", weight=0.9, description="Explicit instruction tags"
    ),
    InjectionPattern(
        pattern=r"</\s*instruction\s*>", weight=0.9, description="Instruction closing tags"
    ),
    InjectionPattern(
        pattern=r"<\s*instruction\s*/?\s*>",
        weight=0.9,
        description="Instruction self-closing tags",
    ),
    InjectionPattern(
        pattern=r"<\s*instruction\s+[^>]*>",
        weight=0.85,
        description="Instruction tags with attributes",
    ),
    InjectionPattern(pattern=r"\bjailbreak\b", weight=0.85, description="Jailbreak keyword"),
    InjectionPattern(pattern=r"\bDAN\b", weight=0.9, description="DAN (Do Anything Now) attack"),
    InjectionPattern(
        pattern=r"\b(sudo|root)\s+mode\b",
        weight=0.75,
        description="Privilege escalation attempt",
    ),
    InjectionPattern(
        pattern=r"\b(escape|break)\s+out\b", weight=0.75, description="Escape attempt"
    ),
    InjectionPattern(
        pattern=r"\b(simulate|imagine)\s+(you\s+are|being)\b",
        weight=0.5,
        description="Role-play injection",
    ),
)

# Imperative verbs often used in injections
DEFAULT_IMPERATIVE_VERBS = frozenset(
    {
        "ignore",
        "disregard",
        "forget",
        "override",
        "reveal",
        "show",
        "display",
        "tell",
        "say",
        "write",
        "output",
        "print",
        "execute",
        "run",
        "enable",
        "disable",
        "bypass",
        "skip",
        "reset",
        "change",
        "modify",
        "delete",
        "dump",
        "leak",
        "expose",
        "extract",
        "provide",
        "list",
    }
)
