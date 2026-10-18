
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from backend.gs_v8_verified_runtime import (
    load_runtime, predict_verified, PredictionAbstained
)

REFERENCE = ROOT / (
    "global_v3/feature_engineering/"
    "worldwide_56_features.parquet"
)

builder, engine, names, models = load_runtime()

if len(names) != 56 or len(models) != 3:
    raise RuntimeError("Original builder/model contract invalid")

reference = pd.read_parquet(REFERENCE)

eligible = reference.loc[
    reference["split"].eq("test")
    & reference["both_teams_have_history"].eq(True)
].sort_values("date_utc")

finite = np.isfinite(
    eligible[names].apply(
        pd.to_numeric, errors="coerce"
    ).to_numpy(dtype=float)
).all(axis=1)

eligible = eligible.iloc[np.flatnonzero(finite)]

passes = 0
unavailable = 0

for _, row in eligible.iterrows():
    kickoff = pd.Timestamp(row["date_utc"])
    if kickoff.tzinfo is None:
        kickoff = kickoff.tz_localize("UTC")
    else:
        kickoff = kickoff.tz_convert("UTC")

    cutoff = kickoff - pd.Timedelta(microseconds=1)

    try:
        output = predict_verified(
            home_team_id=str(row["home_team_id"]),
            away_team_id=str(row["away_team_id"]),
            kickoff_utc=kickoff,
            evidence_cutoff_utc=cutoff,
            historical_test=True
        )
    except PredictionAbstained:
        unavailable += 1
        continue

    actual = engine.build(
        home_team_id=str(row["home_team_id"]),
        away_team_id=str(row["away_team_id"]),
        kickoff_utc=kickoff,
        evidence_cutoff_utc=cutoff
    )

    measured = np.asarray(
        [actual[n] for n in names], dtype=float
    )
    archived = row[names].to_numpy(dtype=float)

    if not np.allclose(
        measured, archived, rtol=1e-9, atol=1e-9
    ):
        raise RuntimeError(
            "Original historical feature parity failed"
        )

    if (
        output["feature_count"] != 56
        or len(output["model_outputs"]) != 3
    ):
        raise RuntimeError(
            "Incomplete worldwide predictions"
        )

    passes += 1
    print(
        f"[PASS] VERIFIED ROUTER {passes}/5 "
        f"fixture={row['id']}",
        flush=True
    )

    if passes == 5:
        break

if passes != 5:
    raise RuntimeError(
        f"Only {passes}/5 historically verified fixtures; "
        f"unavailable={unavailable}"
    )

# The Judge must reject post-kickoff evidence.
first = eligible.iloc[0]
when = pd.Timestamp(first["date_utc"])
if when.tzinfo is None:
    when = when.tz_localize("UTC")

try:
    predict_verified(
        str(first["home_team_id"]),
        str(first["away_team_id"]),
        when,
        when + pd.Timedelta(seconds=1),
        historical_test=True
    )
    raise RuntimeError(
        "Judge failed to reject post-kickoff evidence"
    )
except PredictionAbstained:
    print("[PASS] JUDGE REJECTS POST-KICKOFF DATA")

print("[PASS] VERIFIED V8 TECHNICAL JUDGE AND 56-FEATURE PIPELINE")
print("[PENDING] Native Judge production policy acceptance")
print("[PENDING] Hosted protected endpoint E2E")
