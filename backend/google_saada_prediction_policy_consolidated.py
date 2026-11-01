import math

MAX_MATCHES = 16

SUPPORTED_MARKETS = (
    "Full-time result",
    "Home win",
    "Draw",
    "Away win",
    "Double chance",
    "Over 0.5",
    "Under 0.5",
    "Over 1.5",
    "Under 1.5",
    "Over 2.5",
    "Under 2.5",
    "Over 3.5",
    "Under 3.5",
    "BTTS",
    "BTTS Yes",
    "BTTS No",
    "First-half result",
    "Second-half result",
    "Correct score",
)

EVIDENCE_LEVELS = (
    "HISTORICAL",
    "PARTIAL_UNSEEN",
    "COLD_START",
    "SPARSE_PRIOR",
    "INSUFFICIENT_EVIDENCE",
)

DISCLAIMER = (
    "DISCLAIMER: GoogleSaada predictions are statistical "
    "estimates, not guaranteed results. Predictions involving "
    "new, unfamiliar or previously unseen teams may rely on "
    "limited data and can carry substantially greater "
    "uncertainty. Past performance does not guarantee future "
    "outcomes. Verify fixtures and consider the risks before "
    "making betting or financial decisions."
)


def validate_match_limit(count):
    if type(count) is not int:
        return {
            "allowed": False,
            "reason": "Match count must be an integer."
        }

    if not 1 <= count <= MAX_MATCHES:
        return {
            "allowed": False,
            "reason": "Select between 1 and 16 matches."
        }

    return {"allowed": True, "count": count}


def choose_prediction_path(
    home_history,
    away_history,
    compatible_model=False,
    unseen_capable_model=False,
    cold_start_model=False,
    league_prior_model=False
):
    # Model flags must be supplied by a trusted model registry
    # after compatibility and evaluation checks.
    if home_history and away_history and compatible_model:
        return "HISTORICAL"

    if (
        (home_history or away_history)
        and unseen_capable_model
    ):
        return "PARTIAL_UNSEEN"

    if cold_start_model:
        return "COLD_START"

    if league_prior_model:
        return "SPARSE_PRIOR"

    return "INSUFFICIENT_EVIDENCE"


def validate_prediction_record(record):
    if not isinstance(record, dict):
        return False

    required = (
        "fixture_id",
        "home_team",
        "away_team",
        "market",
        "probability",
        "model_source",
        "evidence_level",
        "model_approved",
        "features_verified"
    )

    if any(field not in record for field in required):
        return False

    if record["evidence_level"] not in EVIDENCE_LEVELS:
        return False

    if record["evidence_level"] == "INSUFFICIENT_EVIDENCE":
        return False

    # These booleans must be established by the trusted inference
    # service, not accepted from arbitrary user text.
    if record["model_approved"] is not True:
        return False

    if record["features_verified"] is not True:
        return False

    if record["market"] not in SUPPORTED_MARKETS:
        return False

    if not str(record["model_source"] or "").strip():
        return False

    if not str(record["fixture_id"] or "").strip():
        return False

    if not str(record["home_team"] or "").strip():
        return False

    if not str(record["away_team"] or "").strip():
        return False

    probability = record["probability"]

    if type(probability) not in (int, float):
        return False

    if not math.isfinite(probability):
        return False

    return 0 <= probability <= 1


def rank_predictions(records, market, limit=16):
    check = validate_match_limit(limit)

    if not check["allowed"]:
        raise ValueError(check["reason"])

    eligible = [
        record for record in records
        if validate_prediction_record(record)
        and record["market"] == market
    ]

    # Multiple predictions from different models for the same
    # fixture must not be selected just because one is higher.
    # Ambiguous duplicate predictions are excluded until a
    # validated model-selection or ensemble policy resolves them.
    by_fixture = {}

    for record in eligible:
        fid = str(record["fixture_id"])
        by_fixture.setdefault(fid, []).append(record)

    unique = [
        values[0]
        for values in by_fixture.values()
        if len(values) == 1
    ]

    unique.sort(
        key=lambda record: (
            -float(record["probability"]),
            str(record["fixture_id"])
        )
    )

    return unique[:limit]


def append_disclaimer(text):
    body = str(text or "").strip()

    if body.endswith(DISCLAIMER):
        return body

    if body:
        return body + "\n\n" + DISCLAIMER

    return DISCLAIMER
