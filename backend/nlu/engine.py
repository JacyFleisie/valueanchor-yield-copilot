"""The custom intent engine.

Pipeline for every utterance:

1. **Normalise** - lowercase, strip punctuation we do not parse.
2. **Repair** - SymSpell loaded with its full English dictionary plus this
   business's vocabulary. Only tokens that are *not* real English words are
   corrected, so "needs" is never rewritten as "need" and a stray word can
   never be turned into a product name.
3. **Classify** - word and character n-gram TF-IDF feeding a logistic
   regression. Regularisation is chosen by cross-validation on the training
   templates, and the hold-out score is measured on phrasings the model has
   never seen.
4. **Extract** - regexes for percentages, money, durations and time windows;
   RapidFuzz against the catalogue for items and categories. A fuzzy hit is
   accepted only above a threshold, with a margin over the runner-up, and
   single-word hits need to be unambiguous.
5. **Decide** - a weak top intent, or two intents nearly tied, becomes a
   clarifying question instead of a guess.
6. **Resolve** - pronouns and bare follow-ups are resolved from stored
   conversation state.

Everything runs locally. No weights are downloaded and no text leaves the machine.
"""

from __future__ import annotations

import pathlib
import re
import statistics
import time
from dataclasses import dataclass, field
from typing import Any

import symspellpy
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import cross_val_score
from sklearn.pipeline import FeatureUnion

from .dataset import INTENT_DESCRIPTIONS, INTENT_LABELS, build_training_data
from .lexicon import Lexicon, normalise

ENGLISH_DICTIONARY = pathlib.Path(symspellpy.__file__).parent / "frequency_dictionary_en_82_765.txt"

NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20,
    "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "hundred": 100,
}

NUMBER_WORDS.update({"thirteen": 13, "fourteen": 14, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19})
TENS_WORDS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
UNITS_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9}

# Managers say "knock twenty percent off", not only "knock 20% off". Spelled
# numbers have to be parsed too, or the discount is silently assumed.
SPELLED_PERCENT_RE = re.compile(
    r"\b(?:"
    r"(?P<tens>twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)(?:[ -](?P<unit>one|two|three|four|five|six|seven|eight|nine))?"
    r"|(?P<teen>thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen)"
    r"|(?P<small>ten|eleven|twelve|one|two|three|four|five|six|seven|eight|nine)"
    r")\s+(?:percent|per cent)\b"
)

PERCENT_RE = re.compile(r"(\d{1,3}(?:[.,]\d+)?)\s?(?:%|percent|per cent)")
MONEY_RE = re.compile(r"(?:r|\$)\s?(\d{1,3}(?:[,\s]\d{3})*(?:[.,]\d{1,2})?)(k\b)?|\b(\d{1,3}(?:[,\s]\d{3})*(?:[.,]\d{1,2})?)\s?(rand|zar|rands)\b")
DURATION_RE = re.compile(r"(\d{1,3}(?:[.,]\d+)?)\s?(h\b|hr\b|hrs\b|hour|hours)")
QUANTITY_RE = re.compile(r"(\d{1,4})\s?(units?|items?|covers?|seats?|slots?)")
TIME_WINDOWS = {
    "before close": ("before close", "at close", "by close", "closing time", "end of day", "tonight", "this evening"),
    "today": ("today", "this afternoon", "this morning", "right now"),
    "tomorrow": ("tomorrow",),
    "this week": ("this week", "next few days"),
}
PRONOUN_RE = re.compile(r"\b(it|that|this|those|them|these)\b")
GRID = (1.0, 3.0, 8.0)

# Too weak a top intent, or two intents too close together, is a question before
# an answer. A low bar here means the assistant explains itself instead of
# guessing, which is the right trade for a small owner who cannot read a
# confidence number anyway.
MIN_TOP_CONFIDENCE = 0.26
MIN_INTENT_MARGIN = 0.04
HIGH_CONF = 0.70

# Below this, a sentence that names no item, no percentage, no money, no duration
# and no time window is not a question yet - it is the start of one, and the right
# answer is to ask which line is meant rather than to answer confidently about the
# wrong one. Intents about the assistant itself are exempt: "what can you do" is a
# complete question with no subject in it.
MIN_CONFIDENCE_WITHOUT_SUBJECT = 0.85
SUBJECTLESS_INTENTS = {"greeting", "capabilities", "sector_info", "explain_term", "out_of_scope"}

# Questions that are plainly not about this business, whatever the classifier
# thinks. A shop owner does not ask a yield copilot for a headache remedy or a
# scraper, and with fourteen intents a stray noun like "advice" can score just
# highly enough to be taken seriously. These cues force an off-topic reading
# before classification is even consulted, so the one question that must never
# slip through - the one that is not about the business at all - has a
# deterministic answer rather than a probabilistic one.
#
# Chosen to be impossible to say about stock, prices, perks or decisions. Words
# that could plausibly belong to this trade - temperature, forecast, order,
# market, stock, cover, slot - are deliberately absent.
OFF_TOPIC_CUES = (
    "medical", "medicine", "doctor", "hospital", "headache", "symptom", "symptoms",
    "diagnose", "diagnosis", "prescription", "antibiotic", "therapy", "therapist",
    "legal", "lawyer", "attorney", "lawsuit", "sue", "visa", "immigration",
    "tax", "weather", "rain", "sunny", "humidity",
    "soccer", "football", "cricket", "rugby", "tennis", "basketball", "olympics",
    "world cup", "who won",
    "python", "javascript", "typescript", "html", "css", "regex", "scrape", "scraping",
    "code", "coding", "program", "script", "algorithm", "compiler",
    "bitcoin", "crypto", "cryptocurrency", "ethereum", "forex",
    "election", "president", "politics", "politician", "parliament",
    "celebrity", "gossip", "horoscope", "astrology", "zodiac",
    "movie", "netflix", "album", "concert", "joke", "poem", "riddle",
    "translate", "translation", "homework", "essay",
)
OFF_TOPIC_RE = re.compile(r"\b(?:" + "|".join(OFF_TOPIC_CUES) + r")\b")

# A confirmed example is worth more than a generated template: it is the owner's
# own sentence, confirmed by the owner. Repeating it during training is what makes
# one correction able to change how a phrasing is read.
LEARNED_EXAMPLE_WEIGHT = 6


@dataclass
class ParseResult:
    text: str
    normalised: str
    repaired: str
    intent: str
    confidence: float
    alternatives: list[dict[str, Any]] = field(default_factory=list)
    entities: list[dict[str, Any]] = field(default_factory=list)
    corrections: list[dict[str, str]] = field(default_factory=list)
    needs_clarification: bool = False
    resolved_from_context: bool = False
    confirmed: bool = False
    focus_item_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "normalised": self.normalised,
            "repaired": self.repaired,
            "intent": self.intent,
            "intentConfirmed": self.confirmed,
            "intentLabel": INTENT_LABELS.get(self.intent, self.intent),
            "intentDescription": INTENT_DESCRIPTIONS.get(self.intent, ""),
            "confidence": round(self.confidence, 3),
            "resolvedFromContext": self.resolved_from_context,
            "alternatives": self.alternatives,
            "entities": self.entities,
            "corrections": self.corrections,
            "needsClarification": self.needs_clarification,
            "focusItemId": self.focus_item_id,
        }


def _split_learned(examples: list[tuple[str, str]]) -> tuple[list[str], list[str]]:
    """Flatten confirmed question/intent pairs into parallel training lists.

    Each confirmed example is repeated, because one owner sentence has to count
    for more than one generated template when the two disagree. Without the
    weight, a single correction is a rounding error against a thousand templates
    and the model effectively does not learn from being told it was wrong.
    """
    texts: list[str] = []
    labels: list[str] = []
    for question, intent in examples:
        texts.extend([question] * LEARNED_EXAMPLE_WEIGHT)
        labels.extend([intent] * LEARNED_EXAMPLE_WEIGHT)
    return texts, labels


def build_vectorizer() -> FeatureUnion:
    return FeatureUnion(
        [
            ("word", TfidfVectorizer(analyzer="word", ngram_range=(1, 2), sublinear_tf=True, min_df=1)),
            ("char", TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), sublinear_tf=True, min_df=1)),
        ]
    )


class IntentEngine:
    """Trained intent classifier plus domain entity extraction and LLM routing."""

    def __init__(
        self,
        lexicon: Lexicon,
        holdout_ratio: float = 0.2,
        evaluation_splits: int = 5,
        learned_examples: list[dict[str, Any]] | None = None,
    ) -> None:
        self.lexicon = lexicon
        started = time.perf_counter()
        self.evaluation_splits = evaluation_splits
        # Questions the owner actually asked and then confirmed. These are merged
        # into the training set only - never into the hold-out set - so the score
        # above stays an honest estimate of generalisation while the shipped model
        # still learns this business's own way of asking.
        self.learned_examples = [
            (example["Question"], example["Intent"])
            for example in (learned_examples or [])
            if example.get("Question") and example.get("Intent") in INTENT_LABELS
        ]

        # Regularisation is chosen once, by cross-validation inside one split's
        # training templates. The hold-out set plays no part in that choice.
        first = build_training_data(lexicon, holdout_ratio=holdout_ratio, seed=1)
        train_texts, train_labels = first["train"]
        probe_vectorizer = build_vectorizer()
        probe_features = probe_vectorizer.fit_transform(train_texts)
        folds = max(2, min(4, min(train_labels.count(intent) for intent in set(train_labels))))
        best_c, best_cv = GRID[0], -1.0
        for candidate in GRID:
            probe = LogisticRegression(max_iter=3000, C=candidate, class_weight="balanced")
            score = cross_val_score(probe, probe_features, train_labels, cv=folds).mean()
            if score > best_cv:
                best_c, best_cv = candidate, score
        self.regularisation = best_c
        self.cv_accuracy = round(float(best_cv), 4)

        # Generalisation is measured over several independent splits, because a
        # single split is a coin toss: with the same corpus, one shuffle reports
        # 95% and the next 87%. The mean is reported, and so is the spread, so a
        # number that only looks good on one lucky split cannot pass unnoticed.
        holdout_scores: list[float] = []
        holdout_sizes: list[int] = []
        for seed in range(1, evaluation_splits + 1):
            data = build_training_data(lexicon, holdout_ratio=holdout_ratio, seed=seed)
            texts, labels = data["train"]
            learned_texts, learned_labels = _split_learned(self.learned_examples)
            if learned_texts:
                texts = list(texts) + learned_texts
                labels = list(labels) + learned_labels
            test_texts, test_labels = data["test"]
            vectorizer = build_vectorizer()
            matrix = vectorizer.fit_transform(texts)
            model = LogisticRegression(max_iter=3000, C=best_c, class_weight="balanced").fit(matrix, labels)
            predicted = model.predict(vectorizer.transform(test_texts))
            hit = sum(1 for guess, truth in zip(predicted, test_labels) if guess == truth)
            holdout_scores.append(hit / max(len(test_labels), 1))
            holdout_sizes.append(len(test_texts))

        # The shipped model then learns from every phrasing, including the ones
        # the evaluation held back, plus every question the owner confirmed. The
        # score above already estimated how it behaves on sentences it has never
        # seen, so knowing more cannot flatter it.
        full = build_training_data(lexicon, holdout_ratio=0.0, seed=1)
        all_texts, all_labels = full["train"]
        self.learned_rows = len(self.learned_examples)
        if self.learned_examples:
            extra_texts, extra_labels = _split_learned(self.learned_examples)
            all_texts = list(all_texts) + extra_texts
            all_labels = list(all_labels) + extra_labels
        self.vectorizer = build_vectorizer()
        features = self.vectorizer.fit_transform(all_texts)
        self.classifier = LogisticRegression(max_iter=3000, C=best_c, class_weight="balanced")
        self.classifier.fit(features, all_labels)

        self.classes = list(self.classifier.classes_)
        self.training_ms = round((time.perf_counter() - started) * 1000, 1)
        self.train_rows = len(all_texts)
        self.templates_per_intent = full["templatesPerIntent"]
        self.feature_count = int(features.shape[1])
        self.holdout_accuracy = round(statistics.fmean(holdout_scores), 4)
        self.holdout_std = round(statistics.pstdev(holdout_scores), 4)
        self.holdout_worst = round(min(holdout_scores), 4)
        self.holdout_rows = int(round(statistics.fmean(holdout_sizes)))

        # Spelling: full English dictionary first, then this business's words on
        # top, so domain terms win but ordinary English is left alone.
        self.spell = symspellpy.SymSpell(max_dictionary_edit_distance=2, prefix_length=7)
        self.english_words: set[str] = set()
        english_ceiling = 0
        if ENGLISH_DICTIONARY.exists():
            self.spell.load_dictionary(str(ENGLISH_DICTIONARY), term_index=0, count_index=1)
            lines = [line.split() for line in ENGLISH_DICTIONARY.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.english_words = {parts[0] for parts in lines}
            english_ceiling = max((int(parts[1]) for parts in lines if parts[1].isdigit()), default=0)
        # This business's vocabulary must beat general English when edit distance
        # ties, otherwise "almon" repairs to "salmon" instead of "almond".
        domain_frequency = english_ceiling * 10 + 1_000_000
        for word in lexicon.words_for_spelling():
            self.spell.create_dictionary_entry(word, domain_frequency)

        # The language assistant lives in nlu/assistant.py and is kept out of the
        # classifier on purpose: this class decides what was asked and nothing
        # else, which is what makes it reproducible and testable.


    # -- text repair ------------------------------------------------------
    def repair(self, text: str) -> tuple[str, list[dict[str, str]]]:
        """Repair typos in unknown tokens only.

        Real English words and known domain words are never rewritten, which is
        what stops "drop" becoming "shop" or "joke" becoming "bake".
        """
        corrections: list[dict[str, str]] = []
        repaired: list[str] = []
        for token in text.split():
            if (
                len(token) < 4
                or any(character.isdigit() for character in token)
                or token in self.lexicon.words
                or token in self.english_words
            ):
                repaired.append(token)
                continue
            suggestions = self.spell.lookup(token, symspellpy.Verbosity.TOP, max_edit_distance=2)
            if suggestions and suggestions[0].term != token:
                corrections.append({"from": token, "to": suggestions[0].term})
                repaired.append(suggestions[0].term)
            else:
                repaired.append(token)
        return " ".join(repaired), corrections

    # -- entity extraction ------------------------------------------------
    def extract(self, text: str) -> list[dict[str, Any]]:
        entities: list[dict[str, Any]] = []

        percent_spans: list[tuple[int, int]] = []
        for match in PERCENT_RE.finditer(text):
            value = float(match.group(1).replace(",", "."))
            if 0 < value <= 100:
                percent_spans.append(match.span())
                entities.append({"type": "percent", "value": value, "raw": match.group(0).strip()})

        # Only fill from spelled numbers when the sentence did not already state
        # the percentage in digits, so "cut 20% by twenty percent" cannot double.
        if not percent_spans:
            for match in SPELLED_PERCENT_RE.finditer(text):
                value = 0.0
                if match.group("tens"):
                    value = TENS_WORDS[match.group("tens")] + UNITS_WORDS.get(match.group("unit") or "", 0)
                elif match.group("teen"):
                    value = NUMBER_WORDS[match.group("teen")]
                else:
                    value = NUMBER_WORDS[match.group("small")]
                if 0 < value <= 100:
                    percent_spans.append(match.span())
                    entities.append({"type": "percent", "value": float(value), "raw": match.group(0).strip()})

        for match in MONEY_RE.finditer(text):
            if any(start <= match.start() < end for start, end in percent_spans):
                continue
            digits = (match.group(1) or match.group(3) or "").replace(",", "").replace(" ", "")
            if not digits:
                continue
            try:
                value = float(digits)
            except ValueError:
                continue
            if match.group(2):
                value *= 1000
            if 0 < value <= 1_000_000:
                entities.append({"type": "money", "value": value, "raw": match.group(0).strip()})

        for match in DURATION_RE.finditer(text):
            entities.append({"type": "duration", "value": float(match.group(1).replace(",", ".")), "unit": "hours", "raw": match.group(0).strip()})

        for match in QUANTITY_RE.finditer(text):
            entities.append({"type": "quantity", "value": int(match.group(1)), "raw": match.group(0).strip()})

        for label, phrases in TIME_WINDOWS.items():
            hit = next((phrase for phrase in phrases if phrase in text), None)
            if hit:
                entities.append({"type": "time_window", "value": label, "raw": hit})

        matched_ids: set[int] = set()
        for hit in self._item_spans(text):
            if hit["itemId"] in matched_ids:
                continue
            matched_ids.add(hit["itemId"])
            entities.append(hit)
            if len(matched_ids) >= 2:
                break

        if not matched_ids:
            category_hit = self.lexicon.match_category(text)
            if category_hit:
                entities.append(category_hit)

        return entities

    def _item_spans(self, text: str) -> list[dict[str, Any]]:
        """Find item mentions as contiguous n-grams, preferring the longest match."""
        tokens = text.split()
        candidates: list[tuple[int, dict[str, Any]]] = []
        for size in range(min(4, len(tokens)), 0, -1):
            for start in range(0, len(tokens) - size + 1):
                phrase = " ".join(tokens[start:start + size])
                if len(phrase) < 4:
                    continue
                hit = self.lexicon.match_item(phrase)
                if not hit:
                    continue
                # A bare single word must be an unambiguous, near-exact hit.
                if size == 1 and (len(phrase) < 5 or hit["score"] < 0.9) and phrase not in self.lexicon.words:
                    continue
                enriched = dict(hit)
                enriched["phrase"] = phrase
                enriched["span"] = [start, start + size]
                candidates.append((size, enriched))
        candidates.sort(key=lambda row: (row[0], row[1]["score"]), reverse=True)
        seen: set[int] = set()
        ordered: list[dict[str, Any]] = []
        for _size, hit in candidates:
            if hit["itemId"] in seen:
                continue
            seen.add(hit["itemId"])
            ordered.append(hit)
        return ordered

    def explain_off_topic(self, parsed: ParseResult, profile: dict[str, Any]) -> str:
        """Plain refusal for a question the copilot will not answer.

        Used when the local engine finds no business intent and no business
        entity. The assistant is never consulted in that case, so the refusal is
        deterministic: the same off-topic question always gets the same answer,
        and no off-topic text is ever sent anywhere.
        """
        return (
            f"I only work on {profile['businessName']} and its live stock, so I am not going to answer that "
            "one. Everything I can help with is on the cards below: what needs attention first, what is "
            "heading for waste, what to bundle instead of cutting the price, whether a price cut breaks "
            "your margin floor, what the rest of the day looks like, and what you have approved or turned "
            "down. Tap one, or ask in your own words."
        )

    # -- parsing ----------------------------------------------------------
    def confirm(self, parsed: ParseResult, intent: str) -> ParseResult:
        """Override the classifier with an intent the manager picked explicitly.

        This is what the clarifying chips use: when someone answers "no, I meant
        a bundle", the answer should be a bundle answer. Confidence is recorded
        as certainty because the intent is no longer a guess.
        """
        if intent not in INTENT_LABELS:
            return parsed
        parsed.confidence = 1.0
        parsed.confirmed = True
        parsed.needs_clarification = False
        if intent != parsed.intent:
            parsed.intent = intent
        parsed.alternatives = [
            alternative for alternative in parsed.alternatives if alternative["intent"] != intent
        ]
        return parsed

    def parse(self, text: str, state: dict[str, Any] | None = None) -> ParseResult:
        raw = (text or "").strip()
        normalised = normalise(raw)
        repaired, corrections = self.repair(normalised)
        probabilities = self.classifier.predict_proba(self.vectorizer.transform([repaired]))[0]
        ranked = sorted(zip(self.classes, probabilities), key=lambda row: float(row[1]), reverse=True)
        intent, confidence = str(ranked[0][0]), float(ranked[0][1])
        # Coerced to plain Python types on purpose: these cross the HTTP boundary,
        # and numpy scalars are not JSON-serialisable, so leaking one turns a
        # normal answer into a 500.
        alternatives = [
            {
                "intent": str(name),
                "label": INTENT_LABELS.get(str(name), str(name)),
                "confidence": round(float(score), 3),
            }
            for name, score in ranked[1:4]
        ]

        entities = self.extract(repaired)

        # A cue that cannot belong to this trade overrules the classifier. This is
        # a guardrail, not a heuristic: it can only ever turn a business reading
        # into a refusal, never the other way round.
        if OFF_TOPIC_RE.search(repaired):
            intent, confidence = "out_of_scope", 0.99
            alternatives = [
                option for option in alternatives if option["intent"] != "out_of_scope"
            ][:3]

        # A follow-up is resolved before the decision to ask back, because "what
        # about that one" is only vague when there is nothing to refer to. With a
        # previous turn in the session it has a subject, and asking again would be
        # asking the owner to repeat themselves.
        focus = next((entity for entity in entities if entity["type"] == "item"), None)
        resolved_from_context = False
        if focus is None and state and state.get("lastItemId") and PRONOUN_RE.search(normalised):
            focus = {
                "type": "item",
                "value": state.get("lastItemLabel") or f"Item {state['lastItemId']}",
                "itemId": state["lastItemId"],
                "category": state.get("lastItemCategory") or "",
                "score": 1.0,
                "matchedOn": "conversation context",
                "resolved": True,
            }
            entities.insert(0, focus)
            resolved_from_context = True

        needs_clarification = False
        if intent != "out_of_scope":
            runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
            weak_reading = confidence < MIN_TOP_CONFIDENCE
            too_close = confidence - runner_up < MIN_INTENT_MARGIN and confidence < HIGH_CONF
            # "What now" is as confident as "what is my margin floor" is, so
            # confidence cannot separate them. What can is whether the sentence
            # points at anything: a wrong answer about a named line is a near
            # miss, while a confident answer to a sentence with no subject at all
            # is a guess dressed up as a decision.
            no_subject = (
                intent not in SUBJECTLESS_INTENTS
                and not entities
                and not resolved_from_context
                and confidence < MIN_CONFIDENCE_WITHOUT_SUBJECT
            )
            needs_clarification = bool(weak_reading or too_close or no_subject)

        return ParseResult(
            text=raw,
            normalised=normalised,
            repaired=repaired,
            intent=intent,
            confidence=float(confidence),
            alternatives=alternatives,
            entities=entities,
            corrections=corrections,
            needs_clarification=needs_clarification,
            resolved_from_context=resolved_from_context,
            focus_item_id=focus["itemId"] if focus else None,
        )

    # -- introspection ----------------------------------------------------
    def model_card(self) -> dict[str, Any]:
        return {
            "name": "ValueAnchor local NLU",
            "architecture": "hybrid word + character TF-IDF -> logistic regression",
            "training": {
                "rows": self.train_rows,
                "learnedRows": getattr(self, "learned_rows", 0),
                "holdoutRows": self.holdout_rows,
                "evaluationSplits": self.evaluation_splits,
                "templatesPerIntent": self.templates_per_intent,
                "seconds": round(self.training_ms / 1000, 2),
                "trainedAt": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                "regularisationC": self.regularisation,
            },
            "scores": {
                "holdoutAccuracy": self.holdout_accuracy,
                "holdoutStdDev": self.holdout_std,
                "holdoutWorst": self.holdout_worst,
                "crossValidationAccuracy": self.cv_accuracy,
            },
            "features": self.feature_count,
            "intents": [
                {"intent": intent, "label": INTENT_LABELS[intent], "description": INTENT_DESCRIPTIONS[intent]}
                for intent in self.classes
            ],
            "vocabulary": {
                "items": len(self.lexicon.item_entries),
                "categories": len(self.lexicon.categories),
                "spellingWords": len(self.lexicon.words_for_spelling()),
                "englishWords": len(self.english_words),
            },
            "components": [
                {"name": "symspellpy", "role": "typo repair restricted to unknown tokens"},
                {"name": "rapidfuzz", "role": "fuzzy catalogue matching with ambiguity gating"},
                {"name": "scikit-learn", "role": "TF-IDF vectoriser and logistic-regression intent classifier"},
            ],
        }
