"""Deterministic scoring bands + phone extraction (plan §10). A single source of
truth for the cold/warm/hot thresholds, so nothing else hardcodes them."""
import re

COLD_MAX_SCORE = 34
WARM_MAX_SCORE = 69

# Valid 2-digit Uzbek mobile and regional operator prefixes
UZ_PREFIXES = frozenset({
    "20", "33", "50", "55", "70", "71", "72", "73", "74", "75", "76", "77", "78", "79", "88", "90", "91", "92", "93", "94", "95", "97", "98", "99"
})

# Matches phone-number-shaped substrings with various separators
_PHONE_CANDIDATE_PATTERN = re.compile(r"(?:\+\s*)?[\d][\d\s\-()./]{5,25}\d|\b\d{9}\b|\b\d{12}\b")


def status_from_score(score: int) -> str:
    if score > WARM_MAX_SCORE:
        return "hot"
    if score > COLD_MAX_SCORE:
        return "warm"
    return "cold"


def normalize_phone_candidate(raw: str) -> str | None:
    """Normalizes a raw candidate string into canonical E.164 format (+998XXXXXXXXX
    for Uzbekistan or +E.164 for international numbers). Returns None if invalid."""
    if not raw:
        return None
    raw = raw.strip()
    is_plus = raw.startswith("+")
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return None

    # 1. Uzbekistan numbers:
    # Full with country code: 998XXXXXXXXX (12 digits)
    if digits.startswith("998") and len(digits) == 12:
        prefix = digits[3:5]
        if prefix in UZ_PREFIXES:
            return f"+{digits}"
    # Local 9-digit: 901234567
    elif len(digits) == 9:
        prefix = digits[0:2]
        if prefix in UZ_PREFIXES:
            return f"+998{digits}"
    # Trunk prefix 8: 8901234567 (10 digits)
    elif digits.startswith("8") and len(digits) == 10:
        prefix = digits[1:3]
        if prefix in UZ_PREFIXES:
            return f"+998{digits[1:]}"
    # Trunk prefix 8998: 8998901234567 (13 digits)
    elif digits.startswith("8998") and len(digits) == 13:
        prefix = digits[4:6]
        if prefix in UZ_PREFIXES:
            return f"+998{digits[4:]}"

    # 2. General International (+E.164 with 10 to 15 digits)
    if is_plus and 10 <= len(digits) <= 15:
        return f"+{digits}"

    return None


_GROUP_RE = re.compile(r"\+?\d+")
_SEGMENT_SPLIT_RE = re.compile(r"\s*[/;\n]\s*|\s+-\s+")


def _sub_candidates(raw: str):
    """Narrower readings of a candidate whose digits as a whole aren't a number.

    The candidate pattern is greedy, so two numbers separated by " / ", or a
    number with a size or quantity stuck to it ("42 901234567"), arrive as one
    blob. The blob is split on separators that clearly end a number, and each
    piece is tried whole, then without its leading groups, then without its
    trailing ones — never an arbitrary run from the middle, which is how
    "1990 1234 5678"-style codes used to turn into phone numbers."""
    for segment in _SEGMENT_SPLIT_RE.split(raw):
        groups = _GROUP_RE.findall(segment)
        if not groups:
            continue
        yield " ".join(groups)
        for start in range(1, len(groups)):
            yield " ".join(groups[start:])
        for end in range(len(groups) - 1, 0, -1):
            yield " ".join(groups[:end])


def extract_valid_phone(text: str | None) -> str | None:
    """Extracts and normalizes the first valid phone number from raw text
    or LLM-proposed phone string. Returns normalized E.164 string or None."""
    if not text:
        return None
    for match in _PHONE_CANDIDATE_PATTERN.finditer(text):
        normalized = normalize_phone_candidate(match.group(0))
        if normalized:
            return normalized
        for sub in _sub_candidates(match.group(0)):
            normalized = normalize_phone_candidate(sub)
            if normalized:
                return normalized
    return None
