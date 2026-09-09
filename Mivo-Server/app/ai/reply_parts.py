"""Splitting one written reply into the two or three messages a person would
actually have sent.

Nobody types a five-line paragraph into Instagram DM. They send a short line,
then another, and the question lands on its own. A single block of text reads as
a machine even when the words are right — and a long one is the message people
stop reading.

The rule this module is built around: **when in doubt, don't split.** Every path
that isn't clearly better as several messages returns the original text
unchanged, and a split that would lose or reorder a single character is thrown
away. The worst outcome here is a customer receiving mangled text, which is far
worse than an unsplit reply.
"""
import re

# Below this, it's already one natural message.
MIN_TOTAL_CHARS = 150

# A fragment shorter than this is a sentence stub, not a message — merge it.
MIN_PART_CHARS = 30

# Instagram tolerates more, but three is where it still reads as a person
# talking rather than a notification burst.
MAX_PARTS = 3

# Sentence end followed by whitespace. Uzbek, Russian and English all break the
# same way here; the lookbehind keeps the punctuation with the sentence it ends.
_SENTENCE_BREAK = re.compile(r"(?<=[.!?…])\s+")
_LINE_BREAK = re.compile(r"\n+")


def _merge_short(chunks: list[str], min_chars: int) -> list[str]:
    """Folds stubs into their neighbour so every part reads as a message."""
    merged: list[str] = []
    for chunk in chunks:
        if merged and len(merged[-1]) < min_chars:
            merged[-1] = f"{merged[-1]} {chunk}"
        else:
            merged.append(chunk)
    # A trailing stub attaches backwards rather than being sent alone.
    if len(merged) > 1 and len(merged[-1]) < min_chars:
        merged[-2] = f"{merged[-2]} {merged[-1]}"
        merged.pop()
    return merged


def _is_faithful(parts: list[str], original: str) -> bool:
    """Every non-space character, in order, exactly once. A split that fails
    this is a bug, and the caller falls back to sending the original."""
    return re.sub(r"\s+", "", " ".join(parts)) == re.sub(r"\s+", "", original)


def split_reply(
    text: str,
    *,
    max_parts: int = MAX_PARTS,
    min_total_chars: int = MIN_TOTAL_CHARS,
    min_part_chars: int = MIN_PART_CHARS,
) -> list[str]:
    """Returns the messages to send, in order. Always at least one."""
    stripped = (text or "").strip()
    if not stripped:
        return []
    if len(stripped) < min_total_chars:
        return [stripped]

    # An explicit line break is the writer telling us where a message ends;
    # prefer it to guessing at sentences.
    chunks = [c.strip() for c in _LINE_BREAK.split(stripped) if c.strip()]
    if len(chunks) < 2:
        chunks = [c.strip() for c in _SENTENCE_BREAK.split(stripped) if c.strip()]

    if len(chunks) < 2:
        return [stripped]

    parts = _merge_short(chunks, min_part_chars)

    # Everything past the cap joins the final message rather than being dropped
    # or sent as a fourth.
    if len(parts) > max_parts:
        parts = parts[: max_parts - 1] + [" ".join(parts[max_parts - 1:])]

    if len(parts) < 2 or not _is_faithful(parts, stripped):
        return [stripped]
    return parts
