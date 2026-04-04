"""
Security analysis module - Prompt injection detection
"""

import hashlib
import html
import json
import logging
import re
import threading
import unicodedata
from dataclasses import dataclass
from urllib.parse import unquote

from markdown_ingress.models import InjectionAnalysis

# Homoglyph mapping: visually similar Unicode characters to ASCII equivalents
# Maps Cyrillic, Greek, and other lookalike characters to their ASCII counterparts
_HOMOGLYPH_MAP: dict[str, str] = {
    # Cyrillic to Latin
    '\u0430': 'a',  # Cyrillic small letter a
    '\u0410': 'A',  # Cyrillic capital letter A
    '\u0435': 'e',  # Cyrillic small letter ie (looks like e)
    '\u0415': 'E',  # Cyrillic capital letter IE
    '\u043E': 'o',  # Cyrillic small letter o
    '\u041E': 'O',  # Cyrillic capital letter O
    '\u0440': 'p',  # Cyrillic small letter er (looks like p)
    '\u0420': 'P',  # Cyrillic capital letter ER
    '\u0441': 'c',  # Cyrillic small letter es (looks like c)
    '\u0421': 'C',  # Cyrillic capital letter ES
    '\u0443': 'y',  # Cyrillic small letter u (looks like y)
    '\u0423': 'Y',  # Cyrillic capital letter U
    '\u0456': 'i',  # Cyrillic small letter byelorussian-ukrainian i
    '\u0406': 'I',  # Cyrillic capital letter BYELORUSSIAN-UKRAINIAN I
    '\u0458': 'j',  # Cyrillic small letter je
    '\u0408': 'J',  # Cyrillic capital letter JE
    '\u04BB': 'h',  # Cyrillic small letter shha
    '\u04BA': 'H',  # Cyrillic capital letter SHHA
    '\u0445': 'x',  # Cyrillic small letter ha (looks like x)
    '\u0425': 'X',  # Cyrillic capital letter HA
    '\u0432': 'B',  # Cyrillic small letter ve (looks like B)
    '\u0412': 'B',  # Cyrillic capital letter VE
    '\u043C': 'M',  # Cyrillic small letter em
    '\u041C': 'M',  # Cyrillic capital letter EM
    '\u043D': 'H',  # Cyrillic small letter en (looks like H)
    '\u041D': 'H',  # Cyrillic capital letter EN
    '\u0442': 'T',  # Cyrillic small letter te
    '\u0422': 'T',  # Cyrillic capital letter TE
    # Greek to Latin
    '\u03B1': 'a',  # Greek small letter alpha
    '\u0391': 'A',  # Greek capital letter ALPHA
    '\u03B5': 'e',  # Greek small letter epsilon
    '\u0395': 'E',  # Greek capital letter EPSILON
    '\u03BF': 'o',  # Greek small letter omicron
    '\u039F': 'O',  # Greek capital letter OMICRON
    '\u03C1': 'p',  # Greek small letter rho
    '\u03A1': 'P',  # Greek capital letter RHO
    '\u03C4': 't',  # Greek small letter tau
    '\u03A4': 'T',  # Greek capital letter TAU
    '\u03B9': 'i',  # Greek small letter iota
    '\u0399': 'I',  # Greek capital letter IOTA
    '\u03BA': 'k',  # Greek small letter kappa
    '\u039A': 'K',  # Greek capital letter KAPPA
    '\u03BD': 'v',  # Greek small letter nu
    '\u039D': 'N',  # Greek capital letter NU
    '\u03C5': 'u',  # Greek small letter upsilon
    '\u03A5': 'Y',  # Greek capital letter UPSILON
    '\u03C7': 'x',  # Greek small letter chi
    '\u03A7': 'X',  # Greek capital letter CHI
    # Other common homoglyphs
    '\u0131': 'i',  # Latin small letter dotless i
    '\u0307': '',   # Combining dot above (remove when combined with i)
    '\u2010': '-',  # Hyphen
    '\u2011': '-',  # Non-breaking hyphen
    '\u2012': '-',  # Figure dash
    '\u2013': '-',  # En dash
    '\u2014': '-',  # Em dash
    '\u2015': '-',  # Horizontal bar
    '\u2212': '-',  # Minus sign
    '\uFF0D': '-',  # Fullwidth hyphen-minus
    # BUG FIX: Add fullwidth characters (commonly used in obfuscation)
    '\uFF01': '!',  # Fullwidth exclamation mark
    '\uFF02': '"',  # Fullwidth quotation mark
    '\uFF03': '#',  # Fullwidth number sign
    '\uFF04': '$',  # Fullwidth dollar sign
    '\uFF05': '%',  # Fullwidth percent sign
    '\uFF06': '&',  # Fullwidth ampersand
    '\uFF07': "'",  # Fullwidth apostrophe
    '\uFF08': '(',  # Fullwidth left parenthesis
    '\uFF09': ')',  # Fullwidth right parenthesis
    '\uFF0A': '*',  # Fullwidth asterisk
    '\uFF0B': '+',  # Fullwidth plus sign
    '\uFF0C': ',',  # Fullwidth comma
    '\uFF0E': '.',  # Fullwidth full stop
    '\uFF0F': '/',  # Fullwidth solidus
    '\uFF10': '0',  # Fullwidth digit zero
    '\uFF11': '1',  # Fullwidth digit one
    '\uFF12': '2',  # Fullwidth digit two
    '\uFF13': '3',  # Fullwidth digit three
    '\uFF14': '4',  # Fullwidth digit four
    '\uFF15': '5',  # Fullwidth digit five
    '\uFF16': '6',  # Fullwidth digit six
    '\uFF17': '7',  # Fullwidth digit seven
    '\uFF18': '8',  # Fullwidth digit eight
    '\uFF19': '9',  # Fullwidth digit nine
    '\uFF1A': ':',  # Fullwidth colon
    '\uFF1B': ';',  # Fullwidth semicolon
    '\uFF1C': '<',  # Fullwidth less-than sign
    '\uFF1D': '=',  # Fullwidth equals sign
    '\uFF1E': '>',  # Fullwidth greater-than sign
    '\uFF1F': '?',  # Fullwidth question mark
    '\uFF20': '@',  # Fullwidth commercial at
    '\uFF21': 'A',  # Fullwidth Latin capital letter A
    '\uFF22': 'B',  # Fullwidth Latin capital letter B
    '\uFF23': 'C',  # Fullwidth Latin capital letter C
    '\uFF24': 'D',  # Fullwidth Latin capital letter D
    '\uFF25': 'E',  # Fullwidth Latin capital letter E
    '\uFF26': 'F',  # Fullwidth Latin capital letter F
    '\uFF27': 'G',  # Fullwidth Latin capital letter G
    '\uFF28': 'H',  # Fullwidth Latin capital letter H
    '\uFF29': 'I',  # Fullwidth Latin capital letter I
    '\uFF2A': 'J',  # Fullwidth Latin capital letter J
    '\uFF2B': 'K',  # Fullwidth Latin capital letter K
    '\uFF2C': 'L',  # Fullwidth Latin capital letter L
    '\uFF2D': 'M',  # Fullwidth Latin capital letter M
    '\uFF2E': 'N',  # Fullwidth Latin capital letter N
    '\uFF2F': 'O',  # Fullwidth Latin capital letter O
    '\uFF30': 'P',  # Fullwidth Latin capital letter P
    '\uFF31': 'Q',  # Fullwidth Latin capital letter Q
    '\uFF32': 'R',  # Fullwidth Latin capital letter R
    '\uFF33': 'S',  # Fullwidth Latin capital letter S
    '\uFF34': 'T',  # Fullwidth Latin capital letter T
    '\uFF35': 'U',  # Fullwidth Latin capital letter U
    '\uFF36': 'V',  # Fullwidth Latin capital letter V
    '\uFF37': 'W',  # Fullwidth Latin capital letter W
    '\uFF38': 'X',  # Fullwidth Latin capital letter X
    '\uFF39': 'Y',  # Fullwidth Latin capital letter Y
    '\uFF3A': 'Z',  # Fullwidth Latin capital letter Z
    '\uFF3B': '[',  # Fullwidth left square bracket
    '\uFF3C': '\\', # Fullwidth reverse solidus
    '\uFF3D': ']',  # Fullwidth right square bracket
    '\uFF3E': '^',  # Fullwidth circumflex accent
    '\uFF3F': '_',  # Fullwidth low line
    '\uFF40': '`',  # Fullwidth grave accent
    '\uFF41': 'a',  # Fullwidth Latin small letter a
    '\uFF42': 'b',  # Fullwidth Latin small letter b
    '\uFF43': 'c',  # Fullwidth Latin small letter c
    '\uFF44': 'd',  # Fullwidth Latin small letter d
    '\uFF45': 'e',  # Fullwidth Latin small letter e
    '\uFF46': 'f',  # Fullwidth Latin small letter f
    '\uFF47': 'g',  # Fullwidth Latin small letter g
    '\uFF48': 'h',  # Fullwidth Latin small letter h
    '\uFF49': 'i',  # Fullwidth Latin small letter i
    '\uFF4A': 'j',  # Fullwidth Latin small letter j
    '\uFF4B': 'k',  # Fullwidth Latin small letter k
    '\uFF4C': 'l',  # Fullwidth Latin small letter l
    '\uFF4D': 'm',  # Fullwidth Latin small letter m
    '\uFF4E': 'n',  # Fullwidth Latin small letter n
    '\uFF4F': 'o',  # Fullwidth Latin small letter o
    '\uFF50': 'p',  # Fullwidth Latin small letter p
    '\uFF51': 'q',  # Fullwidth Latin small letter q
    '\uFF52': 'r',  # Fullwidth Latin small letter r
    '\uFF53': 's',  # Fullwidth Latin small letter s
    '\uFF54': 't',  # Fullwidth Latin small letter t
    '\uFF55': 'u',  # Fullwidth Latin small letter u
    '\uFF56': 'v',  # Fullwidth Latin small letter v
    '\uFF57': 'w',  # Fullwidth Latin small letter w
    '\uFF58': 'x',  # Fullwidth Latin small letter x
    '\uFF59': 'y',  # Fullwidth Latin small letter y
    '\uFF5A': 'z',  # Fullwidth Latin small letter z
    '\uFF5B': '{',  # Fullwidth left curly bracket
    '\uFF5C': '|',  # Fullwidth vertical line
    '\uFF5D': '}',  # Fullwidth right curly bracket
    '\uFF5E': '~',  # Fullwidth tilde
    # BUG FIX: Add Armenian homoglyphs (commonly used in obfuscation attacks)
    '\u0561': 'a',  # Armenian small letter ayb (looks like a)
    '\u0531': 'A',  # Armenian capital letter AYB
    '\u0562': 'b',  # Armenian small letter ben (looks like b)
    '\u0532': 'B',  # Armenian capital letter BEN
    '\u0563': 'g',  # Armenian small letter gim (looks like g)
    '\u0533': 'G',  # Armenian capital letter GIM
    '\u0565': 'e',  # Armenian small letter ech (looks like e)
    '\u0535': 'E',  # Armenian capital letter ECH
    '\u0566': 'z',  # Armenian small letter za (looks like z)
    '\u0536': 'Z',  # Armenian capital letter ZA
    '\u0568': 'd',  # Armenian small letter da (looks like d)
    '\u0538': 'D',  # Armenian capital letter DA
    '\u0572': 'r',  # Armenian small letter ra (looks like r)
    '\u0542': 'R',  # Armenian capital letter RA
    '\u0574': 'm',  # Armenian small letter men (looks like m)
    '\u0544': 'M',  # Armenian capital letter MEN
    '\u0576': 'n',  # Armenian small letter nū (looks like n)
    '\u0546': 'N',  # Armenian capital letter NŪ
    '\u0581': 'o',  # Armenian small letter vo (looks like o)
    '\u054D': 'O',  # Armenian capital letter VO
    '\u056F': 'k',  # Armenian small letter kēn (looks like k)
    '\u053F': 'K',  # Armenian capital letter KĒN
    '\u0578': 'u',  # Armenian small letter vo (looks like u)
    '\u0548': 'U',  # Armenian capital letter VO
    # BUG FIX: Add Cherokee homoglyphs (commonly used in obfuscation attacks)
    '\u13A0': 'A',  # Cherokee letter A
    '\u13AA': 'E',  # Cherokee letter E
    '\u13B6': 'I',  # Cherokee letter I
    '\u13C2': 'O',  # Cherokee letter O
    '\u13D7': 'U',  # Cherokee letter U
    '\u13A4': 'H',  # Cherokee letter H
    '\u13A8': 'L',  # Cherokee letter L
    '\u13AE': 'M',  # Cherokee letter M
    '\u13C8': 'R',  # Cherokee letter R
    '\u13CF': 'S',  # Cherokee letter S
    '\u13D3': 'T',  # Cherokee letter T
    # Zero-width characters (security: remove to prevent bypass attacks)
    '\u200C': '',   # Zero-width non-joiner (ZWNJ) - invisible, used to bypass pattern detection
    '\u200D': '',   # Zero-width joiner (ZWJ) - invisible, used to bypass pattern detection
    # BUG FIX: Add Georgian homoglyphs (commonly used in obfuscation attacks)
    '\u10D0': 'a',  # Georgian an (looks like a)
    '\u10D1': 'b',  # Georgian ban (looks like b)
    '\u10D2': 'g',  # Georgian gan (looks like g)
    '\u10D3': 'd',  # Georgian don (looks like d)
    '\u10D4': 'e',  # Georgian en (looks like e)
    '\u10D5': 'v',  # Georgian vin (looks like v)
    '\u10D6': 'z',  # Georgian zen (looks like z)
    '\u10D7': 't',  # Georgian tan (looks like t)
    '\u10D8': 'i',  # Georgian in (looks like i)
    '\u10D9': 'k',  # Georgian kan (looks like k)
    '\u10DA': 'l',  # Georgian las (looks like l)
    '\u10DB': 'm',  # Georgian man (looks like m)
    '\u10DC': 'n',  # Georgian nar (looks like n)
    '\u10DD': 'o',  # Georgian on (looks like o)
    '\u10DE': 'p',  # Georgian par (looks like p)
    '\u10DF': 'zh', # Georgian zh
    '\u10E0': 'r',  # Georgian rae (looks like r)
    '\u10E1': 's',  # Georgian san (looks like s)
    '\u10E2': 't',  # Georgian tarin (looks like t)
    '\u10E3': 'u',  # Georgian un (looks like u)
    # BUG FIX: Add Mathematical Alphanumeric Symbols (U+1D400–U+1D7FF)
    # These are bold/italic/script variants that look like regular Latin characters
    # Full block would be large; include most common variants
    '\U0001D400': 'A',  # Mathematical Bold Capital A
    '\U0001D401': 'B',  # Mathematical Bold Capital B
    '\U0001D402': 'C',  # Mathematical Bold Capital C
    '\U0001D403': 'D',  # Mathematical Bold Capital D
    '\U0001D404': 'E',  # Mathematical Bold Capital E
    '\U0001D405': 'F',  # Mathematical Bold Capital F
    '\U0001D406': 'G',  # Mathematical Bold Capital G
    '\U0001D407': 'H',  # Mathematical Bold Capital H
    '\U0001D408': 'I',  # Mathematical Bold Capital I
    '\U0001D409': 'J',  # Mathematical Bold Capital J
    '\U0001D40A': 'K',  # Mathematical Bold Capital K
    '\U0001D40B': 'L',  # Mathematical Bold Capital L
    '\U0001D40C': 'M',  # Mathematical Bold Capital M
    '\U0001D40D': 'N',  # Mathematical Bold Capital N
    '\U0001D40E': 'O',  # Mathematical Bold Capital O
    '\U0001D40F': 'P',  # Mathematical Bold Capital P
    '\U0001D410': 'Q',  # Mathematical Bold Capital Q
    '\U0001D411': 'R',  # Mathematical Bold Capital R
    '\U0001D412': 'S',  # Mathematical Bold Capital S
    '\U0001D413': 'T',  # Mathematical Bold Capital T
    '\U0001D414': 'U',  # Mathematical Bold Capital U
    '\U0001D415': 'V',  # Mathematical Bold Capital V
    '\U0001D416': 'W',  # Mathematical Bold Capital W
    '\U0001D417': 'X',  # Mathematical Bold Capital X
    '\U0001D418': 'Y',  # Mathematical Bold Capital Y
    '\U0001D419': 'Z',  # Mathematical Bold Capital Z
    '\U0001D41A': 'a',  # Mathematical Bold Small a
    '\U0001D41B': 'b',  # Mathematical Bold Small b
    '\U0001D41C': 'c',  # Mathematical Bold Small c
    '\U0001D41D': 'd',  # Mathematical Bold Small d
    '\U0001D41E': 'e',  # Mathematical Bold Small e
    '\U0001D41F': 'f',  # Mathematical Bold Small f
    '\U0001D420': 'g',  # Mathematical Bold Small g
    '\U0001D421': 'h',  # Mathematical Bold Small h
    '\U0001D422': 'i',  # Mathematical Bold Small i
    '\U0001D423': 'j',  # Mathematical Bold Small j
    '\U0001D424': 'k',  # Mathematical Bold Small k
    '\U0001D425': 'l',  # Mathematical Bold Small l
    '\U0001D426': 'm',  # Mathematical Bold Small m
    '\U0001D427': 'n',  # Mathematical Bold Small n
    '\U0001D428': 'o',  # Mathematical Bold Small o
    '\U0001D429': 'p',  # Mathematical Bold Small p
    '\U0001D42A': 'q',  # Mathematical Bold Small q
    '\U0001D42B': 'r',  # Mathematical Bold Small r
    '\U0001D42C': 's',  # Mathematical Bold Small s
    '\U0001D42D': 't',  # Mathematical Bold Small t
    '\U0001D42E': 'u',  # Mathematical Bold Small u
    '\U0001D42F': 'v',  # Mathematical Bold Small v
    '\U0001D430': 'w',  # Mathematical Bold Small w
    '\U0001D431': 'x',  # Mathematical Bold Small x
    '\U0001D432': 'y',  # Mathematical Bold Small y
    '\U0001D433': 'z',  # Mathematical Bold Small z
    # Italic variants
    '\U0001D434': 'A',  # Mathematical Italic Capital A
    '\U0001D435': 'B',  # Mathematical Italic Capital B
    '\U0001D436': 'C',  # Mathematical Italic Capital C
    '\U0001D437': 'D',  # Mathematical Italic Capital D
    '\U0001D438': 'E',  # Mathematical Italic Capital E
    '\U0001D439': 'F',  # Mathematical Italic Capital F
    '\U0001D43A': 'G',  # Mathematical Italic Capital G
    '\U0001D43B': 'H',  # Mathematical Italic Capital H
    '\U0001D43C': 'I',  # Mathematical Italic Capital I
    '\U0001D43D': 'J',  # Mathematical Italic Capital J
    '\U0001D43E': 'K',  # Mathematical Italic Capital K
    '\U0001D43F': 'L',  # Mathematical Italic Capital L
    '\U0001D440': 'M',  # Mathematical Italic Capital M
    '\U0001D441': 'N',  # Mathematical Italic Capital N
    '\U0001D442': 'O',  # Mathematical Italic Capital O
    '\U0001D443': 'P',  # Mathematical Italic Capital P
    '\U0001D444': 'Q',  # Mathematical Italic Capital Q
    '\U0001D445': 'R',  # Mathematical Italic Capital R
    '\U0001D446': 'S',  # Mathematical Italic Capital S
    '\U0001D447': 'T',  # Mathematical Italic Capital T
    '\U0001D448': 'U',  # Mathematical Italic Capital U
    '\U0001D449': 'V',  # Mathematical Italic Capital V
    '\U0001D44A': 'W',  # Mathematical Italic Capital W
    '\U0001D44B': 'X',  # Mathematical Italic Capital X
    '\U0001D44C': 'Y',  # Mathematical Italic Capital Y
    '\U0001D44D': 'Z',  # Mathematical Italic Capital Z
    '\U0001D44E': 'a',  # Mathematical Italic Small a
    '\U0001D44F': 'b',  # Mathematical Italic Small b
    '\U0001D450': 'c',  # Mathematical Italic Small c
    '\U0001D451': 'd',  # Mathematical Italic Small d
    '\U0001D452': 'e',  # Mathematical Italic Small e
    '\U0001D453': 'f',  # Mathematical Italic Small f
    '\U0001D454': 'g',  # Mathematical Italic Small g
    '\U0001D455': 'h',  # Mathematical Italic Small h
    '\U0001D456': 'i',  # Mathematical Italic Small i
    '\U0001D457': 'j',  # Mathematical Italic Small j
    '\U0001D458': 'k',  # Mathematical Italic Small k
    '\U0001D459': 'l',  # Mathematical Italic Small l
    '\U0001D45A': 'm',  # Mathematical Italic Small m
    '\U0001D45B': 'n',  # Mathematical Italic Small n
    '\U0001D45C': 'o',  # Mathematical Italic Small o
    '\U0001D45D': 'p',  # Mathematical Italic Small p
    '\U0001D45E': 'q',  # Mathematical Italic Small q
    '\U0001D45F': 'r',  # Mathematical Italic Small r
    '\U0001D460': 's',  # Mathematical Italic Small s
    '\U0001D461': 't',  # Mathematical Italic Small t
    '\U0001D462': 'u',  # Mathematical Italic Small u
    '\U0001D463': 'v',  # Mathematical Italic Small v
    '\U0001D464': 'w',  # Mathematical Italic Small w
    '\U0001D465': 'x',  # Mathematical Italic Small x
    '\U0001D466': 'y',  # Mathematical Italic Small y
    '\U0001D467': 'z',  # Mathematical Italic Small z
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


def _normalize_to_ascii(text: str) -> str:
    """Normalize Unicode text to ASCII for pattern matching, handling homoglyphs.

    Converts visually similar Unicode characters to their ASCII equivalents,
    making pattern matching resistant to homoglyph attacks.

    Examples:
        - Cyrillic 'о' (U+043E) → 'o'
        - Cyrillic 'а' (U+0430) → 'a'
        - Greek 'ο' (U+03BF) → 'o'
    """
    compatible = unicodedata.normalize('NFKC', text)
    result = ''.join(_HOMOGLYPH_MAP.get(c, c) for c in compatible)

    # Then normalize to NFD and filter combining marks (for accented chars)
    normalized = unicodedata.normalize('NFD', result)
    # Keep only ASCII characters (removes remaining non-ASCII and combining marks)
    ascii_text = ''.join(c for c in normalized if ord(c) < 128)
    return ascii_text


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
    - Overlapping alternatives: (a|aa)+
    - Greedy wildcards: .*. *.*

    Args:
        pattern: Regex pattern string to check

    Returns:
        True if pattern may cause ReDoS, False if safe
    """
    # Check for consecutive greedy wildcards
    greedy_wildcards = [
        r'\.\*\s*\.\*',   # .*.*
        r'\.\+\s*\.\+',   # .+.+
    ]

    if _NESTED_QUANTIFIER_RE.search(pattern):
        return True

    if _has_overlapping_alternation(pattern):
        return True

    for redos in greedy_wildcards:
        if re.search(redos, pattern):
            return True

    return False


def _decode_javascript_escapes(text: str) -> str:
    text = _JS_UNICODE_BRACE_ESCAPE_RE.sub(lambda match: chr(int(match.group(1), 16)), text)
    text = _JS_UNICODE_ESCAPE_RE.sub(lambda match: chr(int(match.group(1), 16)), text)
    return _JS_HEX_ESCAPE_RE.sub(lambda match: chr(int(match.group(1), 16)), text)


def _decode_css_escapes(text: str) -> str:
    return _CSS_ESCAPE_RE.sub(lambda match: chr(int(match.group(1), 16)), text)


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
    # BUG FIX: Warn if limit reached (potential deeply nested attack)
    if iterations >= max_iterations and prev != current:
        warnings.append("decoding_iteration_limit_reached")
        _logger.warning("Decoding iteration limit reached, content may use deeply nested encoding")
    return current, warnings


# Unicode whitespace characters that should match \s in patterns
# BUG FIX: Added missing whitespace characters (NEL, LRM/RLM, ZWNBSP)
# Includes: NBSP, various space widths, line/paragraph separators, etc.
UNICODE_WHITESPACE_PATTERN = r"[\s\u00A0\u1680\u2000-\u200B\u2028\u2029\u202F\u205F\u3000\u0085\u200E\u200F\uFEFF]"


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
    _PATTERNS_LOCK = threading.Lock()  # Protect _COMPILED_PATTERNS and _PATTERNS_HASH

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
            pattern=r"<\s*instruction\s*/?\s*>", weight=0.9, description="Instruction self-closing tags"
        ),
        InjectionPattern(
            pattern=r"<\s*instruction\s+[^>]*>", weight=0.85, description="Instruction tags with attributes"
        ),
        # BUG FIX: Added critical injection patterns for jailbreak, DAN, and privilege escalation
        InjectionPattern(
            pattern=r"\bjailbreak\b", weight=0.85, description="Jailbreak keyword"
        ),
        InjectionPattern(
            pattern=r"\bDAN\b", weight=0.9, description="DAN (Do Anything Now) attack"
        ),
        InjectionPattern(
            pattern=r"\b(sudo|root)\s+mode\b", weight=0.75, description="Privilege escalation attempt"
        ),
        InjectionPattern(
            pattern=r"\b(escape|break)\s+out\b", weight=0.75, description="Escape attempt"
        ),
        InjectionPattern(
            pattern=r"\b(simulate|imagine)\s+(you\s+are|being)\b", weight=0.5, description="Role-play injection"
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
        "dump",      # e.g., "dump all data"
        "leak",      # e.g., "leak the prompt"
        "expose",    # e.g., "expose the system"
        "extract",   # e.g., "extract the rules"
        "provide",   # e.g., "provide the instructions"
        "list",      # e.g., "list all rules"
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
        pattern_matches = self._detect_patterns(text)
        imperative_density = self._calculate_imperative_density(text)

        # Calculate base score from patterns.
        # Scale weight by occurrence count (diminishing returns via log) so
        # repeated injection patterns are scored higher than single hits.
        import math
        pattern_score = sum(
            match["weight"] * (1.0 + 0.15 * math.log2(max(1, match["occurrences"])))
            for match in pattern_matches
        )
        pattern_score = min(pattern_score, 1.0)  # Cap at 1.0

        # Add hidden content weight
        hidden_weight = 0.3 if hidden_content_detected else 0.0

        # Add imperative density contribution
        imperative_weight = min(imperative_density * 0.5, 0.3)

        # Combined score
        total_score = min(pattern_score + hidden_weight + imperative_weight, 1.0)

        _, decode_warnings = _decode_html_entities(text)

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
            [{"pattern": p.pattern, "weight": p.weight, "flags": p.flags} for p in cls.INJECTION_PATTERNS],
            sort_keys=True,
        )
        return hashlib.sha256(content.encode()).hexdigest()

    @classmethod
    def _get_compiled_patterns(cls) -> list[tuple[re.Pattern, float, str]]:
        """Return pre-compiled regex patterns, recompiling if the source list changed.

        Thread-safe: Uses a lock to prevent race conditions when multiple threads
        compile patterns simultaneously.
        """
        current_hash = cls._get_patterns_hash()
        # Fast path: already compiled and hash matches
        if cls._COMPILED_PATTERNS is not None and cls._PATTERNS_HASH == current_hash:
            return cls._COMPILED_PATTERNS

        # Slow path: need to compile, acquire lock
        with cls._PATTERNS_LOCK:
            # Double-check after acquiring lock (another thread may have compiled)
            if cls._COMPILED_PATTERNS is None or cls._PATTERNS_HASH != current_hash:
                cls._COMPILED_PATTERNS = [
                    (re.compile(p.pattern, p.flags), p.weight, p.description) for p in cls.INJECTION_PATTERNS
                ]
                cls._PATTERNS_HASH = current_hash
        return cls._COMPILED_PATTERNS

    def _detect_patterns(self, text: str) -> list[dict]:
        """
        Detect injection patterns in text.

        Applies normalization to handle Unicode homoglyphs, non-standard whitespace,
        and HTML entity encoding that could be used to bypass detection.

        Returns list of matched patterns with metadata.
        """
        matches = []

        # Normalize text for security analysis:
        # 1. Decode HTML entities and URL encoding (prevent bypass via &lt;instruction&gt;)
        # 2. Convert Unicode whitespace to regular spaces
        # 3. Normalize to ASCII (handles homoglyphs like Cyrillic 'а' → 'a')
        decoded_text, _ = _decode_html_entities(text)
        normalized_text = _normalize_to_ascii(
            re.sub(UNICODE_WHITESPACE_PATTERN, " ", decoded_text)
        )

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
                    raise ValueError(f"Pattern may cause ReDoS (catastrophic backtracking): {p.description}")
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
            found = regex.findall(normalized_text)
            if found:
                matches.append(
                    {
                        "pattern": description,
                        "weight": weight,
                        "occurrences": len(found),
                        "samples": found[:3],
                    }
                )

        return matches

    def _calculate_imperative_density(self, text: str) -> float:
        """
        Calculate density of imperative verbs in text.

        Applies normalization to handle Unicode homoglyphs that could bypass detection.

        Returns ratio of imperative verbs to total words.
        """
        # Normalize to handle homoglyphs (e.g., Cyrillic 'і' → 'i')
        normalized_text = _normalize_to_ascii(text.lower())

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
