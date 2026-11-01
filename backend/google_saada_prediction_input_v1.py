"""GoogleSaada private prediction request adapter.

Reuses the existing verified ten-model dynamic bridge.
Never creates synthetic model probabilities, identities or fixtures.
No paid AI calls.
"""
from __future__ import annotations
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import re

AUTO = re.compile(
    r"^\s*(?:please\s+)?"
    r"(?:predict|analy[sz]e|forecast)\s+"
    r"(?:up\s+to\s+)?(\d{1,3})\s+"
    r"(?:matches|games|fixtures)"
    r"(?:\s+(?:for\s+)?today)?"
    r"\s*[.!]?\s*$",
    re.I
)

NOTICE = (
    "Experimental model estimates from older historical data. "
    "Club identities and probability calibration are not fully approved; "
    "these are not verified betting recommendations."
)

MARKETS = {
    "FT_1X2_HOME": "Home win",
    "FT_1X2_DRAW": "Draw",
    "FT_1X2_AWAY": "Away win",
    "FT_OVER_2_5": "Over 2.5 goals",
    "FT_UNDER_2_5": "Under 2.5 goals",
    "FT_BTTS_YES": "Both teams score — Yes",
    "FT_BTTS_NO": "Both teams score — No",
}

def auto_request(text):
    raw = str(text or "").strip()
    if len(raw) > 1000:
        return None
    match = AUTO.fullmatch(raw)
    if not match:
        return None
    number = int(match.group(1))
    if not 1 <= number <= 50:
        return None
    return {
        "count": number,
        "today": bool(re.search(r"\btoday\b", raw, re.I)),
    }

def predict_auto(bridge, old, count, profile, day, league, tz):
    try:
        if profile not in bridge._levels:
            return "Select a valid analysis preference.", [], NOTICE

        assessed, current = bridge._evaluations(day, league, tz)

        available = [
            item for item in assessed.values()
            if item.get("status")
            == "UNAPPROVED_FROZEN_HISTORY_RESEARCH_ESTIMATE"
            and isinstance(item.get("market_probabilities"), dict)
        ]

        available.sort(
            key=lambda r: (
                old.utc(r["kickoff_utc"]),
                r["key"]
            )
        )

        selected = available[:count]
        results = []

        for item in selected:
            probabilities = item["market_probabilities"]
            name = item["home"] + " vs " + item["away"]

            # The existing bridge already validates the seven markets,
            # numerical ranges, and complementary probability mass.
            for code, label in MARKETS.items():
                if code not in probabilities:
                    raise RuntimeError("Research market missing")
                value = float(probabilities[code])
                if not 0 <= value <= 1:
                    raise RuntimeError("Invalid model estimate")
                results.append([
                    name, label, f"{value:.1%}"
                ])

        held = sum(
            1 for item in assessed.values()
            if item.get("status")
            != "UNAPPROVED_FROZEN_HISTORY_RESEARCH_ESTIMATE"
        )

        message = (
            f"**{len(selected)} of up to {count} requested matches "
            f"analyzed using the original prediction models.**"
        )

        if not selected:
            message += (
                " No currently eligible model estimates were found."
            )

        notes = (
            f"{len(current)} upcoming provider fixtures considered; "
            f"{held} research results on hold due to eligibility "
            "or identity checks. "
            "Only verified upcoming fixtures with available original "
            "model outputs are displayed.\n\n" + NOTICE
        )

        return message, results, notes

    except (OSError, ValueError, RuntimeError, KeyError, TypeError):
        return (
            "Model analysis is temporarily unavailable. "
            "Please check fixtures and try again.",
            [],
            NOTICE
        )

def route(
    bridge, old, keys, text, profile, day, league, tz,
    recommendation_parser
):
    request = auto_request(text)

    if request is not None:
        if keys:
            return (
                "Clear selected fixtures before requesting "
                "automatic match predictions.",
                [],
                NOTICE
            )

        requested_day = (
            datetime.now(ZoneInfo(tz)).date().isoformat()
            if request["today"]
            else day
        )

        return predict_auto(
            bridge, old, request["count"],
            profile, requested_day, league, tz
        )

    return bridge.predictions_or_recommendations(
        keys, text, profile, day, league, tz,
        recommendation_parser
    )
