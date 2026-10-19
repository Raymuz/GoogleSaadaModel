
import importlib.util
import json
import traceback
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from pandas.api.types import is_numeric_dtype


ROOT = Path(__file__).resolve().parent
DEPLOY = ROOT / "deployment_files"
AUDIT = DEPLOY / "preprocessing_audit"

REPORT = (
    AUDIT / "step11b3d_future_feature_probe.json"
)

SOURCE = ROOT / "backend/original_safe_features.py"
HISTORY = DEPLOY / "feature_state/matches.parquet"

GOLDEN = (
    DEPLOY
    / "feature_verification/safe_feature_golden_reference.parquet"
)

CONTRACTS = DEPLOY / "prematch_model_contracts.json"


# -----------------------------------------------------------
# 3. LOCAL TEST HELPERS
# -----------------------------------------------------------

def write_report(data):

    AUDIT.mkdir(
        parents=True,
        exist_ok=True
    )

    REPORT.write_text(
        json.dumps(
            data,
            indent=2,
            default=str
        ),
        encoding="utf-8"
    )


def next_ids(series, n):

    present = series.dropna()

    if present.empty:
        raise RuntimeError(
            "No historical match IDs available."
        )

    if is_numeric_dtype(series):

        top = int(
            pd.to_numeric(present).max()
        )

        return [
            top + i + 1
            for i in range(n)
        ]

    if all(
        isinstance(v, str)
        for v in present.head(100)
    ):

        candidates = [
            f"__SOCCER_11B3D_PROBE_{i}__"
            for i in range(n)
        ]

        existing = set(
            present.astype(str)
        )

        if any(
            value in existing
            for value in candidates
        ):
            raise RuntimeError(
                "Research probe ID collision."
            )

        return candidates

    raise RuntimeError(
        "Mixed match ID types require review."
    )


def numeric_equal(a, b):

    x = a.to_numpy(
        dtype=float,
        na_value=np.nan
    )

    y = b.to_numpy(
        dtype=float,
        na_value=np.nan
    )

    return bool(
        np.isclose(
            x,
            y,
            rtol=1e-10,
            atol=1e-9,
            equal_nan=True
        ).all()
    )


# -----------------------------------------------------------
# 4. CHECK HISTORICAL REFERENCE PRESERVATION
# -----------------------------------------------------------

def historical_reference_checks(
    rebuilt,
    golden
):

    expected = golden.copy()
    actual = rebuilt.copy()

    expected["__key__"] = (
        expected["match_id"]
        .astype("string")
        .str.strip()
    )

    actual["__key__"] = (
        actual["match_id"]
        .astype("string")
        .str.strip()
    )

    if (
        expected["__key__"].duplicated().any()
        or actual["__key__"].duplicated().any()
    ):
        raise RuntimeError(
            "Duplicate match IDs prevent comparison."
        )

    expected = expected.set_index(
        "__key__"
    )

    actual = actual.set_index(
        "__key__"
    )

    if not expected.index.isin(
        actual.index
    ).all():
        raise RuntimeError(
            "Golden historical matches missing."
        )

    actual = actual.loc[
        expected.index
    ]

    problems = {}

    columns = [
        c for c in expected.columns
        if c not in {
            "match_id",
            "__original_row_position"
        }
    ]

    for column in columns:

        if column not in actual.columns:

            problems[column] = "MISSING"
            continue

        a = (
            expected[column]
            .reset_index(drop=True)
        )

        b = (
            actual[column]
            .reset_index(drop=True)
        )

        if column in {
            "match_day",
            "date_utc"
        }:

            x = pd.to_datetime(
                a, utc=True
            )

            y = pd.to_datetime(
                b, utc=True
            )

            ok = bool(
                (
                    x.eq(y)
                    | (x.isna() & y.isna())
                ).all()
            )

        elif (
            is_numeric_dtype(a)
            and is_numeric_dtype(b)
        ):

            ok = numeric_equal(a, b)

        else:

            x = a.astype("string")
            y = b.astype("string")

            ok = bool(
                (
                    x.eq(y)
                    | (x.isna() & y.isna())
                ).fillna(False).all()
            )

        if not ok:
            problems[column] = "VALUES_DIFFER"

    return len(columns), problems


# -----------------------------------------------------------
# 5. CONSTRUCT RESEARCH-ONLY FUTURE FIXTURES
# -----------------------------------------------------------

def run():

    print("=" * 70)
    print(
        "STEP 11B.3D — FUTURE-FIXTURE CAUSALITY TEST"
    )
    print("=" * 70)

    raw = pd.read_parquet(HISTORY)
    golden = pd.read_parquet(GOLDEN)

    contracts = json.loads(
        CONTRACTS.read_text(
            encoding="utf-8"
        )
    )

    required = [
        "match_id",
        "date_utc",
        "is_played",
        "home_team_key",
        "away_team_key",
        "league_id",
        "league_name",
        "home_team_name",
        "away_team_name"
    ]

    missing = [
        c for c in required
        if c not in raw.columns
    ]

    if missing:
        raise RuntimeError(
            "Historical input columns missing: "
            + str(missing)
        )

    # Select an existing completed historical
    # pairing as a research test template.

    played = (
        raw["is_played"]
        .fillna(False)
        .eq(True)
    )

    candidates = (
        raw.loc[played]
        .copy()
    )

    candidates["__date__"] = (
        pd.to_datetime(
            candidates["date_utc"],
            utc=True,
            errors="coerce"
        )
    )

    for col in (
        "home_team_key",
        "away_team_key",
        "league_id",
        "league_name",
        "home_team_name",
        "away_team_name"
    ):

        candidates = candidates.loc[
            candidates[col].notna()
            & candidates[col]
                .astype("string")
                .str.strip()
                .ne("")
        ]

    candidates = candidates.loc[
        candidates[
            "home_team_key"
        ].astype("string")
        != candidates[
            "away_team_key"
        ].astype("string")
    ]

    candidates = candidates.dropna(
        subset=["__date__"]
    )

    if candidates.empty:
        raise RuntimeError(
            "No historical team pairing "
            "suitable for the research probe."
        )

    sample = (
        candidates
        .sort_values(
            "__date__",
            kind="stable"
        )
        .iloc[-1]
    )

    latest = pd.to_datetime(
        raw["date_utc"],
        utc=True,
        errors="coerce"
    ).max()

    if pd.isna(latest):
        raise RuntimeError(
            "Historical match dates unavailable."
        )

    # Two research fixtures on the SAME UTC day.
    # Date comes after the historical snapshot.

    future_day = (
        latest.normalize()
        + pd.Timedelta(days=7)
    )

    kickoffs = [
        future_day + pd.Timedelta(hours=h)
        for h in (8, 16)
    ]

    ids = next_ids(
        raw["match_id"], 2
    )

    # Remove all available post-match outcomes
    # and match statistics from the probe.

    post_match_fields = [
        "goals_home",
        "goals_away",
        "home_goals",
        "away_goals",
        "outcome",
        "btts",
        "home_shots_total",
        "away_shots_total",
        "home_shots_on_goal",
        "away_shots_on_goal",
        "home_corners",
        "away_corners",
        "home_xg",
        "away_xg",
        "referee_name",
        "referee_api_id"
    ]

    probes = []

    for idx in range(2):

        item = sample.drop(
            labels=["__date__"]
        ).copy()

        item["match_id"] = ids[idx]

        item["date_utc"] = (
            kickoffs[idx].isoformat()
        )

        item["is_played"] = False

        if "status" in raw.columns:
            item["status"] = "NS"

        if "status_norm" in raw.columns:
            item["status_norm"] = "NS"

        if "calendar_year" in raw.columns:
            item["calendar_year"] = int(
                kickoffs[idx].year
            )

        if "source" in raw.columns:
            item["source"] = (
                "RESEARCH_PROBE_ONLY"
            )

        for column in post_match_fields:

            if column in raw.columns:
                item[column] = np.nan

        for column in (
            "api_football_id",
            "fixture_id"
        ):

            if column in raw.columns:
                item[column] = np.nan

        probes.append(item)

    augmented = pd.concat(
        [
            raw,
            pd.DataFrame(
                probes,
                columns=raw.columns
            )
        ],
        ignore_index=True
    )

    if len(augmented) != len(raw) + 2:
        raise RuntimeError(
            "Unexpected fixture count."
        )

    if (
        augmented["match_id"]
        .astype("string")
        .duplicated()
        .any()
    ):
        raise RuntimeError(
            "Duplicate fixture identity."
        )

    # -------------------------------------------------------
    # 6. LOAD ORIGINAL PORTABLE FEATURE ENGINE
    # -------------------------------------------------------

    spec = (
        importlib.util.spec_from_file_location(
            "portable_safe_features",
            SOURCE
        )
    )

    module = (
        importlib.util.module_from_spec(
            spec
        )
    )

    spec.loader.exec_module(
        module
    )

    print(
        "Original historical rows:",
        len(raw)
    )

    print(
        "Research-only unplayed probes: 2"
    )

    print(
        "Probe UTC date:",
        future_day.date()
    )

    print(
        "Recalculating using ORIGINAL "
        "feature engine..."
    )

    regenerated = (
        module.build_safe_soccer_features(
            augmented
        )
    )

    if len(regenerated) != len(augmented):
        raise RuntimeError(
            "Feature engine dropped or "
            "duplicated records."
        )

    # -------------------------------------------------------
    # 7. VERIFY HISTORICAL FEATURES DID NOT CHANGE
    # -------------------------------------------------------

    checked_columns, history_errors = (
        historical_reference_checks(
            regenerated,
            golden
        )
    )

    # -------------------------------------------------------
    # 8. VERIFY FUTURE FEATURE AVAILABILITY
    # -------------------------------------------------------

    generated_ids = (
        regenerated["match_id"]
        .astype("string")
    )

    probe_ids = [
        str(i) for i in ids
    ]

    selected = regenerated.loc[
        generated_ids.isin(
            probe_ids
        )
    ].copy()

    if len(selected) != 2:
        raise RuntimeError(
            "The two future probes were not found."
        )

    selected["__key__"] = (
        selected["match_id"]
        .astype("string")
    )

    selected = (
        selected
        .set_index("__key__")
        .loc[probe_ids]
    )

    categories = {
        "league_name",
        "home_team_name",
        "away_team_name"
    }

    contract_features = {
        role: record["features_in_order"]
        for role, record in contracts.items()
    }

    all_features = sorted({
        column
        for feature_names
            in contract_features.values()
        for column in feature_names
    })

    unknown = sorted({
        column for column in all_features
        if (
            column not in selected.columns
            and column not in raw.columns
        )
    })

    if unknown:
        raise RuntimeError(
            "Unknown trained-model features: "
            + repr(unknown)
        )

    numeric_features = [
        c for c in all_features
        if c not in categories
    ]

    feature_errors = []
    nonfinite_features = []

    for column in numeric_features:

        if column not in selected.columns:

            feature_errors.append(
                f"{column}: unavailable"
            )

            continue

        values = pd.to_numeric(
            selected[column],
            errors="coerce"
        )

        if not np.isfinite(
            values.to_numpy(dtype=float)
        ).all():

            nonfinite_features.append(
                column
            )

        # Both unplayed fixtures have the same
        # teams and UTC match day. Prior-day
        # numerical feature values must agree.

        if not numeric_equal(
            values.iloc[:1],
            values.iloc[1:]
        ):

            feature_errors.append(
                f"{column}: changed within "
                "the same frozen UTC day"
            )

    raw_probe = (
        pd.DataFrame(probes)
        .reset_index(drop=True)
    )

    for column in (
        categories & set(all_features)
    ):

        if (
            raw_probe[column].isna().any()
            or raw_probe[column]
                .astype(str)
                .str.strip()
                .eq("")
                .any()
        ):

            feature_errors.append(
                f"{column}: missing category"
            )

        if (
            raw_probe[column].iloc[0]
            != raw_probe[column].iloc[1]
        ):

            feature_errors.append(
                f"{column}: inconsistent"
            )

    passed = (
        not history_errors
        and not feature_errors
        and not unknown
    )

    # -------------------------------------------------------
    # 9. SAVE AUDIT RESULTS
    # -------------------------------------------------------

    report = {
        "step": "11B.3D",

        "created_utc":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "historical_rows":
            int(len(raw)),

        "synthetic_unplayed_probe_rows": 2,

        "research_probes_not_real_fixtures":
            True,

        "probe_utc_date":
            future_day.date().isoformat(),

        "golden_matches_checked":
            len(golden),

        "historical_columns_checked":
            checked_columns,

        "historical_feature_errors":
            history_errors,

        "same_day_feature_errors":
            feature_errors,

        "numeric_features_with_nonfinite_values":
            nonfinite_features,

        "model_contracts_checked":
            len(contract_features),

        "model_feature_names_available":
            not bool(unknown),

        "research_causality_probe_passed":
            passed,

        "live_fixture_pipeline_verified":
            False,

        "historical_identity_approved":
            False,

        "production_predictions_enabled":
            False,

        "google_drive_runtime_dependency":
            False
    }

    write_report(report)

    # -------------------------------------------------------
    # 10. RESULTS
    # -------------------------------------------------------

    print("\n" + "=" * 70)
    print(
        "STEP 11B.3D — HISTORICAL CAUSALITY RESULTS"
    )
    print("=" * 70)

    print(
        "Historical golden matches:",
        len(golden)
    )

    print(
        "Historical columns compared:",
        checked_columns
    )

    print(
        "Historical feature errors:",
        history_errors or "NONE"
    )

    print(
        "Same-day frozen feature errors:",
        feature_errors or "NONE"
    )

    print(
        "All trained-model feature names available:",
        not bool(unknown)
    )

    print(
        "Numeric features with missing/nonfinite values:",
        nonfinite_features or "NONE"
    )

    print(
        "Research causality probe:",
        "PASS" if passed else "REVIEW"
    )

    print(
        "Actual live-fixture pipeline verified: NO"
    )

    print(
        "Production predictions: DISABLED"
    )

    print("\nReport:", REPORT)

    if not passed:
        raise RuntimeError(
            "Future feature probe did not pass."
        )

    print(
        "\nSTEP 11B.3D COMPLETE — "
        "RESEARCH CAUSALITY PASS"
    )


if __name__ == "__main__":

    try:
        run()

    except Exception as exc:

        if not REPORT.is_file():

            write_report({
                "step": "11B.3D",
                "status": "EXECUTION_FAILED",
                "error": (
                    f"{type(exc).__name__}: {exc}"
                ),
                "production_predictions_enabled":
                    False
            })

        traceback.print_exc()
        raise
