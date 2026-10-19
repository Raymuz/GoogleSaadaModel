
import importlib.util
import hashlib
import hmac
import os
import sys
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

BUILDER_PATH = ROOT / (
    "global_v3/v2g_staging/google_saada_v2g_live56.py"
)

HISTORY_PATH = ROOT / (
    "global_v3/v2g_staging/"
    "v2m_verified_world_history_1612e09be8891b3b.parquet"
)

MODELS = {
    "btts":
        "global_v3/v2g_staging/models/worldwide_btts.joblib",
    "match_result":
        "global_v3/v2g_staging/models/worldwide_match_result.joblib",
    "over_2_5":
        "global_v3/v2g_staging/models/worldwide_over_2_5.joblib",
}

HISTORY_HASH = "73fc5ede4ffe1c812d0a5982ccebb29ec8d0daf779ecd06bfa0f016666f4fd83"

# Reject stale historical evidence for public-facing future
# predictions. Historical parity tests explicitly opt out.
MAX_HISTORY_AGE_DAYS = 30

class PredictionAbstained(Exception):
    pass

def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024*1024), b""):
            h.update(block)
    return h.hexdigest()

@lru_cache(maxsize=1)
def load_runtime():
    if not BUILDER_PATH.is_file():
        raise PredictionAbstained("Verified feature builder unavailable")

    if not HISTORY_PATH.is_file():
        raise PredictionAbstained("Verified historical evidence unavailable")

    if digest(HISTORY_PATH) != HISTORY_HASH:
        raise PredictionAbstained("Historical evidence hash mismatch")

    spec = importlib.util.spec_from_file_location(
        "gs_v8_verified_history56", BUILDER_PATH
    )
    if spec is None or spec.loader is None:
        raise PredictionAbstained("Cannot import verified builder")

    builder = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = builder
    spec.loader.exec_module(builder)

    names = list(builder.FEATURE_NAMES)
    if len(names) != 56 or len(set(names)) != 56:
        raise PredictionAbstained("56-feature contract invalid")

    engine = builder.GlobalHistory56(
        HISTORY_PATH,
        expected_sha256=HISTORY_HASH,
        feature_names=names,
        min_prior_matches=5
    )

    models = {}
    for role, relative in MODELS.items():
        path = ROOT / relative
        if not path.is_file():
            raise PredictionAbstained(
                "Required trained model unavailable: " + role
            )

        # Only original SHA-verified, trusted model files.
        model = joblib.load(path)

        expected = getattr(model, "feature_names_in_", None)
        if expected is None or list(expected) != names:
            raise PredictionAbstained(
                "Trained feature contract mismatch: " + role
            )

        if not callable(getattr(model, "predict_proba", None)):
            raise PredictionAbstained(
                "Classifier probability interface unavailable"
            )

        models[role] = model

    return builder, engine, names, models

def utc(value):
    timestamp = pd.Timestamp(value)
    if pd.isna(timestamp):
        raise PredictionAbstained("Invalid kickoff time")
    if timestamp.tzinfo is None:
        raise PredictionAbstained(
            "Kickoff requires an explicit timezone"
        )
    return timestamp.tz_convert("UTC")

def judge_fixture(
    home_team_id,
    away_team_id,
    kickoff_utc,
    evidence_cutoff_utc,
    historical_test=False
):
    if not isinstance(home_team_id, str) or not home_team_id.strip():
        raise PredictionAbstained("Missing verified home-team identity")

    if not isinstance(away_team_id, str) or not away_team_id.strip():
        raise PredictionAbstained("Missing verified away-team identity")

    if home_team_id == away_team_id:
        raise PredictionAbstained("Identical home/away identities")

    kickoff = utc(kickoff_utc)
    cutoff = utc(evidence_cutoff_utc)
    now = pd.Timestamp.now(tz="UTC")

    if cutoff >= kickoff:
        raise PredictionAbstained(
            "Feature evidence must precede kickoff"
        )

    if not historical_test:
        if kickoff <= now:
            raise PredictionAbstained(
                "Fixture has started or finished"
            )
        if cutoff > now:
            raise PredictionAbstained(
                "Cannot use future evidence"
            )
        if kickoff > now + pd.Timedelta(days=7):
            raise PredictionAbstained(
                "Fixture outside seven-day eligibility window"
            )

    return kickoff, cutoff

def predict_verified(
    home_team_id,
    away_team_id,
    kickoff_utc,
    evidence_cutoff_utc,
    historical_test=False
):
    kickoff, cutoff = judge_fixture(
        home_team_id, away_team_id,
        kickoff_utc, evidence_cutoff_utc,
        historical_test=historical_test
    )

    builder, engine, names, models = load_runtime()

    if not historical_test:
        # This protected candidate cannot claim current source
        # freshness unless its historical evidence is recent.
        history = pd.read_parquet(
            HISTORY_PATH, columns=["date_utc"]
        )
        latest = pd.to_datetime(
            history["date_utc"], utc=True
        ).max()

        if (
            pd.Timestamp.now(tz="UTC") - latest
            > pd.Timedelta(days=MAX_HISTORY_AGE_DAYS)
        ):
            raise PredictionAbstained(
                "Verified historical source is stale"
            )

    try:
        features = engine.build(
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            kickoff_utc=kickoff,
            evidence_cutoff_utc=cutoff
        )
    except builder.FeatureUnavailable as exc:
        raise PredictionAbstained(
            "Insufficient verified pre-match history"
        ) from exc

    if set(features) != set(names):
        raise PredictionAbstained(
            "Generated 56-feature schema mismatch"
        )

    values = np.array(
        [features[name] for name in names], dtype=float
    )

    if not np.isfinite(values).all():
        raise PredictionAbstained("Non-finite model inputs")

    X = pd.DataFrame([values], columns=names)
    results = {}

    for role, model in models.items():
        try:
            p = np.asarray(
                model.predict_proba(X), dtype=float
            )
        except Exception as exc:
            raise PredictionAbstained(
                "Model inference unavailable: " + role
            ) from exc

        classes = [str(x) for x in model.classes_]

        if (
            p.shape != (1, len(classes))
            or not np.isfinite(p).all()
            or (p < 0).any()
            or (p > 1).any()
            or not np.allclose(p.sum(axis=1), 1, atol=1e-6)
        ):
            raise PredictionAbstained(
                "Invalid probability output: " + role
            )

        results[role] = {
            "classes": classes,
            "probabilities": p[0].tolist()
        }

    return {
        "status": "TECHNICALLY_VERIFIED",
        "judge": "TECHNICAL_GATE_ONLY",
        "production_approved": False,
        "calibration_applied": False,
        "feature_count": 56,
        "kickoff_utc": kickoff.isoformat(),
        "evidence_cutoff_utc": cutoff.isoformat(),
        "model_outputs": results,
    }
