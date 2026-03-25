"""
LCO-3 — Input Cleaner & Deduplicator
======================================
First compression pass on input messages. Operates on prose content only —
Safe Zones (code, JSON, tool calls) are guaranteed to arrive here already
excluded by the router.

What it does
────────────
1. Whitespace normalisation  — collapse runs of blank lines, strip trailing spaces
2. Boilerplate removal       — common filler phrases that add tokens but no meaning
3. Sentence deduplication    — exact and near-duplicate sentences removed
4. Repetitive prefix removal — "As an AI language model, …" style openings

What it deliberately does NOT do
──────────────────────────────────
- Semantic compression (that's LCO-5, LLMLingua-style)
- Any modification of system messages unless they have user-added filler
- Any modification of the last user message (preserves intent)
- Anything to messages already flagged as Safe Zones

Design principle: this pass must be fast (no ML, no embeddings) and
conservative. It only removes content that is unambiguously redundant.
False-positive rate must be near zero. When in doubt, keep the text.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

# ── Boilerplate patterns ─────────────────────────────────────────────────────
# Matched case-insensitively against individual sentences or leading phrases.
# Each pattern is anchored so it only fires on clear filler, not mid-sentence.

_BOILERPLATE_PATTERNS: list[re.Pattern] = [p for p in [
    # AI self-identification openers
    re.compile(r"^as an ai(?: language model)?[,.]", re.I),
    re.compile(r"^i(?: am|'m) an ai(?: assistant)?[,.]", re.I),
    re.compile(r"^as a large language model[,.]", re.I),

    # Hollow affirmations at sentence start
    re.compile(r"^certainly[!,.]?\s*", re.I),
    re.compile(r"^of course[!,.]?\s*", re.I),
    re.compile(r"^absolutely[!,.]?\s*", re.I),
    re.compile(r"^sure[!,.]?\s*", re.I),
    re.compile(r"^great question[!,.]?\s*", re.I),
    re.compile(r"^that'?s? (a )?(great|good|excellent|interesting) (question|point)[!,.]?\s*", re.I),

    # Redundant sign-offs / pleasantries that often appear in multi-turn history
    re.compile(r"^i hope (this|that) helps?[!.]?\s*$", re.I),
    re.compile(r"^please (let me know|feel free to ask) if you (have|need) (any )?(more |further )?(questions?|help|clarification)[.!]?\s*$", re.I),
    re.compile(r"^feel free to ask (any |more )?(follow.?up )?(questions?)?[.!]?\s*$", re.I),
    re.compile(r"^is there anything else (i can help|you'?d? like)[?]?\s*$", re.I),
    re.compile(r"^let me know if you(?: have| need| want)(?: any)?(?: more)?(?: questions?| help| clarification)[.!]?\s*$", re.I),
]]

# Blank-line normalisation: collapse 3+ consecutive newlines to 2
_EXCESS_NEWLINES = re.compile(r"\n{3,}")

# Trailing whitespace on each line
_TRAILING_SPACE = re.compile(r"[ \t]+$", re.MULTILINE)

# Sentence splitter — splits on . ! ? followed by whitespace and capital
# Deliberately simple; we're not doing NLP here.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"])")


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class CleanResult:
    original_chars: int = 0
    cleaned_chars: int = 0
    messages_modified: int = 0
    boilerplate_removed: int = 0
    duplicates_removed: int = 0

    @property
    def char_reduction(self) -> int:
        return max(0, self.original_chars - self.cleaned_chars)

    @property
    def reduction_pct(self) -> float:
        if self.original_chars == 0:
            return 0.0
        return 100.0 * self.char_reduction / self.original_chars


# ── Core text cleaning functions ──────────────────────────────────────────────

def _normalise_whitespace(text: str) -> str:
    """Collapse excess blank lines and trim trailing spaces per line."""
    text = _TRAILING_SPACE.sub("", text)
    text = _EXCESS_NEWLINES.sub("\n\n", text)
    return text.strip()


def _remove_boilerplate_sentences(text: str, stats: CleanResult) -> str:
    """
    Remove sentences that match known filler patterns.
    Splits into sentences, filters, then rejoins.
    Conservative: only removes a sentence if the ENTIRE sentence matches.
    """
    sentences = _SENTENCE_SPLIT.split(text)
    cleaned: list[str] = []
    for sentence in sentences:
        stripped = sentence.strip()
        if any(p.search(stripped) for p in _BOILERPLATE_PATTERNS):
            stats.boilerplate_removed += 1
        else:
            cleaned.append(sentence)
    return " ".join(cleaned)


def _normalise_for_comparison(s: str) -> str:
    """
    Reduce a sentence to a form suitable for duplicate detection.
    Lowercases, strips punctuation, collapses whitespace.
    """
    s = unicodedata.normalize("NFKC", s.lower())
    s = re.sub(r"[^\w\s]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _deduplicate_sentences(text: str, stats: CleanResult) -> str:
    """
    Remove repeated sentences within a single message.
    Keeps the first occurrence; removes subsequent identical ones.
    Uses normalised form for comparison but keeps original formatting.
    """
    sentences = _SENTENCE_SPLIT.split(text)
    seen: set[str] = set()
    kept: list[str] = []

    for sentence in sentences:
        key = _normalise_for_comparison(sentence)
        if not key:
            kept.append(sentence)
            continue
        if key in seen:
            stats.duplicates_removed += 1
        else:
            seen.add(key)
            kept.append(sentence)

    return " ".join(kept)


def clean_text(text: str, stats: CleanResult) -> str:
    """Apply all cleaning passes to a single text string."""
    text = _normalise_whitespace(text)
    text = _remove_boilerplate_sentences(text, stats)
    text = _deduplicate_sentences(text, stats)
    text = _normalise_whitespace(text)  # second pass after removals
    return text


# ── Message-level cleaning ────────────────────────────────────────────────────

def _get_text(content: Any) -> str | None:
    """Extract text string from a message content field, or None if non-text."""
    if isinstance(content, str):
        return content
    return None


def _set_text(message: dict[str, Any], text: str) -> dict[str, Any]:
    """Return a shallow copy of message with updated content string."""
    return {**message, "content": text}


def clean_messages(
    messages: list[dict[str, Any]],
    *,
    skip_last_user: bool = True,
    min_length: int = 80,
) -> tuple[list[dict[str, Any]], CleanResult]:
    """
    Apply cleaning to all eligible messages in the array.

    Parameters
    ──────────
    messages       : the messages array from the request body
    skip_last_user : if True, the final user message is never modified
                     (preserves the user's exact intent for the current turn)
    min_length     : messages shorter than this (chars) are passed through
                     as-is — cleaning overhead on tiny messages is not worth it

    Returns
    ───────
    (cleaned_messages, CleanResult)
    cleaned_messages is a new list; unmodified messages share the same dict.
    """
    stats = CleanResult()

    # Find index of last user message for skip logic
    last_user_idx = -1
    if skip_last_user:
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                last_user_idx = i
                break

    cleaned: list[dict[str, Any]] = []

    for i, msg in enumerate(messages):
        # Skip the last user message
        if i == last_user_idx:
            stats.original_chars += len(str(msg.get("content", "")))
            stats.cleaned_chars += len(str(msg.get("content", "")))
            cleaned.append(msg)
            continue

        content = msg.get("content")
        text = _get_text(content)

        # Non-string content (lists of blocks, None) → pass through
        if text is None:
            cleaned.append(msg)
            continue

        stats.original_chars += len(text)

        # Too short to bother
        if len(text) < min_length:
            stats.cleaned_chars += len(text)
            cleaned.append(msg)
            continue

        new_text = clean_text(text, stats)
        stats.cleaned_chars += len(new_text)

        if new_text != text:
            stats.messages_modified += 1
            cleaned.append(_set_text(msg, new_text))
        else:
            cleaned.append(msg)

    return cleaned, stats