"""Pure yield math, shared by the HTTP API and the copilot planner.

Nothing here knows about FastAPI or HTTP: callers pass database rows in and
get plain dictionaries back. The reasoning layer imports this module so a
recommendation computed for the API and one computed inside the copilot can
never disagree.

A bundle is only valid while the price floor survives it. Perks paid for today
are funded in order and dropped if they would breach the floor; the next-visit
credit is a deferred commitment, reported separately instead of diluting
today's margin.
"""

from datetime import datetime, timezone
from typing import Any, Iterable

PERK_TIERS = (
    ("soft", 1, "Free perk"),
    ("partner", 2, "Partner voucher"),
    ("flex", 3, "Next-visit credit"),
)

# Seeded lines are given an expiry window and a number already sold, which
# implies the trading time already behind them. One constant keeps urgency and
# the closing forecast using the same assumption instead of two guesses.
ASSUMED_ELAPSED_HOURS = 12.0


def money(profile: dict[str, Any], value: float) -> str:
    return f"{profile['currencySymbol']}{value:,.0f}"


def hours_remaining(expiration: str) -> float:
    return max(0.0, (datetime.fromisoformat(expiration) - datetime.now(timezone.utc)).total_seconds() / 3600)


def projected_unsold(row) -> dict[str, Any]:
    """Extend the rate of sale so far across the hours the line has left.

    This is the honest version of a closing forecast: rather than reusing the
    urgency index (which blends sold share with elapsed time), it takes units
    sold per elapsed hour and asks how many more would go before the line
    lapses, capped at the units actually on the shelf.
    """
    remaining = hours_remaining(row["ExpirationTimestamp"])
    unsold = row["TotalCapacity"] - row["UnitsSold"]
    rate = row["UnitsSold"] / ASSUMED_ELAPSED_HOURS
    expected_sales = min(float(unsold), rate * remaining)
    expected_unsold = round(unsold - expected_sales, 2)
    return {
        "unitsLeft": unsold,
        "hoursRemaining": round(remaining, 1),
        "hourlyRate": round(rate, 2),
        "expectedAdditionalSales": round(expected_sales, 2),
        "expectedUnsold": expected_unsold,
        "expectedUnsoldValue": round(expected_unsold * row["BasePrice"], 2),
    }


def item_metrics(row) -> dict[str, Any]:
    remaining = hours_remaining(row["ExpirationTimestamp"])
    total = max(remaining + ASSUMED_ELAPSED_HOURS, 1)
    sold_ratio = row["UnitsSold"] / max(row["TotalCapacity"], 1)
    velocity = round(sold_ratio * (1 - remaining / total), 3)
    urgency = round(max(0, 1 - velocity) * (1 / (remaining + 1)), 4)
    risk = "critical" if urgency >= 0.12 else "watch" if velocity < 1 else "healthy"
    return {"velocityIndex": velocity, "urgencyScore": urgency, "hoursRemaining": round(remaining, 1), "riskStatus": risk}


def inventory_item(row) -> dict[str, Any]:
    return {
        "itemId": row["ItemID"],
        "itemName": row["ItemName"] or row["Category"],
        "category": row["Category"],
        "sector": row["Sector"],
        "basePrice": row["BasePrice"],
        "expirationTimestamp": row["ExpirationTimestamp"],
        "totalCapacity": row["TotalCapacity"],
        "unitsSold": row["UnitsSold"],
        "unitsAvailable": row["TotalCapacity"] - row["UnitsSold"],
        **item_metrics(row),
    }


def ranked_inventory(db) -> list[tuple[dict[str, Any], Any]]:
    """Every line, most urgent first."""
    rows = db.execute("SELECT * FROM Inventory ORDER BY ExpirationTimestamp").fetchall()
    return sorted(((item_metrics(row), row) for row in rows), key=lambda pair: pair[0]["urgencyScore"], reverse=True)


def load_perks(db) -> list[dict[str, Any]]:
    rows = db.execute("SELECT * FROM Perks WHERE AvailableStock > 0 ORDER BY Type, CostToBusiness").fetchall()
    return [dict(row) for row in rows]


def pick_perks(perks: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any] | None]:
    perks = list(perks)
    return {
        "soft": next((perk for perk in perks if perk["Type"] == "SoftInventory"), None),
        "partner": next((perk for perk in perks if perk["Type"] == "PartnerVoucher"), None),
        "flex": next((perk for perk in perks if perk["Type"] == "FlexCredit"), None),
    }


def bundle_plan(row, perks: Iterable[dict[str, Any]], profile: dict[str, Any]) -> dict[str, Any]:
    """The floor-safe bundle for one inventory row, with the floor maths exposed."""
    choices = pick_perks(perks)
    price = float(row["BasePrice"])
    metrics = item_metrics(row)
    threshold = round(price * profile["marginFloorPct"] / 100, 2)

    funded = {"soft": False, "partner": False, "flex": bool(choices["flex"])}
    committed = 0.0
    if choices["soft"] and round(price - choices["soft"]["CostToBusiness"], 2) >= threshold:
        funded["soft"] = True
        committed += choices["soft"]["CostToBusiness"]
    if choices["partner"] and round(price - committed - choices["partner"]["CostToBusiness"], 2) >= threshold:
        funded["partner"] = True
        committed += choices["partner"]["CostToBusiness"]

    margin = round(price - committed, 2)
    deferred = round(choices["flex"]["CostToBusiness"], 2) if choices["flex"] else 0.0
    withheld = [label for key, _, label in PERK_TIERS if not funded[key]]

    tiers = []
    for key, number, label in PERK_TIERS:
        perk = choices[key]
        if key == "flex":
            status = "deferred" if perk else "withheld"
        else:
            status = "funded" if funded[key] else "withheld"
        tiers.append({"tier": number, "label": label, "perk": perk if status != "withheld" else None, "status": status})

    sentence = [f"Keep {row['ItemName']} at {money(profile, price)}."]
    if funded["soft"] and choices["soft"]:
        sentence.append(f"Bundle {choices['soft']['PerkName']}.")
    if funded["partner"] and choices["partner"]:
        sentence.append(f"Add {choices['partner']['PerkName']}.")
    if withheld:
        blocked = " and ".join(label.lower() for label in withheld)
        sentence.append(f"{blocked.capitalize()} would push margin under the {profile['marginFloorPct']}% floor, so it is held back.")
    if choices["flex"]:
        sentence.append(
            f"Close with {choices['flex']['PerkName']}, earned on the next visit, costing {money(profile, deferred)} then rather than now."
        )
    sentence.append(f"Stock lapses in {metrics['hoursRemaining']:.1f} hours, so this runs before close.")

    return {
        "item": inventory_item(row),
        "metrics": {
            **metrics,
            "basePrice": price,
            "marginPreserved": margin,
            "marginThreshold": threshold,
            "deferredCommitment": deferred,
            # Exposed rather than left implicit: the comparison below needs to
            # say what the perks actually cost today, and deriving it as
            # ``price - margin`` in the caller is exactly the kind of arithmetic
            # that drifts when the floor rule changes.
            "perkCost": round(committed, 2),
            "roomToAddValue": round(price - threshold, 2),
            "floorRespected": margin >= threshold,
            "underperforming": metrics["velocityIndex"] < 1,
        },
        "recommendation": {
            "headline": f"Hold the {money(profile, price)} price. Add value instead.",
            "action": " ".join(sentence),
            "tiers": tiers,
            "marginRetained": margin,
            "withheldTiers": [label for key, _, label in PERK_TIERS if not funded[key]],
        },
    }


def profit_scenarios(
    row,
    perks: Iterable[dict[str, Any]],
    profile: dict[str, Any],
    markdown_percent: float = 20.0,
) -> dict[str, Any]:
    """Three ways the same stock can end, priced against each other.

    This exists because "protect your margin" is not a number, and an owner
    deciding at 4pm needs one. So the same line is costed three ways - let it
    lapse, mark it down, or add value - each with its cash per unit and its total.

    Two things are deliberately *not* invented here:

    * **The sell-through lift from a bundle is not modelled.** Nobody knows it,
      so all three scenarios move the same projected number of units and the
      comparison is honest about being per-unit. A made-up uplift would make the
      recommended option win by construction, which is the kind of number that
      gets a tool like this distrusted the first time it is checked.
    * **A markdown's long-run cost is stated, not scored.** Customers learning to
      wait for the cut is the real reason to avoid it, and it has no defensible
      figure, so it is reported as a note rather than folded into a total.

    What *is* computed is the number that settles the argument: how many extra
    units a bundle has to move to match a markdown's cash, given that the bundle
    pays for a perk and the markdown does not.
    """
    price = float(row["BasePrice"])
    threshold = round(price * profile["marginFloorPct"] / 100, 2)
    plan = bundle_plan(row, perks, profile)
    projection = projected_unsold(row)
    units_left = int(row["TotalCapacity"] - row["UnitsSold"])
    expected_extra = round(float(projection["expectedAdditionalSales"]), 2)
    expected_unsold = round(float(projection["expectedUnsold"]), 2)

    bundle_margin = round(float(plan["metrics"]["marginPreserved"]), 2)
    perk_cost = round(float(plan["metrics"]["perkCost"]), 2)
    deferred = round(float(plan["metrics"]["deferredCommitment"]), 2)
    markdown = discount_impact(row, profile, markdown_percent)
    markdown_margin = round(float(markdown["newPrice"]), 2)

    scenarios = [
        {
            "key": "lapse",
            "label": "Do nothing",
            "cashPerUnit": round(price, 2),
            "floorRespected": True,
            "perkCost": 0.0,
            "unitsMoved": expected_extra,
            "cash": round(expected_extra * price, 2),
            "writeOff": round(expected_unsold * price, 2),
            "verdict": "loses the stock",
            "detail": (
                f"At the current rate {expected_unsold:g} of {units_left} would never sell, costing "
                f"{money(profile, expected_unsold * price)} at shelf price."
            ),
        },
        {
            "key": "markdown",
            "label": f"Mark it down {markdown_percent:g}%",
            "cashPerUnit": markdown_margin,
            "floorRespected": not markdown["breachesFloor"],
            "perkCost": 0.0,
            "unitsMoved": expected_extra,
            "cash": round(expected_extra * markdown_margin, 2),
            "writeOff": None,
            "verdict": "keeps the price down for good",
            "detail": (
                f"{money(profile, markdown_margin)} a unit, which is {money(profile, markdown['cut'])} less than "
                f"today's {money(profile, price)} - and the next customer expects that price."
            ),
        },
        {
            "key": "bundle",
            "label": "Add value instead",
            "cashPerUnit": bundle_margin,
            "floorRespected": bool(plan["metrics"]["floorRespected"]),
            "perkCost": perk_cost,
            "unitsMoved": expected_extra,
            "cash": round(expected_extra * bundle_margin, 2),
            "writeOff": 0.0,
            "verdict": "keeps the price on the board",
            "detail": (
                f"{money(profile, bundle_margin)} a unit after {money(profile, perk_cost)} of perks, "
                f"against a floor of {money(profile, threshold)}."
            ),
        },
    ]

    breakeven = None
    if bundle_margin > 0 and markdown_margin > 0:
        ratio = round(markdown_margin / bundle_margin, 2)
        extra_percent = round((ratio - 1) * 100)
        # The sign matters and has to be stated, not rendered. A bundle usually
        # keeps less per unit than a cut, so it needs volume to catch up; but when
        # the perks are cheap enough it already wins per unit, and printing
        # "+ -2%" for that case reads like a bug rather than good news.
        if extra_percent > 0:
            direction = "needs-volume"
            explanation = (
                f"A {markdown_percent:g}% cut keeps {money(profile, markdown_margin)} a unit and the bundle keeps "
                f"{money(profile, bundle_margin)}, so the bundle has to move about {extra_percent}% more units "
                f"to make the same money - and unlike the cut, it does not reset what customers expect to pay."
            )
        else:
            direction = "already-ahead"
            explanation = (
                f"The bundle keeps {money(profile, bundle_margin)} a unit against {money(profile, markdown_margin)} "
                f"for a {markdown_percent:g}% cut, so it earns more on every unit sold as well as holding the price."
            )
        breakeven = {
            "ratio": ratio,
            "extraPercent": extra_percent,
            "direction": direction,
            "ifBundled": bundle_margin,
            "ifMarkedDown": markdown_margin,
            "explanation": explanation,
        }

    return {
        "item": inventory_item(row),
        "unitsLeft": units_left,
        "markdownPercent": markdown_percent,
        "floor": {"percent": profile["marginFloorPct"], "perUnit": threshold, "roomToAddValue": round(price - threshold, 2)},
        "scenarios": scenarios,
        "recommended": "bundle" if plan["metrics"]["floorRespected"] else "review",
        "breakeven": breakeven,
        "deferred": {
            "amount": deferred,
            "note": (
                f"{money(profile, deferred)} is promised for the next visit, so it is a cost tomorrow rather than today."
                if deferred
                else "Nothing is carried into tomorrow by this plan."
            ),
        },
        "notModelled": (
            "The extra units a bundle might sell are not estimated, so all three options assume the same projected "
            "volume. The comparison is per unit on purpose: an invented uplift would make the recommendation win by "
            "construction."
        ),
    }


def bundle_pair_profit(
    rows: list,
    perks: Iterable[dict[str, Any]],
    profile: dict[str, Any],
) -> dict[str, Any]:
    """Cost a bundle across two lines, line by line and combined.

    Answers the question an owner actually asks before pairing two things: *does
    this still make money on each of them, or am I paying for one with the other?*
    Each line gets its own verdict so a pairing that quietly loses money on the
    second item cannot hide behind a healthy total.
    """
    lines: list[dict[str, Any]] = []
    total_margin = 0.0
    total_value = 0.0
    total_perk_cost = 0.0
    total_deferred = 0.0

    for row in rows[:3]:
        plan = bundle_plan(row, perks, profile)
        metrics = plan["metrics"]
        price = float(row["BasePrice"])
        threshold = round(price * profile["marginFloorPct"] / 100, 2)
        margin = round(float(metrics["marginPreserved"]), 2)
        units = int(row["TotalCapacity"] - row["UnitsSold"])
        if margin <= threshold:
            verdict = "even"
        elif margin - threshold < threshold * 0.1:
            verdict = "thin"
        else:
            verdict = "makes money"
        lines.append(
            {
                "itemId": row["ItemID"],
                "itemName": row["ItemName"],
                "category": row["Category"],
                "shelfPrice": round(price, 2),
                "floorPerUnit": threshold,
                "marginPerUnit": margin,
                "headroomPerUnit": round(margin - threshold, 2),
                "perkCost": round(float(metrics["perkCost"]), 2),
                "unitsOnShelf": units,
                "valueOnShelf": round(units * price, 2),
                "profitIfAllSells": round(units * margin, 2),
                "hoursRemaining": metrics["hoursRemaining"],
                "verdict": verdict,
                "perks": [tier["label"] for tier in plan["recommendation"]["tiers"] if tier["status"] == "funded"],
            }
        )
        total_margin += units * margin
        total_value += units * price
        total_perk_cost += float(metrics["perkCost"])
        total_deferred += float(metrics["deferredCommitment"])

    thin = [line["itemName"] for line in lines if line["verdict"] in {"even", "thin"}]
    if thin:
        summary = f"{', '.join(thin)} clears at the floor, so it is a survivor rather than an earner."
    else:
        summary = "Every line in this pairing clears its floor with room to spare."

    return {
        "lines": lines,
        "combined": {
            "valueOnShelf": round(total_value, 2),
            "profitIfAllSells": round(total_margin, 2),
            "perkCostToday": round(total_perk_cost, 2),
            "deferredToNextVisit": round(total_deferred, 2),
            "lines": len(lines),
        },
        "verdict": summary,
        "method": (
            "Per line: shelf price, less the perks that fit above that line's own floor. Profit here means shelf price less "
            "perk cost, not cost of goods, which this workspace does not record."
        ),
    }


def discount_impact(row, profile: dict[str, Any], percent: float) -> dict[str, Any]:
    """What a markdown would do to the floor, used to refuse discount requests."""
    price = float(row["BasePrice"])
    metrics = item_metrics(row)
    threshold = round(price * profile["marginFloorPct"] / 100, 2)
    cut = round(price * percent / 100, 2)
    new_price = round(price - cut, 2)
    return {
        "itemName": row["ItemName"],
        "basePrice": price,
        "percent": percent,
        "cut": cut,
        "newPrice": new_price,
        "marginBefore": round(price - threshold, 2),
        "marginAfter": round(new_price - threshold, 2),
        "floorThreshold": threshold,
        "breachesFloor": new_price < threshold,
        "hoursRemaining": metrics["hoursRemaining"],
        "unitsAvailable": row["TotalCapacity"] - row["UnitsSold"],
        "valueAtRisk": round((row["TotalCapacity"] - row["UnitsSold"]) * price, 2),
    }
