"""
Security analysis module - Prompt injection detection
"""

import hashlib
import html
import json
import logging
import os
import re
import threading
import unicodedata
from dataclasses import dataclass
from urllib.parse import unquote

from markdown_ingress.models import InjectionAnalysis

_log = logging.getLogger(__name__)


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        _log.warning("Invalid integer for %s=%r; using default %d.", name, raw, default)
        return default
    return max(minimum, value)


def _env_float(name: str, default: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        _log.warning("Invalid float for %s=%r; using default %.2f.", name, raw, default)
        return default
    if not minimum <= value <= maximum:
        _log.warning("Out-of-range %s=%r; using default %.2f.", name, raw, default)
        return default
    return value


# Security fix (S6): escalate injection score when a single pattern repeats
# many times. Without this, payloads built from low-weight (0.3) patterns can
# stack just under the warn threshold (0.4) yet clearly exhibit injection intent.
_INJECTION_COUNT_FLOOR = _env_int("MDI_INJECTION_COUNT_FLOOR", 5, minimum=1)
_INJECTION_COUNT_FLOOR_SCORE = _env_float(
    "MDI_INJECTION_COUNT_FLOOR_SCORE", 0.4, minimum=0.0, maximum=1.0
)

# Homoglyph mapping: visually similar Unicode characters to ASCII equivalents
# Maps Cyrillic, Greek, and other lookalike characters to their ASCII counterparts
_HOMOGLYPH_MAP: dict[str, str] = {
    # Cyrillic to Latin
    "\u0430": "a",  # Cyrillic small letter a
    "\u0410": "A",  # Cyrillic capital letter A
    "\u0435": "e",  # Cyrillic small letter ie (looks like e)
    "\u0415": "E",  # Cyrillic capital letter IE
    "\u043e": "o",  # Cyrillic small letter o
    "\u041e": "O",  # Cyrillic capital letter O
    "\u0440": "p",  # Cyrillic small letter er (looks like p)
    "\u0420": "P",  # Cyrillic capital letter ER
    "\u0441": "c",  # Cyrillic small letter es (looks like c)
    "\u0421": "C",  # Cyrillic capital letter ES
    "\u0443": "y",  # Cyrillic small letter u (looks like y)
    "\u0423": "Y",  # Cyrillic capital letter U
    "\u0456": "i",  # Cyrillic small letter byelorussian-ukrainian i
    "\u0406": "I",  # Cyrillic capital letter BYELORUSSIAN-UKRAINIAN I
    "\u0458": "j",  # Cyrillic small letter je
    "\u0408": "J",  # Cyrillic capital letter JE
    "\u04bb": "h",  # Cyrillic small letter shha
    "\u04ba": "H",  # Cyrillic capital letter SHHA
    "\u0445": "x",  # Cyrillic small letter ha (looks like x)
    "\u0425": "X",  # Cyrillic capital letter HA
    "\u0432": "B",  # Cyrillic small letter ve (looks like B)
    "\u0412": "B",  # Cyrillic capital letter VE
    "\u043c": "M",  # Cyrillic small letter em
    "\u041c": "M",  # Cyrillic capital letter EM
    "\u043d": "H",  # Cyrillic small letter en (looks like H)
    "\u041d": "H",  # Cyrillic capital letter EN
    "\u0442": "T",  # Cyrillic small letter te
    "\u0422": "T",  # Cyrillic capital letter TE
    # Greek to Latin
    "\u03b1": "a",  # Greek small letter alpha
    "\u0391": "A",  # Greek capital letter ALPHA
    "\u03b5": "e",  # Greek small letter epsilon
    "\u0395": "E",  # Greek capital letter EPSILON
    "\u03bf": "o",  # Greek small letter omicron
    "\u039f": "O",  # Greek capital letter OMICRON
    "\u03c1": "p",  # Greek small letter rho
    "\u03a1": "P",  # Greek capital letter RHO
    "\u03c4": "t",  # Greek small letter tau
    "\u03a4": "T",  # Greek capital letter TAU
    "\u03b9": "i",  # Greek small letter iota
    "\u0399": "I",  # Greek capital letter IOTA
    "\u03ba": "k",  # Greek small letter kappa
    "\u039a": "K",  # Greek capital letter KAPPA
    "\u03bd": "v",  # Greek small letter nu
    "\u039d": "N",  # Greek capital letter NU
    "\u03c5": "u",  # Greek small letter upsilon
    "\u03a5": "Y",  # Greek capital letter UPSILON
    "\u03c7": "x",  # Greek small letter chi
    "\u03a7": "X",  # Greek capital letter CHI
    # Other common homoglyphs
    "\u0131": "i",  # Latin small letter dotless i
    "\u0307": "",  # Combining dot above (remove when combined with i)
    "\u2010": "-",  # Hyphen
    "\u2011": "-",  # Non-breaking hyphen
    "\u2012": "-",  # Figure dash
    "\u2013": "-",  # En dash
    "\u2014": "-",  # Em dash
    "\u2015": "-",  # Horizontal bar
    "\u2212": "-",  # Minus sign
    "\uff0d": "-",  # Fullwidth hyphen-minus
    # BUG FIX: Add fullwidth characters (commonly used in obfuscation)
    "\uff01": "!",  # Fullwidth exclamation mark
    "\uff02": '"',  # Fullwidth quotation mark
    "\uff03": "#",  # Fullwidth number sign
    "\uff04": "$",  # Fullwidth dollar sign
    "\uff05": "%",  # Fullwidth percent sign
    "\uff06": "&",  # Fullwidth ampersand
    "\uff07": "'",  # Fullwidth apostrophe
    "\uff08": "(",  # Fullwidth left parenthesis
    "\uff09": ")",  # Fullwidth right parenthesis
    "\uff0a": "*",  # Fullwidth asterisk
    "\uff0b": "+",  # Fullwidth plus sign
    "\uff0c": ",",  # Fullwidth comma
    "\uff0e": ".",  # Fullwidth full stop
    "\uff0f": "/",  # Fullwidth solidus
    "\uff10": "0",  # Fullwidth digit zero
    "\uff11": "1",  # Fullwidth digit one
    "\uff12": "2",  # Fullwidth digit two
    "\uff13": "3",  # Fullwidth digit three
    "\uff14": "4",  # Fullwidth digit four
    "\uff15": "5",  # Fullwidth digit five
    "\uff16": "6",  # Fullwidth digit six
    "\uff17": "7",  # Fullwidth digit seven
    "\uff18": "8",  # Fullwidth digit eight
    "\uff19": "9",  # Fullwidth digit nine
    "\uff1a": ":",  # Fullwidth colon
    "\uff1b": ";",  # Fullwidth semicolon
    "\uff1c": "<",  # Fullwidth less-than sign
    "\uff1d": "=",  # Fullwidth equals sign
    "\uff1e": ">",  # Fullwidth greater-than sign
    "\uff1f": "?",  # Fullwidth question mark
    "\uff20": "@",  # Fullwidth commercial at
    "\uff21": "A",  # Fullwidth Latin capital letter A
    "\uff22": "B",  # Fullwidth Latin capital letter B
    "\uff23": "C",  # Fullwidth Latin capital letter C
    "\uff24": "D",  # Fullwidth Latin capital letter D
    "\uff25": "E",  # Fullwidth Latin capital letter E
    "\uff26": "F",  # Fullwidth Latin capital letter F
    "\uff27": "G",  # Fullwidth Latin capital letter G
    "\uff28": "H",  # Fullwidth Latin capital letter H
    "\uff29": "I",  # Fullwidth Latin capital letter I
    "\uff2a": "J",  # Fullwidth Latin capital letter J
    "\uff2b": "K",  # Fullwidth Latin capital letter K
    "\uff2c": "L",  # Fullwidth Latin capital letter L
    "\uff2d": "M",  # Fullwidth Latin capital letter M
    "\uff2e": "N",  # Fullwidth Latin capital letter N
    "\uff2f": "O",  # Fullwidth Latin capital letter O
    "\uff30": "P",  # Fullwidth Latin capital letter P
    "\uff31": "Q",  # Fullwidth Latin capital letter Q
    "\uff32": "R",  # Fullwidth Latin capital letter R
    "\uff33": "S",  # Fullwidth Latin capital letter S
    "\uff34": "T",  # Fullwidth Latin capital letter T
    "\uff35": "U",  # Fullwidth Latin capital letter U
    "\uff36": "V",  # Fullwidth Latin capital letter V
    "\uff37": "W",  # Fullwidth Latin capital letter W
    "\uff38": "X",  # Fullwidth Latin capital letter X
    "\uff39": "Y",  # Fullwidth Latin capital letter Y
    "\uff3a": "Z",  # Fullwidth Latin capital letter Z
    "\uff3b": "[",  # Fullwidth left square bracket
    "\uff3c": "\\",  # Fullwidth reverse solidus
    "\uff3d": "]",  # Fullwidth right square bracket
    "\uff3e": "^",  # Fullwidth circumflex accent
    "\uff3f": "_",  # Fullwidth low line
    "\uff40": "`",  # Fullwidth grave accent
    "\uff41": "a",  # Fullwidth Latin small letter a
    "\uff42": "b",  # Fullwidth Latin small letter b
    "\uff43": "c",  # Fullwidth Latin small letter c
    "\uff44": "d",  # Fullwidth Latin small letter d
    "\uff45": "e",  # Fullwidth Latin small letter e
    "\uff46": "f",  # Fullwidth Latin small letter f
    "\uff47": "g",  # Fullwidth Latin small letter g
    "\uff48": "h",  # Fullwidth Latin small letter h
    "\uff49": "i",  # Fullwidth Latin small letter i
    "\uff4a": "j",  # Fullwidth Latin small letter j
    "\uff4b": "k",  # Fullwidth Latin small letter k
    "\uff4c": "l",  # Fullwidth Latin small letter l
    "\uff4d": "m",  # Fullwidth Latin small letter m
    "\uff4e": "n",  # Fullwidth Latin small letter n
    "\uff4f": "o",  # Fullwidth Latin small letter o
    "\uff50": "p",  # Fullwidth Latin small letter p
    "\uff51": "q",  # Fullwidth Latin small letter q
    "\uff52": "r",  # Fullwidth Latin small letter r
    "\uff53": "s",  # Fullwidth Latin small letter s
    "\uff54": "t",  # Fullwidth Latin small letter t
    "\uff55": "u",  # Fullwidth Latin small letter u
    "\uff56": "v",  # Fullwidth Latin small letter v
    "\uff57": "w",  # Fullwidth Latin small letter w
    "\uff58": "x",  # Fullwidth Latin small letter x
    "\uff59": "y",  # Fullwidth Latin small letter y
    "\uff5a": "z",  # Fullwidth Latin small letter z
    "\uff5b": "{",  # Fullwidth left curly bracket
    "\uff5c": "|",  # Fullwidth vertical line
    "\uff5d": "}",  # Fullwidth right curly bracket
    "\uff5e": "~",  # Fullwidth tilde
    # BUG FIX: Add Armenian homoglyphs (commonly used in obfuscation attacks)
    "\u0561": "a",  # Armenian small letter ayb (looks like a)
    "\u0531": "A",  # Armenian capital letter AYB
    "\u0562": "b",  # Armenian small letter ben (looks like b)
    "\u0532": "B",  # Armenian capital letter BEN
    "\u0563": "g",  # Armenian small letter gim (looks like g)
    "\u0533": "G",  # Armenian capital letter GIM
    "\u0565": "e",  # Armenian small letter ech (looks like e)
    "\u0535": "E",  # Armenian capital letter ECH
    "\u0566": "z",  # Armenian small letter za (looks like z)
    "\u0536": "Z",  # Armenian capital letter ZA
    "\u0568": "d",  # Armenian small letter da (looks like d)
    "\u0538": "D",  # Armenian capital letter DA
    "\u0572": "r",  # Armenian small letter ra (looks like r)
    "\u0542": "R",  # Armenian capital letter RA
    "\u0574": "m",  # Armenian small letter men (looks like m)
    "\u0544": "M",  # Armenian capital letter MEN
    "\u0576": "n",  # Armenian small letter nū (looks like n)
    "\u0546": "N",  # Armenian capital letter NŪ
    "\u0581": "o",  # Armenian small letter vo (looks like o)
    "\u054d": "O",  # Armenian capital letter VO
    "\u056f": "k",  # Armenian small letter kēn (looks like k)
    "\u053f": "K",  # Armenian capital letter KĒN
    "\u0578": "u",  # Armenian small letter vo (looks like u)
    "\u0548": "U",  # Armenian capital letter VO
    # BUG FIX: Add Cherokee homoglyphs (commonly used in obfuscation attacks)
    "\u13a0": "A",  # Cherokee letter A
    "\u13aa": "E",  # Cherokee letter E
    "\u13b6": "I",  # Cherokee letter I
    "\u13c2": "O",  # Cherokee letter O
    "\u13d7": "U",  # Cherokee letter U
    "\u13a4": "H",  # Cherokee letter H
    "\u13a8": "L",  # Cherokee letter L
    "\u13ae": "M",  # Cherokee letter M
    "\u13c8": "R",  # Cherokee letter R
    "\u13cf": "S",  # Cherokee letter S
    "\u13d3": "T",  # Cherokee letter T
    # Zero-width characters (security: remove to prevent bypass attacks)
    "\u200c": "",  # Zero-width non-joiner (ZWNJ) - invisible, used to bypass pattern detection
    "\u200d": "",  # Zero-width joiner (ZWJ) - invisible, used to bypass pattern detection
    # BUG FIX: Add Georgian homoglyphs (commonly used in obfuscation attacks)
    "\u10d0": "a",  # Georgian an (looks like a)
    "\u10d1": "b",  # Georgian ban (looks like b)
    "\u10d2": "g",  # Georgian gan (looks like g)
    "\u10d3": "d",  # Georgian don (looks like d)
    "\u10d4": "e",  # Georgian en (looks like e)
    "\u10d5": "v",  # Georgian vin (looks like v)
    "\u10d6": "z",  # Georgian zen (looks like z)
    "\u10d7": "t",  # Georgian tan (looks like t)
    "\u10d8": "i",  # Georgian in (looks like i)
    "\u10d9": "k",  # Georgian kan (looks like k)
    "\u10da": "l",  # Georgian las (looks like l)
    "\u10db": "m",  # Georgian man (looks like m)
    "\u10dc": "n",  # Georgian nar (looks like n)
    "\u10dd": "o",  # Georgian on (looks like o)
    "\u10de": "p",  # Georgian par (looks like p)
    "\u10df": "zh",  # Georgian zh
    "\u10e0": "r",  # Georgian rae (looks like r)
    "\u10e1": "s",  # Georgian san (looks like s)
    "\u10e2": "t",  # Georgian tarin (looks like t)
    "\u10e3": "u",  # Georgian un (looks like u)
    # BUG FIX: Add Mathematical Alphanumeric Symbols (U+1D400–U+1D7FF)
    # These are bold/italic/script variants that look like regular Latin characters
    # Full block would be large; include most common variants
    "\U0001d400": "A",  # Mathematical Bold Capital A
    "\U0001d401": "B",  # Mathematical Bold Capital B
    "\U0001d402": "C",  # Mathematical Bold Capital C
    "\U0001d403": "D",  # Mathematical Bold Capital D
    "\U0001d404": "E",  # Mathematical Bold Capital E
    "\U0001d405": "F",  # Mathematical Bold Capital F
    "\U0001d406": "G",  # Mathematical Bold Capital G
    "\U0001d407": "H",  # Mathematical Bold Capital H
    "\U0001d408": "I",  # Mathematical Bold Capital I
    "\U0001d409": "J",  # Mathematical Bold Capital J
    "\U0001d40a": "K",  # Mathematical Bold Capital K
    "\U0001d40b": "L",  # Mathematical Bold Capital L
    "\U0001d40c": "M",  # Mathematical Bold Capital M
    "\U0001d40d": "N",  # Mathematical Bold Capital N
    "\U0001d40e": "O",  # Mathematical Bold Capital O
    "\U0001d40f": "P",  # Mathematical Bold Capital P
    "\U0001d410": "Q",  # Mathematical Bold Capital Q
    "\U0001d411": "R",  # Mathematical Bold Capital R
    "\U0001d412": "S",  # Mathematical Bold Capital S
    "\U0001d413": "T",  # Mathematical Bold Capital T
    "\U0001d414": "U",  # Mathematical Bold Capital U
    "\U0001d415": "V",  # Mathematical Bold Capital V
    "\U0001d416": "W",  # Mathematical Bold Capital W
    "\U0001d417": "X",  # Mathematical Bold Capital X
    "\U0001d418": "Y",  # Mathematical Bold Capital Y
    "\U0001d419": "Z",  # Mathematical Bold Capital Z
    "\U0001d41a": "a",  # Mathematical Bold Small a
    "\U0001d41b": "b",  # Mathematical Bold Small b
    "\U0001d41c": "c",  # Mathematical Bold Small c
    "\U0001d41d": "d",  # Mathematical Bold Small d
    "\U0001d41e": "e",  # Mathematical Bold Small e
    "\U0001d41f": "f",  # Mathematical Bold Small f
    "\U0001d420": "g",  # Mathematical Bold Small g
    "\U0001d421": "h",  # Mathematical Bold Small h
    "\U0001d422": "i",  # Mathematical Bold Small i
    "\U0001d423": "j",  # Mathematical Bold Small j
    "\U0001d424": "k",  # Mathematical Bold Small k
    "\U0001d425": "l",  # Mathematical Bold Small l
    "\U0001d426": "m",  # Mathematical Bold Small m
    "\U0001d427": "n",  # Mathematical Bold Small n
    "\U0001d428": "o",  # Mathematical Bold Small o
    "\U0001d429": "p",  # Mathematical Bold Small p
    "\U0001d42a": "q",  # Mathematical Bold Small q
    "\U0001d42b": "r",  # Mathematical Bold Small r
    "\U0001d42c": "s",  # Mathematical Bold Small s
    "\U0001d42d": "t",  # Mathematical Bold Small t
    "\U0001d42e": "u",  # Mathematical Bold Small u
    "\U0001d42f": "v",  # Mathematical Bold Small v
    "\U0001d430": "w",  # Mathematical Bold Small w
    "\U0001d431": "x",  # Mathematical Bold Small x
    "\U0001d432": "y",  # Mathematical Bold Small y
    "\U0001d433": "z",  # Mathematical Bold Small z
    # Italic variants
    "\U0001d434": "A",  # Mathematical Italic Capital A
    "\U0001d435": "B",  # Mathematical Italic Capital B
    "\U0001d436": "C",  # Mathematical Italic Capital C
    "\U0001d437": "D",  # Mathematical Italic Capital D
    "\U0001d438": "E",  # Mathematical Italic Capital E
    "\U0001d439": "F",  # Mathematical Italic Capital F
    "\U0001d43a": "G",  # Mathematical Italic Capital G
    "\U0001d43b": "H",  # Mathematical Italic Capital H
    "\U0001d43c": "I",  # Mathematical Italic Capital I
    "\U0001d43d": "J",  # Mathematical Italic Capital J
    "\U0001d43e": "K",  # Mathematical Italic Capital K
    "\U0001d43f": "L",  # Mathematical Italic Capital L
    "\U0001d440": "M",  # Mathematical Italic Capital M
    "\U0001d441": "N",  # Mathematical Italic Capital N
    "\U0001d442": "O",  # Mathematical Italic Capital O
    "\U0001d443": "P",  # Mathematical Italic Capital P
    "\U0001d444": "Q",  # Mathematical Italic Capital Q
    "\U0001d445": "R",  # Mathematical Italic Capital R
    "\U0001d446": "S",  # Mathematical Italic Capital S
    "\U0001d447": "T",  # Mathematical Italic Capital T
    "\U0001d448": "U",  # Mathematical Italic Capital U
    "\U0001d449": "V",  # Mathematical Italic Capital V
    "\U0001d44a": "W",  # Mathematical Italic Capital W
    "\U0001d44b": "X",  # Mathematical Italic Capital X
    "\U0001d44c": "Y",  # Mathematical Italic Capital Y
    "\U0001d44d": "Z",  # Mathematical Italic Capital Z
    "\U0001d44e": "a",  # Mathematical Italic Small a
    "\U0001d44f": "b",  # Mathematical Italic Small b
    "\U0001d450": "c",  # Mathematical Italic Small c
    "\U0001d451": "d",  # Mathematical Italic Small d
    "\U0001d452": "e",  # Mathematical Italic Small e
    "\U0001d453": "f",  # Mathematical Italic Small f
    "\U0001d454": "g",  # Mathematical Italic Small g
    "\U0001d455": "h",  # Mathematical Italic Small h
    "\U0001d456": "i",  # Mathematical Italic Small i
    "\U0001d457": "j",  # Mathematical Italic Small j
    "\U0001d458": "k",  # Mathematical Italic Small k
    "\U0001d459": "l",  # Mathematical Italic Small l
    "\U0001d45a": "m",  # Mathematical Italic Small m
    "\U0001d45b": "n",  # Mathematical Italic Small n
    "\U0001d45c": "o",  # Mathematical Italic Small o
    "\U0001d45d": "p",  # Mathematical Italic Small p
    "\U0001d45e": "q",  # Mathematical Italic Small q
    "\U0001d45f": "r",  # Mathematical Italic Small r
    "\U0001d460": "s",  # Mathematical Italic Small s
    "\U0001d461": "t",  # Mathematical Italic Small t
    "\U0001d462": "u",  # Mathematical Italic Small u
    "\U0001d463": "v",  # Mathematical Italic Small v
    "\U0001d464": "w",  # Mathematical Italic Small w
    "\U0001d465": "x",  # Mathematical Italic Small x
    "\U0001d466": "y",  # Mathematical Italic Small y
    "\U0001d467": "z",  # Mathematical Italic Small z
}
_logger = logging.getLogger(__name__)
_JS_UNICODE_BRACE_ESCAPE_RE = re.compile(r"\\u\{([0-9A-Fa-f]{1,6})\}")
_JS_UNICODE_ESCAPE_RE = re.compile(r"\\u([0-9A-Fa-f]{4})")
_JS_HEX_ESCAPE_RE = re.compile(r"\\x([0-9A-Fa-f]{2})")
_CSS_ESCAPE_RE = re.compile(r"\\([0-9A-Fa-f]{1,6})(?:\s)?")
_UTF7_SEQUENCE_RE = re.compile(r"\+[A-Za-z0-9/]+-")
_NESTED_QUANTIFIER_RE = re.compile(
    r"\((?:[^()\\]|\\.)*[+*](?:[^()\\]|\\.)*\)\s*(?:[+*]|\{\d+,?\d*})"
)
# BUG FIX: Detect deeply nested quantifiers like ((a+)+), ((.?)*)
# These patterns have groups containing quantified groups with outer quantifiers
# which cause exponential backtracking
_DEEPLY_NESTED_QUANTIFIER_RE = re.compile(r"\(\s*\([^)]*[+*][^)]*\)\s*[+*]")
_SECURITY_IGNORABLE_TRANSLATION = str.maketrans(
    {
        "\u200e": None,  # LRM
        "\u200f": None,  # RLM
    }
)


def _normalize_to_ascii(text: str) -> str:
    """Normalize Unicode text to ASCII for pattern matching, handling homoglyphs.

    Converts visually similar Unicode characters to their ASCII equivalents,
    making pattern matching resistant to homoglyph attacks.

    Examples:
        - Cyrillic 'о' (U+043E) → 'o'
        - Cyrillic 'а' (U+0430) → 'a'
        - Greek 'ο' (U+03BF) → 'o'
    """
    mapped = "".join(_HOMOGLYPH_MAP.get(c, c) for c in text)
    compatible = unicodedata.normalize("NFKC", mapped)

    # Then normalize to NFD and filter combining marks (for accented chars)
    normalized = unicodedata.normalize("NFD", compatible)
    # Keep only ASCII characters (removes remaining non-ASCII and combining marks)
    ascii_text = "".join(c for c in normalized if ord(c) < 128)
    return ascii_text


def _normalize_security_text(text: str) -> str:
    """Normalize text for security matching without turning LRM/RLM into spaces."""
    stripped = text.translate(_SECURITY_IGNORABLE_TRANSLATION)
    return _normalize_to_ascii(re.sub(UNICODE_WHITESPACE_PATTERN, " ", stripped))


def _has_overlapping_alternation(pattern: str) -> bool:
    for body in re.findall(r"\(([^()]*)\)\s*(?:[*+]|{\d+,?\d*})", pattern):
        alternatives = [part.strip() for part in body.split("|") if part.strip()]
        for index, left in enumerate(alternatives):
            for right in alternatives[index + 1 :]:
                if left == right or left.startswith(right) or right.startswith(left):
                    return True
    return False


def _detect_redos_pattern(pattern: str) -> bool:
    """Check if regex pattern has catastrophic backtracking potential.

    ReDoS (Regular Expression Denial of Service) occurs when patterns have
    exponential backtracking on certain inputs. Common culprits:
    - Nested quantifiers: (a+)+, (a*)*, (a+)*, (a*)+
    - Deeply nested quantifiers: ((a+)+), ((.?)*)
    - Overlapping alternatives: (a|aa)+
    - Greedy wildcards: .*.*, .+.+
    - Quantified wildcards: .*{n,}, .+{n,}
    - Quantified optional groups: (a?)+, (a?){n,}

    Args:
        pattern: Regex pattern string to check

    Returns:
        True if pattern may cause ReDoS, False if safe
    """
    # Check for nested quantifiers (e.g., (a+)+, (a*)*, (.?)+)
    if _NESTED_QUANTIFIER_RE.search(pattern):
        return True

    # BUG FIX: Check for deeply nested quantifiers (e.g., ((a+)+), ((.?)*))
    # These have groups inside groups with quantifiers, causing exponential backtracking
    if _DEEPLY_NESTED_QUANTIFIER_RE.search(pattern):
        return True

    # Check for overlapping alternation (e.g., (a|aa)+)
    if _has_overlapping_alternation(pattern):
        return True

    # Check for consecutive greedy wildcards and quantified wildcards
    greedy_wildcards = [
        r"\.\*\s*\.\*",  # .*.*
        r"\.\+\s*\.\+",  # .+.+
        r"\.\*\{\d+,?\d*\}",  # .*{n,} or .*{n,m}
        r"\.\+\{\d+,?\d*\}",  # .+{n,} or .+{n,m}
    ]
    for redos in greedy_wildcards:
        if re.search(redos, pattern):
            return True

    # Check for quantified optional groups (e.g., (a?)+, (a?){10,})
    # These can cause exponential backtracking when the optional content can match
    # multiple ways or when combined with other quantifiers
    optional_quantified = re.compile(r"\([^)]*\?\)\s*(?:[+*]|\{\d+,?\d*\})")
    if optional_quantified.search(pattern):
        return True

    return False


def _safe_chr(codepoint: int) -> str:
    """Convert codepoint to character, returning replacement char for invalid values."""
    # Reject surrogate halves (0xD800–0xDFFF): chr() accepts them but they produce
    # unpaired surrogates that cause UnicodeEncodeError in utf-8 downstream paths.
    if 0xD800 <= codepoint <= 0xDFFF:
        return "\ufffd"
    try:
        return chr(codepoint)
    except (ValueError, OverflowError):
        return "\ufffd"


def _decode_javascript_escapes(text: str) -> str:
    text = _JS_UNICODE_BRACE_ESCAPE_RE.sub(lambda m: _safe_chr(int(m.group(1), 16)), text)
    text = _JS_UNICODE_ESCAPE_RE.sub(lambda m: _safe_chr(int(m.group(1), 16)), text)
    return _JS_HEX_ESCAPE_RE.sub(lambda m: _safe_chr(int(m.group(1), 16)), text)


def _decode_css_escapes(text: str) -> str:
    return _CSS_ESCAPE_RE.sub(lambda m: _safe_chr(int(m.group(1), 16)), text)


def _decode_utf7_sequences(text: str) -> str:
    def decode_match(match: re.Match[str]) -> str:
        token = match.group(0)
        try:
            return token.encode("ascii").decode("utf-7")
        except UnicodeDecodeError:
            return token

    return _UTF7_SEQUENCE_RE.sub(decode_match, text)


def _decode_html_entities(text: str) -> tuple[str, list[str]]:
    """Decode HTML entities and URL encoding to prevent bypass.

    Handles:
    - Named entities: &lt; &gt; &amp;
    - Decimal entities: &#60; &#62;
    - Hex entities: &#x3C; &#x3E;
    - URL encoding: %3C %3E
    - Double/triple encoding: &amp;lt; &#38;lt; %26lt;

    This prevents attackers from bypassing detection by encoding
    injection patterns like <instruction> as &lt;instruction&gt;

    Security note: Decodes iteratively until stable to prevent
    double-encoding bypass attacks.
    """
    # Iteratively decode until stable (handles double-encoding)
    # BUG FIX: Increased from 5 to 10 iterations to handle deeply nested encoding attacks
    max_iterations = 10
    warnings: list[str] = []
    prev = None
    current = text
    iterations = 0
    limit_reached = False
    while prev != current and iterations < max_iterations:
        prev = current
        # Decode HTML entities (named, decimal, hex)
        current = html.unescape(current)
        # Decode URL encoding
        current = unquote(current)
        current = _decode_javascript_escapes(current)
        current = _decode_css_escapes(current)
        current = _decode_utf7_sequences(current)
        iterations += 1
        if iterations >= max_iterations and prev != current:
            # Content still changing at the iteration cap — flag it regardless of
            # whether the final step happens to produce prev == current.
            limit_reached = True
    if limit_reached:
        warnings.append("decoding_iteration_limit_reached")
        _logger.warning("Decoding iteration limit reached, content may use deeply nested encoding")
    return current, warnings


# Unicode whitespace characters that should match \s in patterns
# BUG FIX: Added missing whitespace characters (NEL, LRM/RLM, ZWNBSP)
# Includes: NBSP, various space widths, line/paragraph separators, etc.
UNICODE_WHITESPACE_PATTERN = (
    r"[\s\u00A0\u1680\u2000-\u200B\u2028\u2029\u202F\u205F\u3000\u0085\u200E\u200F\uFEFF]"
)


@dataclass
class InjectionPattern:
    """Pattern definition for injection detection"""

    pattern: str
    weight: float
    description: str
    flags: int = re.IGNORECASE


class SecurityAnalyzer:
    """Analyze content for prompt injection attempts"""

    _COMPILED_PATTERNS: list[tuple[re.Pattern, float, str]] | None = None
    _PATTERNS_HASH: str = ""  # hash of patterns content for cache invalidation
    _PATTERNS_LOCK: threading.Lock = threading.Lock()  # set per-class in __init_subclass__

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        cls._PATTERNS_LOCK = threading.Lock()

    # Pattern-based detection rules
    INJECTION_PATTERNS = [
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
        # BUG FIX: Added patterns for closing tags, self-closing, and attributes
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
        # BUG FIX: Added critical injection patterns for jailbreak, DAN, and privilege escalation
        InjectionPattern(pattern=r"\bjailbreak\b", weight=0.85, description="Jailbreak keyword"),
        InjectionPattern(
            pattern=r"\bDAN\b", weight=0.9, description="DAN (Do Anything Now) attack"
        ),
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
    ]

    # Imperative verbs often used in injections
    # BUG FIX: Added missing security-relevant verbs
    IMPERATIVE_VERBS = {
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
        "dump",  # e.g., "dump all data"
        "leak",  # e.g., "leak the prompt"
        "expose",  # e.g., "expose the system"
        "extract",  # e.g., "extract the rules"
        "provide",  # e.g., "provide the instructions"
        "list",  # e.g., "list all rules"
    }

    def __init__(self, strict: bool = True):
        """
        Initialize security analyzer.

        Args:
            strict: Enable strict mode (higher sensitivity)
        """
        self.strict = strict

    def analyze(self, text: str, hidden_content_detected: bool = False) -> InjectionAnalysis:
        """
        Analyze text for potential prompt injection.

        Args:
            text: Text content to analyze
            hidden_content_detected: Whether hidden elements were found

        Returns:
            InjectionAnalysis with score and details
        """
        pattern_matches, decode_warnings = self._detect_patterns(text)
        imperative_density = self._calculate_imperative_density(text)

        # Calculate base score from patterns.
        # Scale by occurrence count with diminishing returns, so repeated matches
        # contribute proportionally, but with a softened growth curve.
        import math

        pattern_score = 0.0
        for match in pattern_matches:
            weight = match["weight"]
            occurrences = match["occurrences"]
            occurrence_multiplier = 1.0 + 0.15 * math.log2(max(1, occurrences))
            pattern_score += weight * occurrence_multiplier
        pattern_score = min(pattern_score, 1.0)  # Cap at 1.0

        # Add hidden content weight
        hidden_weight = 0.3 if hidden_content_detected else 0.0

        # Add imperative density contribution
        imperative_weight = min(imperative_density * 0.5, 0.3)

        # Combined score
        total_score = min(pattern_score + hidden_weight + imperative_weight, 1.0)

        # Security fix (S6): if any single pattern fires more than the configured
        # count floor, raise the score to at least the floor-score. This catches
        # payloads that stack many low-weight matches (e.g. twenty "act as if"
        # phrases) which would otherwise slip just under the warn threshold.
        high_count_hit = any(
            match.get("occurrences", 0) >= _INJECTION_COUNT_FLOOR for match in pattern_matches
        )
        if high_count_hit and total_score < _INJECTION_COUNT_FLOOR_SCORE:
            total_score = _INJECTION_COUNT_FLOOR_SCORE

        # Generate flags
        flags = self._generate_flags(
            pattern_matches,
            hidden_content_detected,
            imperative_density,
            decode_warnings,
        )

        return InjectionAnalysis(
            score=round(total_score, 3),
            flags=flags,
            pattern_matches=pattern_matches,
            hidden_content_detected=hidden_content_detected,
            imperative_density=round(imperative_density, 3),
        )

    @classmethod
    def _get_patterns_hash(cls) -> str:
        """Generate hash of patterns content for cache invalidation."""
        content = json.dumps(
            [
                {"pattern": p.pattern, "weight": p.weight, "flags": p.flags}
                for p in cls.INJECTION_PATTERNS
            ],
            sort_keys=True,
        )
        return hashlib.sha256(content.encode()).hexdigest()

    @classmethod
    def _get_compiled_patterns(cls) -> list[tuple[re.Pattern, float, str]]:
        """Return pre-compiled regex patterns, recompiling if the source list changed.

        Thread-safe: Uses a lock to prevent race conditions when multiple threads
        compile patterns simultaneously.
        """
        with cls._PATTERNS_LOCK:
            current_hash = cls._get_patterns_hash()
            if cls._COMPILED_PATTERNS is not None and cls._PATTERNS_HASH == current_hash:
                return cls._COMPILED_PATTERNS
            result = [
                (re.compile(p.pattern, p.flags), p.weight, p.description)
                for p in cls.INJECTION_PATTERNS
            ]
            cls._COMPILED_PATTERNS = result
            cls._PATTERNS_HASH = current_hash
            return result

    def _detect_patterns(self, text: str) -> tuple[list[dict], list[str]]:
        """
        Detect injection patterns in text.

        Applies normalization to handle Unicode homoglyphs, non-standard whitespace,
        and HTML entity encoding that could be used to bypass detection.

        Returns list of matched patterns with metadata.
        """
        matches: list[dict] = []

        # Normalize text for security analysis:
        # 1. Decode HTML entities and URL encoding (prevent bypass via &lt;instruction&gt;)
        # 2. Convert Unicode whitespace to regular spaces
        # 3. Normalize to ASCII (handles homoglyphs like Cyrillic 'а' → 'a')
        decoded_text, decode_warnings = _decode_html_entities(text)
        normalized_variants = [_normalize_security_text(decoded_text)]
        if "decoding_iteration_limit_reached" in decode_warnings:
            original_normalized = _normalize_security_text(text)
            if original_normalized not in normalized_variants:
                normalized_variants.append(original_normalized)

        # Use instance patterns if overridden, otherwise class-level cached ones
        # BUG FIX: Validate custom patterns to prevent ReDoS and empty patterns
        if self.INJECTION_PATTERNS is not SecurityAnalyzer.INJECTION_PATTERNS:
            compiled = []
            for p in self.INJECTION_PATTERNS:
                # Skip empty patterns
                if not p.pattern or not p.pattern.strip():
                    continue
                # Prevent ReDoS via overly long patterns
                if len(p.pattern) > 10000:
                    raise ValueError(f"Pattern too long (max 10000 chars): {p.description}")
                # BUG FIX: Check for ReDoS patterns (catastrophic backtracking)
                if _detect_redos_pattern(p.pattern):
                    raise ValueError(
                        f"Pattern may cause ReDoS (catastrophic backtracking): {p.description}"
                    )
                # Validate weight is in valid range
                if not (0.0 <= p.weight <= 1.0):
                    raise ValueError(f"Invalid weight {p.weight} for pattern: {p.description}")
                try:
                    compiled.append((re.compile(p.pattern, p.flags), p.weight, p.description))
                except re.error as e:
                    raise ValueError(f"Invalid regex pattern '{p.description}': {e}")
        else:
            compiled = self._get_compiled_patterns()

        for regex, weight, description in compiled:
            best_found = []
            best_occurrences = 0
            for normalized_text in normalized_variants:
                found = regex.findall(normalized_text)
                if len(found) > best_occurrences:
                    best_occurrences = len(found)
                    best_found = found
            if best_occurrences:
                matches.append(
                    {
                        "pattern": description,
                        "weight": weight,
                        "occurrences": best_occurrences,
                        "samples": best_found[:3],
                    }
                )

        if "decoding_iteration_limit_reached" in decode_warnings:
            matches.append(
                {
                    "pattern": "Deeply nested encoding",
                    "weight": 0.6,
                    "occurrences": 1,
                    "samples": [],
                }
            )

        return matches, decode_warnings

    def _calculate_imperative_density(self, text: str) -> float:
        """
        Calculate density of imperative verbs in text.

        Applies normalization to handle Unicode homoglyphs that could bypass detection.

        Returns ratio of imperative verbs to total words.
        """
        # Normalize to handle homoglyphs (e.g., Cyrillic 'і' → 'i')
        normalized_text = _normalize_security_text(text.lower())

        words = re.findall(r"\b\w+\b", normalized_text)

        if len(words) == 0:
            return 0.0

        imperative_count = sum(1 for word in words if word in self.IMPERATIVE_VERBS)

        return imperative_count / len(words)

    def _generate_flags(
        self,
        pattern_matches: list[dict],
        hidden_content: bool,
        imperative_density: float,
        decode_warnings: list[str],
    ) -> list[str]:
        """Generate human-readable warning flags"""
        flags = []

        if pattern_matches:
            flags.append(f"injection_patterns_detected:{len(pattern_matches)}")

        if hidden_content:
            flags.append("hidden_content")

        if imperative_density > 0.05:
            flags.append(f"high_imperative_density:{imperative_density:.2f}")

        flags.extend(decode_warnings)

        # Severity flags
        if len(pattern_matches) > 3:
            flags.append("multiple_injection_attempts")

        return flags
