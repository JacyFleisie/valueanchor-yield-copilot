"""Domain lexicon for the custom NLU layer.

The same words mean different things in different businesses, so the lexicon is
rebuilt from the active sector's live inventory whenever the sector changes. It
supplies what the rest of the pipeline needs:

* canonical entity names (inventory items and categories) plus the aliases a
  manager might actually type for them,
* the domain word list SymSpell uses for spelling correction, so "croisant"
  repairs to "croissant" before intent classification,
* the sector's own vocabulary (its noun for stock, its business name) so
  training sentences and answers are written in the language of the business.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from rapidfuzz import fuzz, process

# Words the assistant must never "correct" away, plus filler to drop.
GENERIC_WORDS = {
    "the", "a", "an", "of", "and", "or", "for", "with", "without", "to", "in", "on", "at", "by", "from",
    "is", "are", "was", "were", "be", "been", "do", "does", "did", "can", "could", "should", "would",
    "will", "may", "might", "must", "have", "has", "had", "we", "us", "our", "you", "your", "i", "me",
    "my", "it", "its", "this", "that", "these", "those", "them", "they", "he", "she", "there", "here",
    "what", "which", "who", "whom", "whose", "when", "where", "why", "how", "much", "many", "more",
    "most", "less", "least", "some", "any", "all", "every", "each", "no", "not", "none", "today",
    "tonight", "now", "soon", "before", "after", "close", "closing", "end", "day", "time", "left",
    "remaining", "risk", "at", "up", "down", "out", "over", "under", "about", "than", "then", "just",
    "please", "show", "tell", "give", "need", "want", "get", "make", "take", "let", "get", "still",
    "first", "next", "last", "top", "best", "worst", "help", "hello", "hi", "hey", "thanks", "thank",
    "good", "morning", "afternoon", "evening", "ok", "okay", "yes", "no", "new", "old", "same", "other",
    "margin", "floor", "price", "prices", "discount", "discounts", "bundle", "bundles", "offer",
    "offers", "perk", "perks", "waste", "wasted", "spoilage", "spoil", "throw", "thrown", "unsold",
    "leftover", "leftovers", "sell", "sold", "selling", "stock", "inventory", "capacity", "item",
    "items", "line", "lines", "batch", "batches", "bake", "bakes", "slot", "slots", "seat", "seats",
    "room", "rooms", "cover", "covers", "unit", "units", "value", "revenue", "cost", "costs", "profit",
    "protect", "protected", "safe", "safer", "push", "move", "clear", "clearing", "recover", "versus",
    "vs", "compare", "comparison", "between", "difference", "better", "worse", "should", "could",
    "recommend", "recommendation", "advice", "suggest", "suggestion", "action", "actions", "plan",
    "forecast", "project", "projection", "expect", "expecting", "likely", "percent", "percentage",
    "hour", "hours", "minute", "minutes", "week", "month", "year", "rand", "zar", "business", "shop",
    "store", "cafe", "café", "sector", "industry", "preset", "manager", "decisions", "decision",
    "approved", "declined", "approve", "decline", "history", "trend", "trends", "average", "total",
    "summary", "report", "status", "situation", "picture", "overview", "help", "capable", "capabilities",
    "do", "does", "your", "name", "you", "who", "are", "list", "see", "check", "look", "looking",
}


def normalise(text: str) -> str:
    """Lowercase, collapse whitespace, keep digits and the few symbols we parse."""
    cleaned = text.lower().replace("’", "'")
    cleaned = re.sub(r"[^a-z0-9%.,'\-\s]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def singular(word: str) -> str:
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("ses") and len(word) > 4:
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


@dataclass
class ItemEntry:
    item_id: int
    name: str
    category: str
    aliases: list[str] = field(default_factory=list)


@dataclass
class Lexicon:
    """Everything the pipeline knows about the active business's vocabulary."""

    item_entries: list[ItemEntry]
    categories: list[str]
    noun: str
    business_name: str
    sector_name: str
    item_label: str
    margin_floor_pct: int
    currency_symbol: str
    words: set[str]

    @property
    def item_names(self) -> list[str]:
        return [entry.name for entry in self.item_entries]

    def words_for_spelling(self) -> dict[str, int]:
        """Word -> frequency, used to build the SymSpell dictionary."""
        frequencies: dict[str, int] = {word: 100 for word in GENERIC_WORDS if len(word) > 1}
        for entry in self.item_entries:
            for token in re.split(r"[^a-z0-9]+", entry.name.lower()):
                if len(token) > 2:
                    frequencies[token] = frequencies.get(token, 0) + 500
        for category in self.categories:
            for token in re.split(r"[^a-z0-9]+", category.lower()):
                if len(token) > 2:
                    frequencies[token] = frequencies.get(token, 0) + 400
        for token in re.split(r"[^a-z0-9]+", f"{self.noun} {self.sector_name} {self.business_name}".lower()):
            if len(token) > 2:
                frequencies[token] = frequencies.get(token, 0) + 300
        return frequencies

    def match_item(self, text: str) -> dict[str, Any] | None:
        """Fuzzy-match a phrase against item names and aliases.

        Matching is deliberately conservative. "last night" and "flat white"
        share no word, so a character-level resemblance used to be enough to
        invent a line item; a candidate now has to share a real word with the
        query, or be a near-exact character match that only differs by a typo.
        Ties go to nobody: two items scoring the same means the phrase was not
        specific enough to act on.
        """
        query = normalise(text)
        if not query:
            return None
        candidates: list[tuple[str, ItemEntry]] = []
        for entry in self.item_entries:
            for alias in entry.aliases:
                candidates.append((alias, entry))
        if not candidates:
            return None
        scored: dict[int, tuple[float, ItemEntry, str]] = {}
        for alias, entry in candidates:
            score = _pair_score(query, alias)
            if score <= 0:
                continue
            previous = scored.get(entry.item_id)
            if previous is None or score > previous[0]:
                scored[entry.item_id] = (score, entry, alias)
        if not scored:
            return None
        ranked = sorted(scored.values(), key=lambda row: row[0], reverse=True)
        best = ranked[0]
        runner_up = ranked[1][0] if len(ranked) > 1 else 0.0
        if best[0] < 76 or (best[0] - runner_up < 4 and best[0] < 97):
            return None
        return {
            "type": "item",
            "value": best[1].name,
            "itemId": best[1].item_id,
            "category": best[1].category,
            "score": round(best[0] / 100, 3),
            "matchedOn": best[2],
        }

    def match_category(self, text: str) -> dict[str, Any] | None:
        query = normalise(text)
        if not query or not self.categories:
            return None
        result = process.extractOne(query, self.categories, scorer=fuzz.WRatio)
        if result and result[1] >= 80:
            return {"type": "category", "value": result[0], "score": round(result[1] / 100, 3)}
        return None


def _significant_tokens(text: str) -> list[str]:
    return [
        token
        for token in re.split(r"[^a-z0-9]+", text)
        if len(token) > 2 and token not in GENERIC_WORDS
    ]


def _pair_score(query: str, alias: str) -> float:
    """Score one query against one alias, refusing word-free resemblances.

    A candidate must either share a significant word with the query, or be a
    near-exact character match (a typo). Otherwise "night" would answer to
    "white" and every unrelated sentence would name a product.
    """
    query_words = _significant_tokens(query)
    alias_words = _significant_tokens(alias)
    shared = set(query_words) & set(alias_words)
    character = fuzz.ratio(query, alias)
    if not shared and character < 82:
        return 0.0
    if len(query_words) <= 1 and len(alias_words) <= 1:
        return max(character, fuzz.WRatio(query, alias), fuzz.token_sort_ratio(query, alias))
    return max(character, fuzz.token_sort_ratio(query, alias), fuzz.partial_ratio(query, alias) * 0.95)


def _aliases_for(name: str, category: str) -> list[str]:
    """Generate the phrases a manager plausibly types for one item."""
    lowered = normalise(name)
    tokens = [token for token in re.split(r"[^a-z0-9]+", lowered) if token]
    meaningful = [token for token in tokens if token not in GENERIC_WORDS and not token.isdigit()]
    aliases: set[str] = {lowered}
    if meaningful:
        aliases.add(" ".join(meaningful))
        for token in meaningful:
            if len(token) > 3:
                aliases.add(token)
                aliases.add(singular(token))
                aliases.add(token + "s" if not token.endswith("s") else token)
        for size in range(2, min(3, len(meaningful)) + 1):
            aliases.add(" ".join(meaningful[:size]))
            aliases.add(" ".join(meaningful[-size:]))
    if category:
        category_words = [word for word in re.split(r"[^a-z0-9]+", normalise(category)) if len(word) > 3]
        aliases.update(category_words)
    return sorted(alias for alias in aliases if len(alias) > 2)


def build_lexicon(profile: dict[str, Any], items: Iterable[dict[str, Any]]) -> Lexicon:
    """Build the lexicon from the active business profile and its live lines."""
    entries = [
        ItemEntry(item_id=item["itemId"], name=item["itemName"], category=item["category"], aliases=_aliases_for(item["itemName"], item["category"]))
        for item in items
    ]
    categories = sorted({item["category"] for item in items})
    lexicon = Lexicon(
        item_entries=entries,
        categories=categories,
        noun=profile["inventoryNoun"],
        business_name=profile["businessName"],
        sector_name=profile["name"],
        item_label=profile["itemLabel"],
        margin_floor_pct=profile["marginFloorPct"],
        currency_symbol=profile["currencySymbol"],
        words=set(),
    )
    words: set[str] = set(GENERIC_WORDS)
    for entry in entries:
        words.update(re.split(r"[^a-z0-9]+", entry.name.lower()))
        words.update(entry.aliases)
    for category in categories:
        words.update(re.split(r"[^a-z0-9]+", category.lower()))
    words.update(re.split(r"[^a-z0-9]+", f"{lexicon.noun} {lexicon.sector_name} {lexicon.business_name}".lower()))
    lexicon.words = {word for word in words if len(word) > 2}
    return lexicon
