"""The retrieval layer: what the copilot is allowed to know.

Every answer in this product has to be grounded in something real. This module
turns the live workspace into a small corpus of documents - one per stock line,
one per perk, one per pricing rule, one per sector preset, one per logged
decision and one per question the business has already asked - and retrieves the
handful of documents that matter for a given question.

Two consequences follow from doing it this way:

* **Nothing is invented.** The assistant can only speak from documents it was
  handed, and each answer carries the ids of the documents it used. That is the
  retrieval half of retrieval-augmented generation.
* **Retrieval runs on the machine.** The index is a TF-IDF matrix built with
  scikit-learn, the same library the classifier already uses, so there is no
  second model to download and no text leaving the box. The optional language
  model is only ever handed the retrieved documents, never the raw database.

The glossary is part of the corpus on purpose. A small-business owner asking
"what does urgency mean" deserves the same grounded answer as one asking about a
batch of croissants, and the assistant is allowed to explain both.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

try:  # imported as a package (tests, tooling)
    from .. import yield_engine
except ImportError:  # pragma: no cover - imported with backend/ on sys.path
    import yield_engine


# Plain-English definitions for every term the engine uses internally. The owner
# never has to learn "urgency score" to run a shop, so each term ships with the
# sentence that explains it.
GLOSSARY: dict[str, str] = {
    "margin floor": (
        "The lowest share of the shelf price this business will accept. Below it, "
        "the sale costs more than it earns, so the engine refuses to go there."
    ),
    "markdown": (
        "Cutting the price shown on the menu or shelf. ValueAnchor avoids these "
        "because customers learn to wait for them."
    ),
    "velocity index": (
        "How much of today's stock has already sold, as a share of what was made. "
        "66% means about two thirds of the batch is gone."
    ),
    "urgency score": (
        "A single number that rises when a line is both short on time and still "
        "carrying stock. The higher it is, the earlier you should deal with it."
    ),
    "risk status": (
        "How much trouble a line is in. Healthy means it should sell out on its "
        "own, Watch means keep an eye on it, Critical means act today."
    ),
    "sell-through": "The share of everything made today that has actually sold.",
    "exposure": (
        "The money still sitting on the shelf on lines that could lapse. It is "
        "what you lose today if nothing changes."
    ),
    "perk": (
        "Something extra you give the customer instead of a price cut - a free "
        "cookie, a spare hour, a next-visit credit. It costs you less than the "
        "discount it replaces."
    ),
    "tier": (
        "One step of a bundle, from the small extra to the biggest one the margin "
        "can still afford."
    ),
    "funded": (
        "Paid for out of today's margin, so it never takes the line below the "
        "floor."
    ),
    "deferred": (
        "Earned on the customer's next visit rather than charged against today, "
        "so today's margin stays whole."
    ),
    "withheld": (
        "Left out on purpose because it would have broken the floor. It is shown "
        "so you can see what was considered."
    ),
    "audit trail": (
        "The running record of every recommendation you approved or declined, with "
        "the item and the margin it kept."
    ),
    "intent": (
        "Which of the things you can ask this question was. The engine treats a "
        "low-confidence reading as a question back to you rather than a guess."
    ),
    "retrieval": (
        "The step that looks up the stock, perks, rules and past decisions that "
        "are relevant to your question, before anything is written down."
    ),
    "grounded": (
        "Said only from the records shown with the answer, so every number can be "
        "traced back to a line of live data."
    ),
}


@dataclass
class Document:
    """One retrievable fact, with the metadata the UI needs to cite it."""

    doc_id: str
    kind: str
    title: str
    text: str
    detail: str = ""
    weight: float = 1.0
    meta: dict[str, Any] = field(default_factory=dict)

    def as_source(self, score: float) -> dict[str, Any]:
        return {
            "id": self.doc_id,
            "kind": self.kind,
            "title": self.title,
            "detail": self.detail,
            "text": self.text,
            "relevance": round(float(score), 3),
            "itemId": self.meta.get("itemId"),
        }


def _item_documents(profile: dict[str, Any], rows: Iterable[dict[str, Any]]) -> list[Document]:
    docs: list[Document] = []
    for row in rows:
        metrics = yield_engine.item_metrics(row)
        unsold = row["TotalCapacity"] - row["UnitsSold"]
        exposure = unsold * row["BasePrice"]
        floor_value = row["BasePrice"] * profile["marginFloorPct"] / 100
        docs.append(
            Document(
                doc_id=f"item:{row['ItemID']}",
                kind="stock",
                title=row["ItemName"],
                detail=(
                    f"{unsold} of {row['TotalCapacity']} unsold at "
                    f"{yield_engine.money(profile, row['BasePrice'])}, "
                    f"{metrics['hoursRemaining']:.1f} h left, "
                    f"{metrics['velocityIndex']:.0%} sold, {metrics['riskStatus']}"
                ),
                text=(
                    f"{row['ItemName']} is a {row['Category']} line sold at "
                    f"{yield_engine.money(profile, row['BasePrice'])} each. "
                    f"{unsold} of {row['TotalCapacity']} units are unsold, worth "
                    f"{yield_engine.money(profile, exposure)} in total. "
                    f"It has {metrics['hoursRemaining']:.1f} hours of shelf life left and has sold "
                    f"{metrics['velocityIndex']:.0%} of its batch, which puts it in the "
                    f"{metrics['riskStatus']} band with an urgency score of {metrics['urgencyScore']:.3f}. "
                    f"The price floor for this line is {yield_engine.money(profile, floor_value)}, "
                    f"which is {profile['marginFloorPct']}% of its shelf price."
                ),
                weight=1.3,
                meta={"itemId": row["ItemID"], "category": row["Category"]},
            )
        )
    return docs


def _perk_documents(profile: dict[str, Any], perks: Iterable[dict[str, Any]]) -> list[Document]:
    docs: list[Document] = []
    for perk in perks:
        docs.append(
            Document(
                doc_id=f"perk:{perk['PerkID']}",
                kind="perk",
                title=perk["PerkName"],
                detail=(
                    f"{perk['Type']} perk, costs the business "
                    f"{yield_engine.money(profile, perk['CostToBusiness'])} and is worth "
                    f"{yield_engine.money(profile, perk['RetailValue'])} to the customer, "
                    f"{perk['AvailableStock']} available"
                ),
                text=(
                    f"Perk {perk['PerkName']} of type {perk['Type']} can be bundled with a stock line. "
                    f"It costs the business {yield_engine.money(profile, perk['CostToBusiness'])} and the customer "
                    f"receives {yield_engine.money(profile, perk['RetailValue'])} of value, so it gives away "
                    f"{yield_engine.money(profile, perk['RetailValue'] - perk['CostToBusiness'])} of perceived value "
                    f"for {yield_engine.money(profile, perk['CostToBusiness'])} of cost. "
                    f"{perk['AvailableStock']} are in stock."
                ),
                weight=1.1,
                meta={"perkId": perk["PerkID"]},
            )
        )
    return docs


def _rule_documents(profile: dict[str, Any]) -> list[Document]:
    docs = [
        Document(
            doc_id="rule:margin-floor",
            kind="rule",
            title="Margin floor",
            detail=f"{profile['marginFloorPct']}% of shelf price, and markdowns are not offered",
            text=(
                f"The margin floor for {profile['businessName']} is {profile['marginFloorPct']}% of the shelf "
                f"price. Every recommendation is checked against it before it is shown. A bundle is only "
                f"offered while the perk cost leaves the line at or above the floor, and a request to cut "
                f"the price is refused outright when it would go below it."
            ),
            weight=1.4,
        ),
        Document(
            doc_id="rule:bundle-first",
            kind="rule",
            title="Bundles instead of markdowns",
            detail="Add value instead of cutting the price",
            text=(
                "ValueAnchor protects the price by adding value rather than cutting it. A customer is offered "
                "a perk that costs the business less than the discount it replaces, so the line keeps its "
                "shelf price and the business keeps its margin."
            ),
            weight=1.2,
        ),
        Document(
            doc_id="rule:how-answers-are-made",
            kind="rule",
            title="How answers are made",
            detail="Read live data, decide with the local engine, then explain in plain words",
            text=(
                "A question is first read by the local language model on this machine, which decides what "
                "was asked and pulls the item, percentage, money and time out of the sentence. That reading "
                "selects one of the engine's own answers, computed from the live stock. Only then is the "
                "answer put into plain words. The language model is never asked to work out a number, and it "
                "is never allowed to answer a question outside this business's live stock, its perks, its "
                "pricing rules and its decision history."
            ),
            weight=1.5,
        ),
    ]
    for term, meaning in GLOSSARY.items():
        docs.append(
            Document(
                doc_id=f"glossary:{term}",
                kind="glossary",
                title=term.title(),
                detail=meaning,
                text=f"{term}: {meaning}",
                weight=0.9,
                meta={"term": term},
            )
        )
    return docs


def _sector_documents(sectors: dict[str, dict[str, Any]], active: str) -> list[Document]:
    docs: list[Document] = []
    for slug, preset in sectors.items():
        docs.append(
            Document(
                doc_id=f"sector:{slug}",
                kind="sector",
                title=f"{preset['name']} - {preset['businessName']}",
                detail=(
                    f"{preset['blurb']} - {len(preset['items'])} lines, "
                    f"{preset['marginFloorPct']}% floor"
                    + (" (active)" if slug == active else "")
                ),
                text=(
                    f"Business preset {preset['name']} runs as {preset['businessName']}, which sells "
                    f"{preset['inventoryNoun']}. It has a {preset['marginFloorPct']}% margin floor and "
                    f"{len(preset['items'])} seeded lines. {preset['blurb']}"
                ),
                weight=1.0 if slug == active else 0.6,
                meta={"sector": slug, "active": slug == active},
            )
        )
    return docs


def _decision_documents(profile: dict[str, Any], history: Iterable[dict[str, Any]]) -> list[Document]:
    docs: list[Document] = []
    for entry in history:
        label = entry.get("ItemLabel") or f"Item {entry.get('ItemID')}"
        docs.append(
            Document(
                doc_id=f"decision:{entry.get('StrategyID')}",
                kind="decision",
                title=f"{label} - {entry.get('ManagerDecision')}",
                detail=(
                    f"{entry.get('ManagerDecision')} on {entry.get('CreatedTimestamp', '')[:10]}, "
                    f"{entry.get('UnitsFilled', 0)} units, kept "
                    f"{yield_engine.money(profile, float(entry.get('MarginRetained') or 0))}"
                ),
                text=(
                    f"On {label}, the engine recommended: {entry.get('RecommendedAction')}. "
                    f"The manager {str(entry.get('ManagerDecision', '')).lower()} it, filling "
                    f"{entry.get('UnitsFilled', 0)} units and retaining "
                    f"{yield_engine.money(profile, float(entry.get('MarginRetained') or 0))} of margin."
                ),
                weight=1.1,
                meta={"itemId": entry.get("ItemID")},
            )
        )
    return docs


def _learned_documents(learned: Iterable[dict[str, Any]]) -> list[Document]:
    docs: list[Document] = []
    for index, example in enumerate(learned):
        docs.append(
            Document(
                doc_id=f"learned:{index}",
                kind="learned",
                title=f"Asked before: {example.get('Question', '')[:60]}",
                detail=(
                    f"Confirmed by the owner as '{example.get('Intent', '')}', "
                    f"{example.get('TimesSeen', 1)} time(s)"
                ),
                text=(
                    f"The owner asked '{example.get('Question')}', which the engine read as "
                    f"'{example.get('Intent')}'. This is confirmed, so it is treated as an example of that."
                ),
                weight=1.2,
                meta={"intent": example.get("Intent")},
            )
        )
    return docs


class KnowledgeBase:
    """A retrievable view of one workspace.

    Built fresh whenever the workspace changes - stock reseeded, sector switched,
    a decision logged, a question learned - because the corpus *is* the live data.
    """

    def __init__(
        self,
        profile: dict[str, Any],
        rows: list[dict[str, Any]],
        perks: list[dict[str, Any]],
        history: list[dict[str, Any]],
        sectors: dict[str, dict[str, Any]],
        learned: list[dict[str, Any]] | None = None,
    ) -> None:
        self.profile = profile
        self.documents: list[Document] = [
            *_rule_documents(profile),
            *_item_documents(profile, rows),
            *_perk_documents(profile, perks),
            *_sector_documents(sectors, profile["sector"]),
            *_decision_documents(profile, history),
            *_learned_documents(learned or []),
        ]
        self.vectorizer = TfidfVectorizer(
            analyzer="word",
            ngram_range=(1, 2),
            sublinear_tf=True,
            min_df=1,
        )
        corpus = [
            f"{doc.title} {doc.detail} {doc.text} " + " ".join(str(value) for value in doc.meta.values())
            for doc in self.documents
        ]
        self._matrix = self.vectorizer.fit_transform(corpus)
        self._weights = np.array([doc.weight for doc in self.documents], dtype=float)

    def retrieve(self, query: str, k: int = 6, focus_item_id: int | None = None) -> list[dict[str, Any]]:
        """Return the ``k`` records to ground an answer to ``query``.

        Two things happen beyond plain similarity search, and both are there to
        make the grounding reliable rather than lucky:

        * **The answer's subject is pinned.** When the planner has already decided
          which line the answer is about, that line's record is included even if
          the wording of the question scored it low. The assistant explaining a
          figure must be able to see the record the figure came from.
        * **An unmatched question still gets records.** A short question often
          shares no vocabulary with any document, and returning the \"best\" of
          nothing would leave the assistant ungrounded. In that case the pricing
          rules and the most exposed lines are returned instead.
        """
        scores = cosine_similarity(self.vectorizer.transform([query or " "]), self._matrix)[0] * self._weights
        order = np.argsort(scores)[::-1]

        hits: list[tuple[Document, float]] = []
        seen: set[str] = set()

        if focus_item_id is not None:
            pin = next(
                (
                    doc
                    for doc in self.documents
                    if doc.kind == "stock" and doc.meta.get("itemId") == focus_item_id
                ),
                None,
            )
            if pin is not None:
                index = self.documents.index(pin)
                hits.append((pin, float(scores[index])))
                seen.add(pin.doc_id)

        for index in order:
            if len(hits) >= k:
                break
            score = float(scores[int(index)])
            if score <= 0.0:
                break
            doc = self.documents[int(index)]
            if doc.doc_id in seen:
                continue
            seen.add(doc.doc_id)
            hits.append((doc, score))

        # Enough to answer from, or everything there is to fall back on.
        if len(hits) < min(3, k):
            for doc, score in self._default_hits(k + len(hits)):
                if len(hits) >= k:
                    break
                if doc.doc_id in seen:
                    continue
                seen.add(doc.doc_id)
                hits.append((doc, score))

        return [doc.as_source(score) for doc, score in hits[:k]]

    def _default_hits(self, k: int) -> list[tuple[Document, float]]:
        """What to retrieve when the wording matches nothing: the rules, then the lines."""
        hits = [(doc, 0.35) for doc in self.documents if doc.kind == "rule"]
        hits += [(doc, 0.25) for doc in self.documents if doc.kind == "stock"]
        hits += [(doc, 0.15) for doc in self.documents if doc.kind == "perk"]
        return hits[:k]

    def glossary_for(self, text: str) -> list[dict[str, str]]:
        """Plain-English definitions for the jargon that appears in ``text``.

        The owner should not have to know what "urgency score" means to read an
        answer, so every term used is handed back with its definition and the UI
        can show it inline.
        """
        lowered = (text or "").lower()
        found: list[dict[str, str]] = []
        for term, meaning in GLOSSARY.items():
            if term in lowered:
                found.append({"term": term, "meaning": meaning})
        return found

    def summary(self) -> dict[str, Any]:
        kinds: dict[str, int] = {}
        for doc in self.documents:
            kinds[doc.kind] = kinds.get(doc.kind, 0) + 1
        return {
            "documents": len(self.documents),
            "byKind": kinds,
            "glossaryTerms": len(GLOSSARY),
            "terms": int(self._matrix.shape[1]),
            "builtFrom": "live inventory, perks, pricing rules, sector presets, decision history and confirmed questions",
        }
