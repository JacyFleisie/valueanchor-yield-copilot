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
