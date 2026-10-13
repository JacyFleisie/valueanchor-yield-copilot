"""Turn a parsed utterance plus live data into an answer, with its working shown.

The intent engine answers *what was asked*. This module answers it: every intent
has a handler that reads the live inventory rows, runs the same yield maths the
rest of the product uses (``yield_engine``), and returns both the prose answer
and a reasoning trace.

Two rules shape every handler:

* Anything with a number in it is computed from database rows, never invented.
  If a figure cannot be derived, the handler says so instead of estimating.
* When the engine is unsure what was asked, the handler asks back. A wrong
  confident answer is worse than a clarifying question.

Keeping the prose here rather than in the HTTP layer means the copilot, any
future CLI, and the tests all get identical answers from identical inputs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

try:  # imported as a package (tests, tooling)
    from .. import yield_engine
except ImportError:  # pragma: no cover - imported with backend/ on sys.path
    import yield_engine

from .engine import ParseResult
from .dataset import INTENT_LABELS, INTENT_HOW


@dataclass
class Reply:
    """The copilot's answer plus everything the UI needs to explain and act on it.

    ``answer`` is what a careful analyst would write. ``headline``, ``meaning``,
    ``next_steps`` and ``key_numbers`` are the same answer written for somebody
    who runs a shop and has thirty seconds: what happened, what it means, what to
    do, and the figures laid out with a label instead of a bare number.
    """

    intent: str
    answer: str
    reasoning: list[str] = field(default_factory=list)
    focus_item_id: int | None = None
    evidence: list[dict[str, Any]] = field(default_factory=list)
    clarify_options: list[dict[str, str]] = field(default_factory=list)
    headline: str = ""
    meaning: str = ""
    next_steps: list[dict[str, str]] = field(default_factory=list)
    key_numbers: list[dict[str, str]] = field(default_factory=list)
    follow_ups: list[str] = field(default_factory=list)
    glossary: list[dict[str, str]] = field(default_factory=list)
    severity: str = "info"
    plain: str = ""
    custom: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "intentLabel": INTENT_LABELS.get(self.intent, self.intent),
            "answer": self.answer,
            "reasoning": self.reasoning,
            "focusItemId": self.focus_item_id,
            "evidence": self.evidence,
            "clarifyOptions": self.clarify_options,
            "how": INTENT_HOW.get(self.intent, ""),
            "headline": self.headline,
            "meaning": self.meaning,
            "nextSteps": self.next_steps,
            "keyNumbers": self.key_numbers,
            "followUps": self.follow_ups,
            "glossary": self.glossary,
            "severity": self.severity,
            "plain": self.plain,
        }


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _sorted_rows(rows: Iterable[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Every line paired with its live metrics, most urgent first."""
    pairs = [(yield_engine.item_metrics(row), row) for row in rows]
    return sorted(pairs, key=lambda pair: pair[0]["urgencyScore"], reverse=True)


def _by_id(rows: list[dict[str, Any]], item_id: int | None) -> dict[str, Any] | None:
    if item_id is None:
        return None
    return next((row for row in rows if row["ItemID"] == item_id), None)


def _entity(parse: ParseResult, kind: str) -> dict[str, Any] | None:
    return next((entity for entity in parse.entities if entity["type"] == kind), None)


def _entities(parse: ParseResult, kind: str) -> list[dict[str, Any]]:
    return [entity for entity in parse.entities if entity["type"] == kind]


def _describe(row: dict[str, Any], metrics: dict[str, Any], profile: dict[str, Any]) -> str:
    unsold = row["TotalCapacity"] - row["UnitsSold"]
    return (
        f"{row['ItemName']} ({row['Category']}): {unsold} of {row['TotalCapacity']} unsold at "
        f"{yield_engine.money(profile, row['BasePrice'])}, {metrics['hoursRemaining']:.1f} h of shelf life, "
        f"velocity {metrics['velocityIndex']:.0%}"
    )


def _value_at_risk(row: dict[str, Any]) -> float:
    return round((row["TotalCapacity"] - row["UnitsSold"]) * row["BasePrice"], 2)


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    """Pluralise a count for owner-facing prose.

    Naively appending ``s`` rendered the coffee preset as "5 batchs", because
    the sector's item label is *Batch*. Words ending in a sibilant take ``-es``
    and a consonant plus ``y`` takes ``-ies``, so the rule lives here instead of
    relying on every caller to remember the plural of its own label.
    """
    if count == 1:
        return f"{count} {singular}"
    if plural:
        return f"{count} {plural}"
    lower = singular.lower()
    if lower.endswith(("s", "x", "z", "ch", "sh")):
        word = f"{singular}es"
    elif lower.endswith("y") and len(singular) > 1 and lower[-2] not in "aeiou":
        word = f"{singular[:-1]}ies"
    else:
        word = f"{singular}s"
    return f"{count} {word}"


# ---------------------------------------------------------------------------
# Intent handlers
# ---------------------------------------------------------------------------
def _priority(pairs, profile, rows):
    metrics, row = pairs[0]
    runner = pairs[1] if len(pairs) > 1 else None
    unsold = row["TotalCapacity"] - row["UnitsSold"]
    answer = (
        f"Start with {row['ItemName']}. {unsold} of {row['TotalCapacity']} are still on the shelf at "
        f"{yield_engine.money(profile, row['BasePrice'])} and there are only {metrics['hoursRemaining']:.1f} hours left, "
        f"so it is the line under the most pressure in the {profile['name'].lower()} "
        f"(urgency {metrics['urgencyScore']:.2f}). That single line is worth "
        f"{yield_engine.money(profile, _value_at_risk(row))} of stock sitting there today, "
        f"so if you do one thing, do this one."
    )
    if runner:
        answer += (
            f" After that, the next line to look at is {runner[1]['ItemName']} "
            f"with {runner[0]['hoursRemaining']:.1f} hours left and urgency {runner[0]['urgencyScore']:.2f}."
        )
    reasoning = [
        f"Ranked all {_plural(len(pairs), 'live line')} by urgency, most urgent first",
        f"{row['ItemName']} tops the list: {unsold} unsold, {metrics['hoursRemaining']:.1f} h left, velocity {metrics['velocityIndex']:.0%}",
        "Urgency gives more weight to lines that are both low on time and still carrying stock, so the top line is the one where acting now moves the most",
    ]
    return Reply("priority", answer, reasoning, row["ItemID"])


def _waste_risk(pairs, profile, rows):
    exposed = [(m, r) for m, r in pairs if m["hoursRemaining"] <= 24]
    total = round(sum(_value_at_risk(r) for _m, r in exposed), 2)
    units = sum(r["TotalCapacity"] - r["UnitsSold"] for _m, r in exposed)
    worst = exposed[:3]
    leader = worst[0][1]["ItemName"] if worst else "nothing"
    answer = (
        f"About {yield_engine.money(profile, total)} of stock value is on lines that will lapse if nothing else happens, "
        f"spread over {_plural(len(exposed), 'line')} and {_plural(units, 'unit')}. {leader} is the biggest single one. "
    )
    if worst:
        answer += "The three lines carrying the most exposure are " + ", ".join(
            f"{r['ItemName']} at {yield_engine.money(profile, _value_at_risk(r))}" for _m, r in worst
        ) + ". Moving one of these with a small free perk usually costs less than the write-off it prevents."
    reasoning = [
        f"Counted every line with 24 hours or less of shelf life: {len(exposed)} of {len(pairs)}",
        f"Exposure is unsold units multiplied by shelf price: {_plural(units, 'unit')} totalling {yield_engine.money(profile, total)}",
        "Waste is the loss you take today if nothing changes, so it is the number to beat with a bundle",
    ]
    return Reply("waste_risk", answer, reasoning, worst[0][1]["ItemID"] if worst else None)


def _margin_floor(parse, pairs, profile, rows, focus):
    floor = profile["marginFloorPct"]
    if focus:
        price = focus["BasePrice"]
        threshold = round(price * floor / 100, 2)
        headroom = round(price - threshold, 2)
        answer = (
            f"On {focus['ItemName']} you will not go below {yield_engine.money(profile, threshold)}. "
            f"That is {floor}% of the {yield_engine.money(profile, price)} shelf price, so the floor itself is "
            f"{yield_engine.money(profile, threshold)} of margin on every unit. "
            f"That leaves {yield_engine.money(profile, headroom)} of room to spend on a perk before the floor is hit, "
            f"and no room at all for a cash markdown."
        )
        reasoning = [
            f"Floor is a property of the {profile['name'].lower()} preset: {floor}%",
            f"{yield_engine.money(profile, price)} x {floor}% = {yield_engine.money(profile, threshold)} minimum acceptable margin on this line",
            f"Room to spend on perks today: {yield_engine.money(profile, headroom)}",
        ]
    else:
        answer = (
            f"For {profile['businessName']} the floor is {floor}% of shelf price. Nothing the engine recommends "
            f"takes a line below it: a bundle is only offered while the perk cost keeps the floor intact, and a "
            f"markdown is refused outright. Ask about a specific item to see its floor in money."
        )
        reasoning = [
            f"Floor is {floor}% for the active {profile['name'].lower()} preset",
            "Every recommendation rechecks the floor before it is returned",
        ]
    return Reply("margin_floor", answer, reasoning, focus["ItemID"] if focus else None)


def _bundle_recommend(pairs, profile, rows, perks, focus):
    _metrics, row = _pair_for(focus, pairs)
    if row is None:
        return _need_item(profile, "bundle")
    plan = yield_engine.bundle_plan(row, perks, profile)
    recommendation = plan["recommendation"]
    tiers = [tier for tier in recommendation["tiers"] if tier["status"] != "withheld"]
    kept = round(recommendation["marginRetained"], 2)
    threshold = round(plan["metrics"]["marginThreshold"], 2)
    answer = (
        f"{recommendation['action']} You keep {yield_engine.money(profile, kept)} of margin against the "
        f"{yield_engine.money(profile, threshold)} floor, so the markdown you were thinking about is not needed "
        f"and the item still sells at full price."
    )
    tier_notes = [
        "{}: {}".format(
            tier["label"],
            "funded from this visit's margin" if tier["status"] == "funded" else "deferred to the next visit",
        )
        for tier in tiers
    ]
    reasoning = [
        f"Floor for {row['ItemName']} is {yield_engine.money(profile, threshold)}",
        *tier_notes,
        (
            f"{len(recommendation['withheldTiers'])} tier(s) withheld: they would break the "
            f"{profile['marginFloorPct']}% floor"
            if recommendation["withheldTiers"]
            else f"Every tier fits above the {profile['marginFloorPct']}% floor, so nothing was withheld"
        ),
    ]
    if not tiers:
        reasoning.append("No perk fits under the floor on this line, so the answer is a price hold with nothing added")
    return Reply("bundle_recommend", answer, reasoning, row["ItemID"], evidence=[{"bundle": plan}])


def _discount_request(parse, pairs, profile, rows, focus):
    percent_entity = _entity(parse, "percent")
    percent = percent_entity["value"] if percent_entity else 10.0
    row = focus or (pairs[0][1] if pairs else None)
    if row is None:
        return _need_item(profile, "discount")
    impact = yield_engine.discount_impact(row, profile, percent)
    if impact["breachesFloor"]:
        answer = (
            f"No. A {percent:.0f}% cut takes {row['ItemName']} to {yield_engine.money(profile, impact['newPrice'])}, "
            f"which is below its {yield_engine.money(profile, impact['floorThreshold'])} floor. On every unit that "
            f"destroys {yield_engine.money(profile, max(0.0, impact['cut']))} of margin, so the floor would not survive. "
            f"Try a bundled perk instead: it pulls the same customer and the floor stays safe."
        )
        verdict = "no, the floor would break"
    else:
        answer = (
            f"A {percent:.0f}% cut on {row['ItemName']} lands at {yield_engine.money(profile, impact['newPrice'])}, "
            f"which is still above the {yield_engine.money(profile, impact['floorThreshold'])} floor, so it is allowed. "
            f"It sets you back {yield_engine.money(profile, impact['cut'])} per unit though, and a bundle usually "
            f"converts better without teaching customers to wait for a markdown."
        )
        verdict = "allowed"
    reasoning = [
        f"Requested markdown: {percent:.0f}% on {row['ItemName']}",
        f"New price {yield_engine.money(profile, impact['newPrice'])} versus floor {yield_engine.money(profile, impact['floorThreshold'])}",
        f"Margin left per unit after the cut: {yield_engine.money(profile, impact['newPrice'] - impact['floorThreshold'])}",
        f"Verdict: {verdict}",
    ]
    return Reply("discount_request", answer, reasoning, row["ItemID"], evidence=[{"discount": impact}])


def _pair_for(focus, pairs):
    row = focus
    if row is None:
        return pairs[0] if pairs else (None, None)
    for metrics, candidate in pairs:
        if candidate["ItemID"] == row["ItemID"]:
            return metrics, candidate
    metrics = yield_engine.item_metrics(row)
    return metrics, row


def _compare_items(parse, pairs, profile, rows):
    mentions = [entity for entity in _entities(parse, "item")]
    if len(mentions) < 2:
        listed = ", ".join(row["ItemName"] for _m, row in pairs[:3])
        return _need_item(profile, "compare", detail=f"I can see {_plural(len(pairs), 'line')}: {listed}.")
    left_id, right_id = mentions[0]["itemId"], mentions[1]["itemId"]
    lines = []
    for metrics, row in pairs:
        if row["ItemID"] in (left_id, right_id):
            lines.append((metrics, row))
    if len(lines) < 2:
        return _need_item(profile, "compare")
    lines.sort(key=lambda pair: pair[0]["urgencyScore"], reverse=True)
    (first_m, first), (second_m, second) = lines[0], lines[1]
    answer = (
        f"Between the two, {first['ItemName']} needs attention first. Urgency is {first_m['urgencyScore']:.2f} against "
        f"{second_m['urgencyScore']:.2f} for {second['ItemName']}; {first['TotalCapacity'] - first['UnitsSold']} unsold "
        f"with {first_m['hoursRemaining']:.1f} hours left versus {second['TotalCapacity'] - second['UnitsSold']} unsold "
        f"with {second_m['hoursRemaining']:.1f} hours. Deal with {first['ItemName']} first: it is "
        f"{yield_engine.money(profile, _value_at_risk(first))} of exposure against {yield_engine.money(profile, _value_at_risk(second))} for {second['ItemName']}."
    )
    reasoning = [
        f"Compared {first['ItemName']} and {second['ItemName']} on urgency, not on raw units",
        f"Urgency: {first['ItemName']} {first_m['urgencyScore']:.2f} vs {second['ItemName']} {second_m['urgencyScore']:.2f}",
        f"Exposure: {yield_engine.money(profile, _value_at_risk(first))} vs {yield_engine.money(profile, _value_at_risk(second))}",
    ]
    return Reply("compare_items", answer, reasoning, first["ItemID"])


def _forecast_close(pairs, profile, rows):
    exposure = round(sum(_value_at_risk(row) for _m, row in pairs), 2)
    units_left = sum(row["TotalCapacity"] - row["UnitsSold"] for _m, row in pairs)
    projections = []
    for metrics, row in pairs:
        projection = yield_engine.projected_unsold(row)
        projections.append((row, metrics, projection))
    projected = round(sum(entry[2]["expectedUnsoldValue"] for entry in projections), 2)
    sold_forecast = round(sum(entry[2]["expectedAdditionalSales"] for entry in projections), 2)
    answer = (
        f"If nothing else changes, about {yield_engine.money(profile, projected)} of stock value is still unsold at close, "
        f"from {_plural(units_left, 'unit')} on the shelf now. At the rate they are selling so far, roughly "
        f"{sold_forecast:.0f} more units move before we shut, which is why the close is smaller than today's "
        f"{yield_engine.money(profile, exposure)} exposure but still worth looking at. "
    )
    worst = sorted(projections, key=lambda entry: entry[2]["expectedUnsoldValue"], reverse=True)[:3]
    answer += "The three biggest closing positions are " + ", ".join(
        f"{row['ItemName']} at {yield_engine.money(profile, projection['expectedUnsoldValue'])} "
        f"({projection['expectedUnsold']:.0f} of {projection['unitsLeft']} units left)"
        for row, _metrics, projection in worst
    ) + "."
    reasoning = [
        f"For each of {_plural(len(pairs), 'line')}, took the rate of sale implied by units sold over the "
        f"{yield_engine.ASSUMED_ELAPSED_HOURS:.0f} hours already elapsed",
        "Extended that rate across the hours each line has left, capped at the units actually on the shelf",
        f"Projected remainder: {yield_engine.money(profile, projected)} across all lines, "
        f"{_plural(len(worst), 'line')} carrying the largest positions",
        "This is a projection from current behaviour, not a guarantee; a bundle is what changes it",
    ]
    return Reply("forecast_close", answer, reasoning, worst[0][0]["ItemID"] if worst else None)


def _what_if(parse, pairs, profile, rows, perks, focus):
    percent_entity = _entity(parse, "percent")
    percent = percent_entity["value"] if percent_entity else 10.0
    row = focus or (pairs[0][1] if pairs else None)
    if row is None:
        return _need_item(profile, "scenario")
    discount = yield_engine.discount_impact(row, profile, percent)
    plan = yield_engine.bundle_plan(row, perks, profile)
    bundle_margin = round(plan["recommendation"]["marginRetained"], 2)
    bundle_threshold = round(plan["metrics"]["marginThreshold"], 2)
    discount_margin = round(discount["newPrice"] - discount["floorThreshold"], 2)
    answer = (
        f"Two ways on {row['ItemName']}. Bundle: you keep {yield_engine.money(profile, bundle_margin)} against the "
        f"{yield_engine.money(profile, bundle_threshold)} floor, the floor "
        f"{'stays safe' if plan['metrics']['floorRespected'] else 'would break'}, and the customer has a reason to "
        f"come back. Mark down {percent:.0f}%: price drops to {yield_engine.money(profile, discount['newPrice'])}, "
        f"margin falls to {yield_engine.money(profile, discount_margin)} and the floor is "
        f"{'broken' if discount['breachesFloor'] else 'still safe'}. "
        f"The bundle keeps {yield_engine.money(profile, round(bundle_margin - discount_margin, 2))} more per unit."
    )
    reasoning = [
        f"Ran both paths on the same line: {row['ItemName']}",
        f"Bundle keeps {yield_engine.money(profile, bundle_margin)}; the {percent:.0f}% markdown keeps {yield_engine.money(profile, discount_margin)}",
        f"Difference per unit: {yield_engine.money(profile, round(bundle_margin - discount_margin, 2))}",
        "Both scenarios use the live stock, the floor and the actual perk costs, so you can act on either one straight away",
    ]
    return Reply("what_if", answer, reasoning, row["ItemID"], evidence=[{"discount": discount}, {"bundle": plan}])


def _capacity_status(pairs, profile, rows):
    units = sum(row["TotalCapacity"] for _m, row in pairs)
    sold = sum(row["UnitsSold"] for _m, row in pairs)
    sellthrough = round(100 * sold / max(units, 1))
    average_velocity = round(sum(m["velocityIndex"] for m, _r in pairs) / max(len(pairs), 1), 3)
    exposed = [(m, r) for m, r in pairs if m["urgencyScore"] >= 0.12]
    best = max(pairs, key=lambda pair: pair[0]["velocityIndex"])
    answer = (
        f"{profile['businessName']} has sold {sellthrough}% of the day so far: {sold} of {units} units across "
        f"{_plural(len(pairs), profile['itemLabel'].lower())}. Average velocity is {average_velocity:.0%} and "
        f"{_plural(len(exposed), 'line')} are in the critical band. Your fastest mover is "
        f"{best[1]['ItemName']} ({best[0]['velocityIndex']:.0%}) and the pressure sits on "
        f"{pairs[0][1]['ItemName']}."
    )
    reasoning = [
        f"Added up units sold and capacity across all {_plural(len(pairs), 'line')}: {sold} units sold of {units} capacity",
        f"Sell-through {sellthrough}%, mean velocity {average_velocity:.0%}",
        f"Critical band is urgency 0.12 or higher: {_plural(len(exposed), 'line')} qualify",
    ]
    return Reply("capacity_status", answer, reasoning, exposed[0][1]["ItemID"] if exposed else None)


def _decision_history(history, profile):
    if not history:
        answer = (
            "Nothing is on record yet. Every recommendation you approve or decline is written to the log with "
            "the item, the action, the units filled and the margin retained, so this tab fills up as you act."
        )
        return Reply("decision_history", answer, ["StrategyHistory is empty for this workspace"])
    approved = [row for row in history if str(row["ManagerDecision"]).lower() == "approved"]
    declined = [row for row in history if str(row["ManagerDecision"]).lower() == "declined"]
    modified = [row for row in history if str(row["ManagerDecision"]).lower() == "modified"]
    retained = round(sum(row["MarginRetained"] for row in history), 2)
    latest = history[0]
    action = latest["RecommendedAction"].split(".")[0]
    answer = (
        f"{_plural(len(history), 'decision')} on record: {len(approved)} approved, {len(modified)} changed, "
        f"{len(declined)} declined, holding {yield_engine.money(profile, retained)} of retained margin in total. "
        f"The most recent was a {action} on {latest.get('ItemLabel') or 'item ' + str(latest['ItemID'])}, "
        f"{str(latest['ManagerDecision']).lower()} with {yield_engine.money(profile, latest['MarginRetained'])} kept."
    )
    reasoning = [
        f"Read {_plural(len(history), 'row')} from StrategyHistory, newest first",
        f"Approved {len(approved)}, changed {len(modified)}, declined {len(declined)}",
        f"Summed the margin column of those rows: {yield_engine.money(profile, retained)}",
        "History keeps the label it was recorded with, so switching sector never rewrites the past",
    ]
    return Reply("decision_history", answer, reasoning, latest["ItemID"])


def _sector_info(sectors, profile, rows):
    names = ", ".join(f"{preset['name']} ({preset['businessName']})" for preset in sectors.values())
    answer = (
        f"You are running {profile['name'].lower()} for {profile['businessName']}. The same engine also covers "
        f"{len(sectors) - 1} other kinds of business: {names}. Each one has its own stock, how fast it perishes, "
        f"perk costs, margin floor and words the owner uses, so switching sector reseeds the workspace rather than "
        f"changing the code."
    )
    reasoning = [
        f"Read the preset registry: {_plural(len(sectors), 'sector')}",
        f"Active preset is '{profile['sector']}' with a {profile['marginFloorPct']}% floor",
        "Vocabulary for this answer comes from the active preset's own noun and business name",
    ]
    return Reply("sector_info", answer, reasoning)


def _capabilities(profile, rows):
    noun = profile["inventoryNoun"]
    lowest = min(rows, key=lambda row: row["BasePrice"]) if rows else None
    highest = max(rows, key=lambda row: row["BasePrice"]) if rows else None
    answer = (
        "I read your live stock, the margin floor and your decision history, then answer in the words of this business. "
        "I can tell you what needs attention first, how much is heading for waste and what that is worth, "
        "what a bundle would look like on an item instead of a markdown, whether a price cut breaks the floor, "
        "which of two lines to deal with first, what the rest of the day looks like if nothing changes, "
        "and what you have approved or turned down. I can also switch or explain sectors. "
    )
    if lowest and highest:
        answer += (
            f"Right now that means {len(rows)} live {noun} from {lowest['ItemName']} at "
            f"{yield_engine.money(profile, lowest['BasePrice'])} up to {highest['ItemName']} at "
            f"{yield_engine.money(profile, highest['BasePrice'])}."
        )
    reasoning = [
        "Mapped the question to the capability list for the active preset",
        f"Grounding examples in this business: {_plural(len(rows), 'live line')}",
    ]
    return Reply("capabilities", answer, reasoning)


def _explain_term(parse, pairs, profile):
    """Explain one of the engine's own words, on a live line.

    A glossary definition on its own is still abstract, so wherever the term maps
    onto a number this answer also shows that number for the line under the most
    pressure. Teaching the word and showing it on real stock is the difference
    between a dictionary and an explanation.
    """
    from .knowledge import GLOSSARY

    text = f"{parse.repaired or ''} {parse.normalised or ''} {parse.text or ''}".lower()
    term = next((key for key in GLOSSARY if key in text), None)
    if term is None:
        # The sentence may name a term in pieces, as in "what is a price floor".
        term = next(
            (
                key
                for key in GLOSSARY
                if sum(1 for word in key.split() if len(word) > 4 and word in text) >= max(1, len(key.split()) - 1)
            ),
            None,
        )

    metrics, row = pairs[0] if pairs else (None, None)
    item = row["ItemName"] if row else "your stock"
    money = lambda value: yield_engine.money(profile, value)  # noqa: E731

    examples: dict[str, str] = {}
    if row is not None:
        floor_value = round(row["BasePrice"] * profile["marginFloorPct"] / 100, 2)
        unsold = row["TotalCapacity"] - row["UnitsSold"]
        examples = {
            "margin floor": f"On the {item} it is {money(floor_value)}: {profile['marginFloorPct']}% of the {money(row['BasePrice'])} shelf price.",
            "urgency score": f"The {item} is at {metrics['urgencyScore']:.2f} right now, which is high because {unsold} units are left with {metrics['hoursRemaining']:.1f} hours to go.",
            "velocity index": f"The {item} is at {metrics['velocityIndex']:.0%}: that share of its batch has already sold.",
            "exposure": f"The {item} alone is {money(unsold * row['BasePrice'])} of exposure.",
            "money at risk": f"The {item} alone is {money(unsold * row['BasePrice'])} at risk.",
            "risk status": f"The {item} is currently {metrics['riskStatus']}.",
            "perk": "A free loyalty stamp costs you nothing at all, which is why it is offered first.",
            "tier": f"On the {item} the first tier is paid for out of today's margin and the last one is earned next visit.",
        }

    if term is None:
        words = ", ".join(list(GLOSSARY)[:6])
        answer = (
            f"Tell me which word you mean and I will put it in plain English. The ones I can explain include {words}."
        )
        reasoning = [
            "The question was about what a word means, but no term in the glossary matched it",
            "Rather than guess at a definition, the answer asks which word was meant",
        ]
        reply = Reply("explain_term", answer, reasoning)
        reply.custom = True
        reply.headline = "Which word should I explain?"
        reply.meaning = "Every term I use is defined in plain language, so you never have to take one on trust."
        reply.severity = "info"
        reply.follow_ups = [f"what does {word} mean" for word in list(GLOSSARY)[:3]]
        return reply

    meaning = GLOSSARY[term]
    example = examples.get(term, "")
    answer = f"{term.title()} means: {meaning}" + (f" {example}" if example else "")
    reasoning = [
        f"Matched '{term}' from the glossary, which is the same list the assistant is given",
        "Answered from the definition rather than from a model, so the explanation cannot drift",
    ]
    if example:
        reasoning.append("Put the definition on a live line so the word is attached to a number you can see")

    reply = Reply("explain_term", answer, reasoning, row["ItemID"] if row else None)
    reply.custom = True
    reply.headline = f"{term.title()} - in plain words"
    reply.meaning = meaning
    reply.severity = "info"
    reply.next_steps = [
        {"label": "See it on today's priority", "detail": "The line this applies to right now", "ask": "which batch needs attention first"},
        {"label": "See what needs no discounting", "detail": "A bundle that keeps your price", "ask": f"what should i bundle with the {item}"},
    ]
    reply.follow_ups = [
        f"what does {other} mean"
        for other in [key for key in GLOSSARY if key != term][:2]
    ] + ["which batch needs attention first"]
    return reply


def _greeting(profile):
    answer = (
        f"Morning. {profile['businessName']} is loaded as a {profile['name'].lower()} with a "
        f"{profile['marginFloorPct']}% floor. {profile['sampleQuestion']}"
    )
    return Reply("greeting", answer, ["A greeting was detected, so the answer is the business's own opening question"])


def _out_of_scope(parse, profile):
    answer = (
        f"That one is outside what I can help with. I only work on the live stock at {profile['businessName']}: "
        f"what needs attention, what is heading for waste, what to bundle, what a markdown would cost, and "
        f"what the day looks like at close."
    )
    reasoning = [
        "No business vocabulary matched the sentence",
        "The engine treats this as off topic rather than guessing at a business intent",
    ]
    return Reply("out_of_scope", answer, reasoning)


# ---------------------------------------------------------------------------
def _need_item(profile, topic: str, detail: str = "") -> Reply:
    detail = (detail + " ") if detail else ""
    answer = (
        f"{detail}Tell me which {profile['itemLabel'].lower()} you mean and I will work it out. "
        f"For a {topic} question I need one of your live lines named, for example "
        f"\"{profile['sampleQuestion'].lower()}\"."
    )
    reasoning = ["The question needs a specific item and none was recognised in the sentence", "This is a spot where the assistant is asking for the missing detail before it can answer"]
    return Reply(topic, answer, reasoning, None)


# ---------------------------------------------------------------------------
# Plain-language layer
#
# The handlers above answer like an analyst. This section rewrites the same
# answer for the person who actually runs the shop: a one-line headline, what
# the figures mean in practice, the figures themselves with a label on each one,
# and the specific things they can do next - each of which is a question the
# copilot already knows how to answer.
# ---------------------------------------------------------------------------
def _line(pairs, item_id) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """The live row behind an id, paired with its metrics."""
    row = _by_id([candidate for _metrics, candidate in pairs], item_id)
    if row is None:
        return None, None
    return _pair_for(row, pairs)


def _exposure(row: dict[str, Any]) -> float:
    return round((row["TotalCapacity"] - row["UnitsSold"]) * row["BasePrice"], 2)


def _key_numbers(intent, pairs, profile, row, metrics) -> list[dict[str, str]]:
    """The figures behind the answer, each with a label and a reason to care."""
    money = lambda value: yield_engine.money(profile, value)  # noqa: E731

    if intent == "waste_risk":
        exposed = [(m, r) for m, r in pairs if m["hoursRemaining"] <= 24]
        units = sum(r["TotalCapacity"] - r["UnitsSold"] for _m, r in exposed)
        total = round(sum(_exposure(r) for _m, r in exposed), 2)
        return [
            {"label": "Money at risk", "value": money(total), "note": "stock that may not sell in time"},
            {"label": "Units exposed", "value": f"{units}", "note": "still on the shelf on those lines"},
            {"label": "Lines affected", "value": f"{len(exposed)} of {len(pairs)}", "note": "need a decision today"},
        ]

    if intent == "forecast_close":
        projections = [(r, yield_engine.projected_unsold(r)) for _m, r in pairs]
        projected = round(sum(p["expectedUnsoldValue"] for _r, p in projections), 2)
        extra = round(sum(p["expectedAdditionalSales"] for _r, p in projections), 2)
        units_left = sum(r["TotalCapacity"] - r["UnitsSold"] for _m, r in pairs)
        return [
            {"label": "Unsold at close", "value": money(projected), "note": "if nothing changes"},
            {"label": "More units expected to sell", "value": f"{extra:.0f}", "note": "at today's rate of sale"},
            {"label": "Units on the shelf now", "value": f"{units_left}", "note": f"across {len(pairs)} lines"},
        ]

    if intent == "capacity_status":
        units = sum(r["TotalCapacity"] for _m, r in pairs)
        sold = sum(r["UnitsSold"] for _m, r in pairs)
        critical = sum(1 for m, _r in pairs if m["urgencyScore"] >= 0.12)
        return [
            {"label": "Sold so far today", "value": f"{round(100 * sold / max(units, 1))}%", "note": f"{sold} of {units} units"},
            {"label": "Mean rate of sale", "value": f"{sum(m['velocityIndex'] for m, _r in pairs) / max(len(pairs), 1):.0%}", "note": "average across every line"},
            {"label": "Needs attention", "value": f"{critical}", "note": "lines in the critical band"},
        ]

    if intent == "decision_history":
        return []

    if row is None:
        return []

    unsold = row["TotalCapacity"] - row["UnitsSold"]
    floor_value = round(row["BasePrice"] * profile["marginFloorPct"] / 100, 2)
    room = round(row["BasePrice"] - floor_value, 2)
    return [
        {"label": "Shelf price", "value": money(row["BasePrice"]), "note": "what the customer pays today"},
        {"label": "Still unsold", "value": f"{unsold} of {row['TotalCapacity']}", "note": "left on the shelf"},
        {"label": "Time left", "value": f"{metrics['hoursRemaining']:.1f} h", "note": "before it has to go"},
        {"label": "Money at risk", "value": money(_exposure(row)), "note": "if none of it sells"},
        {"label": "Your floor on this line", "value": money(floor_value), "note": f"{profile['marginFloorPct']}% of shelf price"},
        {"label": "Room to add value", "value": money(room), "note": "what a perk may cost you"},
    ]


def _plan_block(intent, profile, row, metrics, pairs, perks, parse=None) -> dict[str, Any]:
    """Headline, plain meaning, severity and the next steps for one intent."""
    money = lambda value: yield_engine.money(profile, value)  # noqa: E731
    item = row["ItemName"] if row else "this line"
    floor_pct = profile["marginFloorPct"]

    if intent == "priority" and row is not None:
        unsold = row["TotalCapacity"] - row["UnitsSold"]
        return {
            "headline": f"Start with the {item}",
            "meaning": (
                f"It has {metrics['hoursRemaining']:.1f} hours of shelf life left with {unsold} of "
                f"{row['TotalCapacity']} still unsold, which makes it the line where acting now saves the most money. "
                f"Everything else can wait until this one is moving."
            ),
            "severity": "act-now",
            "steps": [
                {"label": f"Bundle the {item} instead of cutting it", "detail": "Keeps the shelf price and adds a perk", "ask": f"what should i bundle with the {item}"},
                {"label": "See what waiting costs", "detail": "What is still unsold at close if nothing changes", "ask": "what will close look like"},
                {"label": "Compare it with the next line", "detail": "Know which one to do first when time is short", "ask": "what will go to waste"},
            ],
            "follow_ups": [f"what should i bundle with the {item}", "what will close look like", "am i allowed to drop the price"],
        }

    if intent == "waste_risk":
        exposed = [(m, r) for m, r in pairs if m["hoursRemaining"] <= 24]
        total = round(sum(_exposure(r) for _m, r in exposed), 2)
        return {
            "headline": f"{money(total)} of stock could go to waste",
            "meaning": (
                f"These {len(exposed)} lines will not sell out on their own before they have to go. "
                "That money is what you lose today if nothing changes - and a small free perk usually costs "
                "less than the write-off it prevents."
            ),
            "severity": "act-now" if total > 0 else "info",
            "steps": [
                {"label": "Bundle the biggest one", "detail": "Highest exposure first", "ask": f"what should i bundle with the {exposed[0][1]['ItemName']}" if exposed else "what should i bundle"},
                {"label": "See the close projection", "detail": "What is left if nothing changes", "ask": "what will close look like"},
                {"label": "Check what one is costing you least", "detail": "Cheapest perk that still adds value", "ask": "what can i give away cheaply"},
            ],
            "follow_ups": ["what should i bundle", "what will close look like", "am i allowed to drop the price"],
        }

    if intent == "margin_floor":
        if row is not None:
            floor_value = round(row["BasePrice"] * floor_pct / 100, 2)
            return {
                "headline": f"{money(floor_value)} is your floor on the {item}",
                "meaning": (
                    f"Your rule is to keep at least {floor_pct}% of the {money(row['BasePrice'])} shelf price. "
                    f"That leaves {money(row['BasePrice'] - floor_value)} to spend on something extra for the "
                    "customer, and nothing at all for a price cut."
                ),
                "severity": "info",
                "steps": [
                    {"label": f"See the bundle for the {item}", "detail": "Uses the room above the floor rather than cutting into it", "ask": f"what should i bundle with the {item}"},
                    {"label": "Test a price cut", "detail": "Check whether it would break the floor", "ask": "what if i drop the price 10%"},
                ],
                "follow_ups": [f"what should i bundle with the {item}", "what if i drop the price 10%"],
            }
        return {
            "headline": f"Your floor is {floor_pct}% on every line",
            "meaning": (
                f"{profile['businessName']} keeps at least {floor_pct}% of every shelf price. A bundle is only "
                "offered while it fits above that, and a price cut that would go below it is refused outright."
            ),
            "severity": "info",
            "steps": [{"label": "See it in money", "detail": "Ask about one line to get the exact floor for it", "ask": "what is my margin floor"}],
            "follow_ups": ["what should i bundle", "am i allowed to drop the price"],
        }

    if intent == "bundle_recommend" and row is not None:
        plan = yield_engine.bundle_plan(row, perks, profile)
        kept = round(plan["recommendation"]["marginRetained"], 2)
        threshold = round(plan["metrics"]["marginThreshold"], 2)
        return {
            "headline": f"Bundle the {item} - do not cut the price",
            "meaning": (
                f"The {item} sells at {money(row['BasePrice'])} and your floor is {money(threshold)}. "
                f"Even after paying for the perk you are still at {money(kept)}, so there is no reason to touch "
                "the price - and no customer learns to wait for a discount."
            ),
            "severity": "opportunity",
            "steps": [
                {"label": "Compare it with a price cut", "detail": "See what the markdown would have cost you", "ask": "what if i drop the price 15%"},
                {"label": "See what is still unsold at close", "detail": "Whether this line needs more than one action", "ask": "what will close look like"},
                {"label": "Approve it on the Inventory tab", "detail": "Logs the decision with the margin it kept", "ask": ""},
            ],
            "follow_ups": [f"what if i drop the price on the {item}", "what will close look like", "what have i approved so far"],
        }

    if intent == "discount_request":
        percent_entity = _entity(parse, "percent") if parse is not None else None
        percent = float(percent_entity["value"]) if percent_entity else 10.0
        if row is not None:
            impact = yield_engine.discount_impact(row, profile, percent)
            blocked = impact["breachesFloor"]
            return {
                "headline": (f"No - that breaks your floor on the {item}" if blocked else f"That is allowed, but the {item} keeps more as a bundle"),
                "meaning": (
                    "A price cut takes money straight out of your margin on every unit, and it teaches customers "
                    "to wait for the next one. A perk costs you less than the cut and keeps the shelf price intact."
                ),
                "severity": "blocked" if blocked else "caution",
                "steps": [
                    {"label": f"Bundle the {item} instead", "detail": "Same customer, floor untouched", "ask": f"what should i bundle with the {item}"},
                    {"label": "See both side by side", "detail": "Bundle against markdown, per unit", "ask": f"what if i drop the price on the {item}"},
                ],
                "follow_ups": [f"what should i bundle with the {item}", f"what if i drop the price on the {item}"],
            }

    if intent == "compare_items":
        return {
            "headline": f"Deal with the {item} first",
            "meaning": (
                "Urgency compares how little time is left against how much stock is left, so the line with less "
                "time and more stock comes first - not the one with the highest price."
            ),
            "severity": "act-now",
            "steps": [
                {"label": f"Bundle the {item}", "detail": "Start with the more urgent of the two", "ask": f"what should i bundle with the {item}"},
                {"label": "See all waste risk", "detail": "Every line that lapses in the next day", "ask": "what will go to waste"},
            ],
            "follow_ups": [f"what should i bundle with the {item}", "what will go to waste"],
        }

    if intent == "forecast_close":
        projections = [(r, yield_engine.projected_unsold(r)) for _m, r in pairs]
        projected = round(sum(p["expectedUnsoldValue"] for _r, p in projections), 2)
        worst = max(projections, key=lambda entry: entry[1]["expectedUnsoldValue"], default=None)
        return {
            "headline": f"{money(projected)} still on the shelf at close",
            "meaning": (
                "This is what today looks like if you change nothing, based on how fast things are actually "
                "selling right now. It is a projection, not a verdict - one bundle changes it."
            ),
            "severity": "watch",
            "steps": [
                {"label": f"Act on the {worst[0]['ItemName']}" if worst else "Act on the biggest position", "detail": "The largest closing position", "ask": f"what should i bundle with the {worst[0]['ItemName']}" if worst else "what should i bundle"},
                {"label": "See where attention pays most", "detail": "Ranked by urgency, not by price", "ask": "which batch needs attention first"},
            ],
            "follow_ups": ["which batch needs attention first", "what should i bundle", "how is today going"],
        }

    if intent == "what_if" and row is not None:
        return {
            "headline": f"Two ways on the {item}, one keeps more",
            "meaning": (
                "Both options use your live stock, your floor and the real perk costs, so you can act on "
                "whichever fits the counter right now."
            ),
            "severity": "opportunity",
            "steps": [
                {"label": f"Go with the bundle on the {item}", "detail": "Highest margin per unit", "ask": f"what should i bundle with the {item}"},
                {"label": "Check the floor again", "detail": "See the exact lowest price allowed", "ask": f"what is my floor on the {item}"},
            ],
            "follow_ups": [f"what is my floor on the {item}", "what will close look like"],
        }

    if intent == "capacity_status":
        units = sum(r["TotalCapacity"] for _m, r in pairs)
        sold = sum(r["UnitsSold"] for _m, r in pairs)
        return {
            "headline": f"{round(100 * sold / max(units, 1))}% sold so far today",
            "meaning": (
                "This is the whole day in one number: how much of what you made has actually gone. "
                "The lines still behind are where the remaining money is."
            ),
            "severity": "info",
            "steps": [
                {"label": "See what is lagging", "detail": "Ranked by urgency", "ask": "which batch needs attention first"},
                {"label": "See the close projection", "detail": "Where today ends up", "ask": "what will close look like"},
            ],
            "follow_ups": ["which batch needs attention first", "what will close look like"],
        }

    if intent == "decision_history":
        return {
            "headline": "Your decisions so far",
            "meaning": (
                "Every approval and refusal is written down with the item and the margin it kept, so the "
                "choices you already made are what the engine learns from."
            ),
            "severity": "info",
            "steps": [{"label": "Review live stock", "detail": "Approve or decline the next action", "ask": "which batch needs attention first"}],
            "follow_ups": ["which batch needs attention first", "what will go to waste"],
        }

    if intent == "sector_info":
        return {
            "headline": f"You are running {profile['name'].lower()}",
            "meaning": (
                f"{profile['businessName']} sells {profile['inventoryNoun']} with a {floor_pct}% margin floor. "
                "The same engine runs other kinds of business too - switching reloads the stock, the perks and "
                "the words, without changing anything else."
            ),
            "severity": "info",
            "steps": [{"label": "See what I can help with", "detail": "The full list of questions", "ask": "what can you do"}],
            "follow_ups": ["what can you do", "which batch needs attention first"],
        }

    if intent == "capabilities":
        return {
            "headline": "Here is what I can help with",
            "meaning": (
                "Ask in your own words and I will use the live numbers. I explained what I understood and where "
                "every figure came from, so you never have to take a number on faith."
            ),
            "severity": "info",
            "steps": [
                {"label": "Start where it matters", "detail": "The line under the most pressure", "ask": "which batch needs attention first"},
                {"label": "See the waste risk", "detail": "What lapses if nothing changes", "ask": "what will go to waste"},
            ],
            "follow_ups": ["which batch needs attention first", "what will go to waste", "what is my margin floor"],
        }

    if intent == "greeting":
        return {
            "headline": f"Morning - {profile['businessName']} is ready",
            "meaning": f"{profile['marginFloorPct']}% floor, {len(pairs)} live lines, and the live numbers already loaded.",
            "severity": "info",
            "steps": [{"label": "See today's priority", "detail": "The line to deal with first", "ask": profile["sampleQuestion"]}],
            "follow_ups": [profile["sampleQuestion"], "what will go to waste"],
        }

    if intent == "out_of_scope":
        return {
            "headline": "That one is outside what I can help with",
            "meaning": (
                f"I only read the live stock at {profile['businessName']} and the rules and history that go with "
                "it. I will not guess at anything else, because a confident wrong answer about your money is "
                "worse than no answer."
            ),
            "severity": "blocked",
            "steps": [
                {"label": "Ask what needs attention", "detail": "The most useful question to start with", "ask": "which batch needs attention first"},
                {"label": "Ask what will go to waste", "detail": "Stock value that lapses if nothing changes", "ask": "what will go to waste"},
            ],
            "follow_ups": ["which batch needs attention first", "what will go to waste", "what can you do"],
        }

    return {
        "headline": INTENT_LABELS.get(intent, intent),
        "meaning": "",
        "severity": "info",
        "steps": [],
        "follow_ups": ["which batch needs attention first", "what should i bundle"],
    }


def _enrich(reply: Reply, parse: ParseResult, profile: dict[str, Any], pairs, perks) -> Reply:
    """Add the owner-facing layer: headline, meaning, figures, steps, follow-ups."""
    metrics, row = _line(pairs, reply.focus_item_id)
    reply.key_numbers = reply.key_numbers or _key_numbers(reply.intent, pairs, profile, row, metrics)

    # A handler that already wrote its own owner-facing layer keeps it; the rest
    # get theirs derived from the intent.

    # Any internal term that leaked into the prose gets a plain definition, so a
    # reader who does not know what "urgency" means is never stuck.
    from .knowledge import GLOSSARY

    lowered = (reply.answer or "").lower()
    reply.glossary = [
        {"term": term, "meaning": meaning}
        for term, meaning in GLOSSARY.items()
        if term in lowered
    ]

    if reply.clarify_options:
        # Asking back is its own kind of answer, and it should read like one. The
        # intent-derived headline ("Bundle") would describe a decision that has
        # not been made yet, and the intent-derived steps would assume a line was
        # named when the whole point is that it was not.
        reply.headline = "I need one more thing before I answer"
        reply.meaning = (
            "Two readings of that sentence came out almost level, so rather than pick one and be confidently "
            "wrong about your money, it is asking. Pick the one you meant and it answers with the live numbers; "
            "the choice is also remembered, so it will not ask again for that phrasing."
        )
        reply.severity = "info"
        reply.next_steps = []
        reply.follow_ups = [question for question in dict.fromkeys([
            "which batch needs attention first",
            "what will go to waste",
            "what can you do",
        ]) if question]
    elif not reply.custom:
        block = _plan_block(reply.intent, profile, row, metrics, pairs, perks, parse)
        reply.headline = block["headline"]
        reply.meaning = block["meaning"]
        reply.severity = block["severity"]
        reply.next_steps = block["steps"]
        reply.follow_ups = [question for question in dict.fromkeys(block["follow_ups"]) if question]

    first_step = reply.next_steps[0]["label"] if reply.next_steps else ""
    reply.plain = " ".join(part for part in [reply.headline, reply.meaning, first_step] if part).strip()
    return reply


def _clarify(parse, alternatives) -> Reply:
    """Offer the model's own best reading first, then the runners-up.

    Each option carries its intent id, so the UI can send the chosen intent
    back without relying on list order.
    """
    best = {"intent": parse.intent, "label": INTENT_LABELS.get(parse.intent, parse.intent)}
    options = [best] + [
        {"intent": alternative["intent"], "label": alternative["label"]}
        for alternative in alternatives[:2]
        if alternative["intent"] != parse.intent
    ]
    others = " or ".join(option["label"].lower() for option in options[1:]) or "something else"
    answer = (
        f"I could read that more than one way, so I would rather check than guess. I think this is about "
        f"{best['label'].lower()}; if that is wrong, it might be about {others}. Pick one and I will answer "
        f"with the live numbers."
    )
    reasoning = [
        f"Top intent was '{parse.intent}' at {parse.confidence:.0%} confidence",
        f"The margin over the next best ({parse.alternatives[0]['label']} at {parse.alternatives[0]['confidence']:.0%}) "
        f"was too small to act on" if parse.alternatives else "No single intent stood out",
        "The engine asked instead of answering, because a wrong confident answer is worse than a question",
    ]
    return Reply(parse.intent, answer, reasoning, parse.focus_item_id, clarify_options=options)


# ---------------------------------------------------------------------------
def plan(
    parse: ParseResult,
    *,
    profile: dict[str, Any],
    rows: list[dict[str, Any]],
    history: list[dict[str, Any]],
    perks: list[dict[str, Any]],
    sectors: dict[str, dict[str, Any]],
) -> Reply:
    """Answer a parsed utterance using live data. Pure function, easy to test."""
    pairs = _sorted_rows(rows)
    focus = _by_id(rows, parse.focus_item_id)

    base_reasoning = [
        f"Intent '{parse.intent}' confirmed by the manager, not classified"
        if parse.confirmed
        else f"Intent '{parse.intent}' at {parse.confidence:.0%} confidence"
    ]
    if parse.corrections:
        base_reasoning.append(
            "Repaired typos before classifying: " + ", ".join(f"{c['from']} -> {c['to']}" for c in parse.corrections)
        )
    if parse.resolved_from_context:
        base_reasoning.append("No item named, so the previous item in this conversation was used")
    if focus:
        base_reasoning.append(f"Resolved the item to {focus['ItemName']} ({focus['Category']})")

    if not pairs:
        return _enrich(Reply(parse.intent, "There is no inventory signal to analyse yet.", base_reasoning), parse, profile, pairs, perks)

    if parse.needs_clarification and parse.intent != "out_of_scope":
        reply = _clarify(parse, parse.alternatives)
        return _enrich(
            Reply(
                reply.intent,
                reply.answer,
                base_reasoning + reply.reasoning,
                reply.focus_item_id,
                clarify_options=reply.clarify_options,
            ),
            parse,
            profile,
            pairs,
            perks,
        )

    handler = {
        "priority": lambda: _priority(pairs, profile, rows),
        "waste_risk": lambda: _waste_risk(pairs, profile, rows),
        "margin_floor": lambda: _margin_floor(parse, pairs, profile, rows, focus),
        "bundle_recommend": lambda: _bundle_recommend(pairs, profile, rows, perks, focus),
        "discount_request": lambda: _discount_request(parse, pairs, profile, rows, focus),
        "compare_items": lambda: _compare_items(parse, pairs, profile, rows),
        "forecast_close": lambda: _forecast_close(pairs, profile, rows),
        "what_if": lambda: _what_if(parse, pairs, profile, rows, perks, focus),
        "capacity_status": lambda: _capacity_status(pairs, profile, rows),
        "decision_history": lambda: _decision_history(history, profile),
        "sector_info": lambda: _sector_info(sectors, profile, rows),
        "capabilities": lambda: _capabilities(profile, rows),
        "greeting": lambda: _greeting(profile),
        "explain_term": lambda: _explain_term(parse, pairs, profile),
        "out_of_scope": lambda: _out_of_scope(parse, profile),
    }.get(parse.intent, lambda: _capabilities(profile, rows))

    reply = handler()
    extra = [
        f"Answered as {INTENT_LABELS.get(parse.intent, parse.intent)} because the engine was confident enough to act",
    ]
    reply.reasoning = base_reasoning + extra + reply.reasoning
    if reply.focus_item_id is None:
        reply.focus_item_id = focus["ItemID"] if focus else None
    return _enrich(reply, parse, profile, pairs, perks)
