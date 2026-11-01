
import importlib.util
import json
import sys
import traceback

from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype


ROOT = Path(__file__).resolve().parent
DEPLOY = ROOT / "deployment_files"

SOURCE = ROOT / "backend/original_safe_features.py"

MATCHES = (
    DEPLOY / "feature_state/matches.parquet"
)

GOLDEN = (
    DEPLOY
    / "feature_verification/safe_feature_golden_reference.parquet"
)

AUDIT = DEPLOY / "preprocessing_audit"

REPORT = (
    AUDIT / "step11b3c_feature_reproduction.json"
)

DETAILS = (
    AUDIT / "step11b3c_column_comparison.csv"
)

AUDIT.mkdir(parents=True, exist_ok=True)


def write_report(data):
    REPORT.write_text(
        json.dumps(
            data,
            indent=2,
            ensure_ascii=False,
            default=str
        ),
        encoding="utf-8"
    )


def normalized_ids(series):
    return series.astype("string").str.strip()


def compare_column(column, reference, generated):

    # Historical timestamps should represent
    # the same instant even if displayed in
    # different timezone-aware formats.

    if column in ("date_utc", "match_day"):

        a = pd.to_datetime(
            reference,
            utc=True,
            errors="raise"
        )

        b = pd.to_datetime(
            generated,
            utc=True,
            errors="raise"
        )

        equal = (
            a.eq(b)
            | (a.isna() & b.isna())
        ).fillna(False)

        maximum_error = None

    elif (
        is_numeric_dtype(reference)
        and is_numeric_dtype(generated)
    ):

        a = reference.to_numpy(
            dtype=float,
            na_value=np.nan
        )

        b = generated.to_numpy(
            dtype=float,
            na_value=np.nan
        )

        if (
            np.isinf(a).any()
            or np.isinf(b).any()
        ):
            raise ValueError(
                f"Infinite value in column {column}"
            )

        equal = np.isclose(
            a,
            b,
            rtol=1e-10,
            atol=1e-9,
            equal_nan=True
        )

        valid = (
            np.isfinite(a)
            & np.isfinite(b)
        )

        maximum_error = (
            float(np.max(np.abs(a[valid] - b[valid])))
            if valid.any()
            else 0.0
        )

    else:

        a = reference.astype("string")
        b = generated.astype("string")

        equal = (
            a.eq(b)
            | (a.isna() & b.isna())
        ).fillna(False).to_numpy(dtype=bool)

        maximum_error = None

    equal = np.asarray(
        equal,
        dtype=bool
    )

    mismatch_positions = np.flatnonzero(~equal)

    examples = []

    for i in mismatch_positions[:3]:

        examples.append({
            "row": int(i),
            "expected": str(reference.iloc[i]),
            "actual": str(generated.iloc[i])
        })

    return {
        "column": column,
        "rows_compared": len(equal),
        "mismatches": int(
            len(mismatch_positions)
        ),
        "max_absolute_difference": maximum_error,
        "examples": examples
    }


def main():

    print("=" * 70)
    print("STEP 11B.3C — CLEAN PROCESS FEATURE REGENERATION")
    print("=" * 70)

    # -----------------------------------------------------------
    # 2. LOAD THE ORIGINAL PORTABLE FEATURE MODULE
    # -----------------------------------------------------------

    spec = importlib.util.spec_from_file_location(
        "soccer_original_safe_features_11b3c",
        SOURCE
    )

    module = importlib.util.module_from_spec(spec)

    spec.loader.exec_module(module)

    builder = getattr(
        module,
        "build_safe_soccer_features",
        None
    )

    if not callable(builder):
        raise RuntimeError(
            "Original feature builder is not callable."
        )

    # -----------------------------------------------------------
    # 3. LOAD HISTORICAL DATA FROM THE DEPLOYMENT DIRECTORY
    # -----------------------------------------------------------

    matches = pd.read_parquet(MATCHES)
    golden = pd.read_parquet(GOLDEN)

    print("Historical source rows:", len(matches))
    print("Golden reference rows:", len(golden))

    if len(matches) != 367569:
        raise RuntimeError(
            "Historical source row count differs "
            "from the verified Step 11B.3B snapshot."
        )

    if len(golden) != 64:
        raise RuntimeError(
            "Expected 64 historical reference rows."
        )

    # -----------------------------------------------------------
    # 4. RECOMPUTE FEATURES USING THE ORIGINAL FUNCTION
    # -----------------------------------------------------------

    print(
        "\nRunning ORIGINAL "
        "build_safe_soccer_features..."
    )

    regenerated = builder(matches)

    if not isinstance(regenerated, pd.DataFrame):
        raise RuntimeError(
            "Original builder did not return a DataFrame."
        )

    print(
        "Recalculated feature rows:",
        len(regenerated)
    )

    print(
        "Recalculated feature columns:",
        len(regenerated.columns)
    )

    if len(regenerated) != len(matches):
        raise RuntimeError(
            "Historical regeneration row count differs."
        )

    # -----------------------------------------------------------
    # 5. ALIGN BY ORIGINAL MATCH ID
    # -----------------------------------------------------------
    # Never assume identical DataFrame row ordering.
    # -----------------------------------------------------------

    if (
        "match_id" not in golden.columns
        or "match_id" not in regenerated.columns
    ):
        raise RuntimeError(
            "Cannot align original results: match_id missing."
        )

    reference_ids = normalized_ids(
        golden["match_id"]
    )

    generated_ids = normalized_ids(
        regenerated["match_id"]
    )

    if (
        reference_ids.isna().any()
        or reference_ids.eq("").any()
        or reference_ids.duplicated().any()
    ):
        raise RuntimeError(
            "Golden reference match IDs are not unique."
        )

    if (
        generated_ids.isna().any()
        or generated_ids.eq("").any()
        or generated_ids.duplicated().any()
    ):
        raise RuntimeError(
            "Regenerated match IDs are not unique."
        )

    reference = golden.copy()
    generated = regenerated.copy()

    reference["_comparison_id"] = (
        reference_ids.to_numpy()
    )

    generated["_comparison_id"] = (
        generated_ids.to_numpy()
    )

    reference = reference.set_index(
        "_comparison_id"
    )

    generated = generated.set_index(
        "_comparison_id"
    )

    missing_ids = (
        reference.index.difference(
            generated.index
        )
    )

    if len(missing_ids):
        raise RuntimeError(
            "Missing original match IDs: "
            f"{list(missing_ids[:10])}"
        )

    generated = generated.loc[
        reference.index
    ]

    print(
        "Match IDs aligned:",
        len(reference)
    )

    # -----------------------------------------------------------
    # 6. COMPARE ORIGINAL AND REGENERATED FEATURES
    # -----------------------------------------------------------

    excluded = {
        "__original_row_position",
        "match_id"
    }

    required_columns = [
        col for col in reference.columns
        if col not in excluded
    ]

    missing_columns = [
        col for col in required_columns
        if col not in generated.columns
    ]

    if missing_columns:
        raise RuntimeError(
            "Recalculated feature columns missing: "
            f"{missing_columns}"
        )

    comparison = []

    for column in required_columns:

        a = reference[column].reset_index(drop=True)
        b = generated[column].reset_index(drop=True)

        comparison.append(
            compare_column(column, a, b)
        )

    comparison_df = pd.DataFrame([
        {
            "Column": item["column"],
            "Rows_Compared": item["rows_compared"],
            "Mismatches": item["mismatches"],
            "Max_Absolute_Difference":
                item["max_absolute_difference"]
        }
        for item in comparison
    ])

    comparison_df.to_csv(
        DETAILS,
        index=False
    )

    problem_columns = [
        entry for entry in comparison
        if entry["mismatches"] > 0
    ]

    total_mismatches = sum(
        entry["mismatches"]
        for entry in comparison
    )

    passed = (
        len(problem_columns) == 0
        and len(required_columns) > 0
    )

    # -----------------------------------------------------------
    # 7. SAVE ACTUAL VERIFICATION RESULTS
    # -----------------------------------------------------------

    report = {
        "step": "11B.3C",

        "created_utc":
            datetime.now(timezone.utc).isoformat(),

        "original_feature_engine_imported": True,

        "source_rows": len(matches),

        "regenerated_rows": len(regenerated),

        "golden_reference_rows": len(golden),

        "match_ids_aligned": len(reference),

        "features_compared":
            len(required_columns),

        "columns_with_mismatches":
            len(problem_columns),

        "total_value_mismatches":
            total_mismatches,

        "historical_reference_reproduction_passed":
            passed,

        "all_historical_rows_compared":
            False,

        "upcoming_fixture_features_verified":
            False,

        "current_elo_form_state_verified":
            False,

        "production_predictions_enabled":
            False,

        "google_drive_runtime_dependency":
            False,

        "problem_columns":
            problem_columns,

        "column_report":
            str(DETAILS.relative_to(ROOT))
    }

    write_report(report)

    # -----------------------------------------------------------
    # 8. OUTPUT
    # -----------------------------------------------------------

    print("\n" + "=" * 70)
    print("HISTORICAL FEATURE REPRODUCTION RESULTS")
    print("=" * 70)

    print(
        "Original feature columns compared:",
        len(required_columns)
    )

    print(
        "Golden reference matches:",
        len(reference)
    )

    print(
        "Columns with mismatches:",
        len(problem_columns)
    )

    print(
        "Total value mismatches:",
        total_mismatches
    )

    if passed:

        print(
            "HISTORICAL REFERENCE REPRODUCTION: PASS"
        )

    else:

        print(
            "HISTORICAL REFERENCE REPRODUCTION: REVIEW"
        )

        for entry in problem_columns[:15]:

            print(
                f" - {entry['column']}: "
                f"{entry['mismatches']} mismatches"
            )

            for example in entry["examples"][:2]:
                print(
                    "   Expected:",
                    example["expected"],
                    "| Actual:",
                    example["actual"]
                )

    print("\nSafety checks:")
    print("Model retraining: 0")
    print("Production predictions: DISABLED")
    print("Google Drive runtime dependency: NONE")

    print("\nReport:", REPORT)
    print("Column comparison:", DETAILS)

    if not passed:
        raise RuntimeError(
            "Historical feature reproduction differs. "
            "Review the saved report before proceeding."
        )

    print("\nSTEP 11B.3C COMPLETE — PASS")


if __name__ == "__main__":
    try:
        main()

    except Exception as exc:

        # Preserve a failure report if comparison
        # could not finish due to source/schema errors.

        if not REPORT.exists():

            write_report({
                "step": "11B.3C",
                "status": "EXECUTION_FAILED",
                "error": (
                    f"{type(exc).__name__}: {exc}"
                ),
                "production_predictions_enabled": False,
                "historical_reference_reproduction_passed":
                    False
            })

        traceback.print_exc()
        sys.exit(1)
