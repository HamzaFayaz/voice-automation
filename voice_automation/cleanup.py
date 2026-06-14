"""Transcript cleanup utilities.

Functions for normalising raw STT output — collapsing whitespace, removing
stuttered duplicate words, capitalising, and merging overlapping partial
transcripts into a single coherent string.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


def clean_transcript(text: str, trailing_space: bool = True, replacements: dict[str, str] | None = None) -> str:
    """Clean up a raw transcript string.

    Processing steps (in order):

    1. Strip leading / trailing whitespace.
    2. Collapse runs of whitespace into a single space.
    3. Remove consecutive duplicate words (case-insensitive) that are
       common artefacts of streaming partial updates.
    4. Apply custom user word replacements (case-insensitive).
    5. Capitalise the first character if it is not already uppercase.
    6. Optionally append a single trailing space for dictation ergonomics.

    Parameters
    ----------
    text:
        Raw transcript text.
    trailing_space:
        If *True*, append one trailing space to non-empty results.
    replacements:
        Optional mapping of case-insensitive words to replace.

    Returns
    -------
    str
        The cleaned transcript, or ``""`` for blank input.
    """
    if not text or not text.strip():
        return ""

    # 1 – strip outer whitespace
    result = text.strip()

    # 2 – collapse internal whitespace
    result = re.sub(r"\s+", " ", result)

    # 3 – remove consecutive duplicate words (case-insensitive)
    #     The regex captures a word and matches immediate repetitions,
    #     preserving punctuation attached to the *last* occurrence.
    result = re.sub(
        r"\b(\w+)((?:\s+\1)+)\b",
        r"\1",
        result,
        flags=re.IGNORECASE,
    )

    # Collapse any whitespace that might have been left around.
    result = re.sub(r"\s+", " ", result).strip()

    if not result:
        return ""

    # 4 – apply user word replacements
    if replacements:
        for word, replacement in replacements.items():
            pattern = re.compile(rf"\b{re.escape(word)}\b", re.IGNORECASE)
            result = pattern.sub(replacement, result)

    # 5 – capitalise first letter
    result = result[0].upper() + result[1:]

    # 6 – optional trailing space
    if trailing_space:
        result += " "

    return result


def merge_partials(partials: list[str]) -> str:
    """Merge a sequence of partial transcripts into one coherent text.

    Overlapping words at the boundary between consecutive partials are
    detected and collapsed so the same phrase is not repeated.

    Parameters
    ----------
    partials:
        Ordered list of partial transcript strings.

    Returns
    -------
    str
        The merged text.
    """
    if not partials:
        return ""

    # Filter out empty / whitespace-only entries.
    cleaned = [p.strip() for p in partials if p and p.strip()]
    if not cleaned:
        return ""

    merged = cleaned[0]

    for i in range(1, len(cleaned)):
        merged = _merge_two(merged, cleaned[i])

    return merged.strip()


def _merge_two(left: str, right: str) -> str:
    """Merge two strings, removing the longest overlapping suffix/prefix.

    If the end of *left* overlaps with the beginning of *right*, the
    overlapping region is included only once.
    """
    if not left:
        return right
    if not right:
        return left

    left_words = left.split()
    right_words = right.split()

    # Find the longest overlap: try matching the last N words of *left*
    # against the first N words of *right*.
    max_overlap = min(len(left_words), len(right_words))
    best = 0

    for length in range(1, max_overlap + 1):
        tail = [w.lower() for w in left_words[-length:]]
        head = [w.lower() for w in right_words[:length]]
        if tail == head:
            best = length

    if best > 0:
        # Keep all of *left* and append the non-overlapping part of *right*.
        return " ".join(left_words + right_words[best:])

    # No overlap – simple concatenation.
    return " ".join(left_words + right_words)
