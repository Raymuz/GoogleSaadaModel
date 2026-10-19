"""
GoogleSaada V8 research engine adapter.

The underlying historical engine is preserved.
This module does not authorize production predictions.
"""

from pathlib import Path
import sys
import threading

SOURCE = Path(
    "/content/GS_Original10_Replay_Recovery/"
    "stage4_original_backend/source"
)

_engine = None
_engine_lock = threading.Lock()


def get_engine():
    global _engine

    with _engine_lock:
        if _engine is None:
            if not SOURCE.is_dir():
                raise RuntimeError(
                    "Recovered historical source unavailable"
                )

            source_path = str(SOURCE)
            if source_path not in sys.path:
                sys.path.insert(0, source_path)

            from backend.dynamic_research_inference_v3_markets_recent import (
                DynamicResearchInference,
            )

            _engine = DynamicResearchInference()

        return _engine


def analyze_source_fixtures(fixtures):
    """
    Execute the actual historical inference method.

    Only research output is returned. The engine's identity,
    calibration and production-authorization fields are retained.
    """
    if not isinstance(fixtures, list):
        raise ValueError("Fixtures must be a list")

    engine = get_engine()
    results = engine.analyze(fixtures)

    if not isinstance(results, list):
        raise RuntimeError("Unexpected research output")

    if len(results) != len(fixtures):
        raise RuntimeError("Fixture/result count mismatch")

    for item in results:
        if not isinstance(item, dict):
            raise RuntimeError("Malformed research result")

        if item.get("production_prediction_authorized") is not False:
            raise RuntimeError(
                "Unexpected production authorization"
            )

        if item.get("identity_approved") is not False:
            raise RuntimeError(
                "Historical identity research incorrectly approved"
            )

        if item.get("calibration_verified") is not False:
            raise RuntimeError(
                "Unverified calibration incorrectly approved"
            )

    return results


def research_day(day, league="All supported leagues",
                 time_zone="Africa/Douala", transport=None):
    """
    Use the actual existing discovery and inference pipeline.

    The transport argument supports controlled offline testing.
    In deployed operation, the original engine uses its provider.
    """
    engine = get_engine()

    result = engine.day(
        day,
        league,
        time_zone,
        transport,
    )

    if not isinstance(result, dict):
        raise RuntimeError("Invalid research-day response")

    required_flags = {
        "research_only": True,
        "production_predictions_enabled": False,
        "club_identities_approved": False,
        "market_calibration_verified": False,
        "historical_results_refreshed": False,
    }

    for key, expected in required_flags.items():
        if result.get(key) is not expected:
            raise RuntimeError(
                "Research safety contract violation: " + key
            )

    if result.get("public_betting_picks") != []:
        raise RuntimeError(
            "Unapproved betting picks must not be published"
        )

    rows = result.get("research_results")
    if not isinstance(rows, list):
        raise RuntimeError("Research results must be a list")

    for row in rows:
        if row.get("production_prediction_authorized") is not False:
            raise RuntimeError(
                "Unapproved match result was authorized"
            )

    return result
