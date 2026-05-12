"""
Security static data — homoglyphs, regex constants, and the InjectionPattern dataclass.

This module holds all pure-data definitions so that ``core/security.py`` stays focused
on logic.  Every name defined here is re-exported by ``core/security.py``, so existing
callers that import from ``markdown_ingress.core.security`` continue to work unchanged.
"""

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Homoglyph mapping
# ---------------------------------------------------------------------------

# Visually similar Unicode characters → ASCII equivalents.
# Maps Cyrillic, Greek, and other lookalike characters to their ASCII counterparts.
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
    # BUG FIX: Add Mathematical Alphanumeric Symbols (U+1D400-U+1D7FF)
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

# ---------------------------------------------------------------------------
# Regex constants
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Unicode whitespace pattern
# ---------------------------------------------------------------------------

# Unicode whitespace characters that should match \s in patterns.
# BUG FIX: Added missing whitespace characters (NEL, LRM/RLM, ZWNBSP)
# Includes: NBSP, various space widths, line/paragraph separators, etc.
UNICODE_WHITESPACE_PATTERN = (
    r"[\s\u00A0\u1680\u2000-\u200B\u2028\u2029\u202F\u205F\u3000\u0085\u200E\u200F\uFEFF]"
)

# ---------------------------------------------------------------------------
# InjectionPattern dataclass
# ---------------------------------------------------------------------------


@dataclass
class InjectionPattern:
    """Pattern definition for injection detection"""

    pattern: str
    weight: float
    description: str
    flags: int = re.IGNORECASE
