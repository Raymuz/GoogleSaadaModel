
"""GoogleSaada V8 — scheduled research refresh and score gate.

Reads original provider code and canonical history from private staging.
Never changes deployed models, production history or prediction routes.
"""

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from huggingface_hub import hf_hub_download

SPACE = "raymuz/googlesaada-v8-staging"

SOURCE = [
    "backend/live_data_service.py",
    "app_ui/config/leagues.json",
    "deployment_files/feature_state/matches.parquet",
    "deployment_files/manifest.json",
]

VERIFIED_HISTORY = (
    "global_v3/v2g_staging/"
    "v2m_verified_world_history_1612e09be8891b3b.parquet"
)

EXPECTED_HISTORY_SHA256 = (
    "73fc5ede4ffe1c812d0a5982ccebb29ec8d0daf"
    "779ecd06bfa0f016666f4fd83"
)

TOKEN = os.environ["HF_TOKEN"]
REVISION = os.environ["GS_V8_STAGING_REVISION"]

OUTPUT = Path("v8_refresh_evidence").resolve()
OUTPUT.mkdir(parents=True, exist_ok=True)

def checksum(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def download(relative):
    return Path(hf_hub_download(
        repo_id=SPACE,
        repo_type="space",
        revision=REVISION,
        filename=relative,
        token=TOKEN,
    ))

def run():
    with tempfile.TemporaryDirectory(
        prefix="gs_v8_hourly_"
    ) as temporary:
        root = Path(temporary)

        for relative in SOURCE:
            original = download(relative)
            destination = root / relative
            destination.parent.mkdir(
                parents=True, exist_ok=True
            )
            destination.write_bytes(original.read_bytes())

        manifest = json.loads(
            (root / "deployment_files/manifest.json")
            .read_text(encoding="utf-8")
        )

        if manifest.get(
            "production_predictions_enabled"
        ) is not False:
            raise RuntimeError(
                "Prediction release lock changed"
            )

        history_path = download(VERIFIED_HISTORY)

        if checksum(history_path) != EXPECTED_HISTORY_SHA256:
            raise RuntimeError(
                "Verified model-history baseline changed"
            )

        history = pd.read_parquet(
            history_path,
            columns=[
                "id", "date_utc",
                "home_team_id", "away_team_id",
                "goals_home", "goals_away",
            ],
        )

        required_history = {
            "id", "date_utc",
            "home_team_id", "away_team_id",
            "goals_home", "goals_away",
        }

        if not required_history.issubset(history.columns):
            raise RuntimeError(
                "Model-history input schema changed"
            )

        if history["id"].astype(str).duplicated().any():
            raise RuntimeError(
                "Canonical history contains duplicate IDs"
            )

        # Execute the exact original provider in a child process.
        # Network access is bounded by the original provider.
        runner = root / "run_provider.py"
        runner.write_text("""
import importlib.util
import sys
from pathlib import Path

root = Path(sys.argv[1])
output = Path(sys.argv[2])

source = root / "backend/live_data_service.py"

spec = importlib.util.spec_from_file_location(
    "googlesaada_original_live_provider",
    source,
)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

module.MAX_HTTP_CALLS = min(
    int(module.MAX_HTTP_CALLS), 12
)
module.REQUEST_TIMEOUT = min(
    int(module.REQUEST_TIMEOUT), 15
)

module.run(root, output)
""", encoding="utf-8")

        provider_out = root / "provider_research"

        result = subprocess.run(
            [
                sys.executable,
                str(runner),
                str(root),
                str(provider_out),
            ],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )

        (OUTPUT / "provider_execution.log").write_text(
            result.stdout[-12000:]
            + "\\n"
            + result.stderr[-4000:],
            encoding="utf-8",
        )

        if result.returncode:
            raise RuntimeError(
                "Original provider failed: "
                + str(result.returncode)
            )

        csv_files = sorted(
            provider_out.rglob(
                "step12a1_provider_matches_*.csv"
            ),
            key=lambda x: x.stat().st_mtime,
            reverse=True,
        )

        if not csv_files:
            raise RuntimeError(
                "Original provider output missing"
            )

        rows = pd.read_csv(
            csv_files[0],
            dtype={
                "Source_Match_ID": str,
                "Source_Competition_ID": str,
                "Source_Home_Team_ID": str,
                "Source_Away_Team_ID": str,
            },
        )

        required = {
            "Source",
            "Source_Competition_ID",
            "Source_Match_ID",
            "Source_Home_Team_ID",
            "Source_Away_Team_ID",
            "Source_Status",
            "Score_Verification",
            "Identity_Approved",
            "Feature_Update_Authorized",
            "Kickoff_UTC",
        }

        if not required.issubset(rows.columns):
            raise RuntimeError(
                "Original provider output schema changed"
            )

        # Duplicate/conflict detection must be source-qualified.
        source_key = (
            rows["Source"].astype(str)
            + "|"
            + rows["Source_Competition_ID"].astype(str)
            + "|"
            + rows["Source_Match_ID"].astype(str)
        )

        duplicates = int(source_key.duplicated().sum())

        if duplicates:
            raise RuntimeError(
                f"Duplicate provider source identities: {duplicates}"
            )

        finished = rows.loc[
            rows["Source_Status"].astype(str).eq(
                "FINISHED"
            )
        ].copy()

        approved_flags = (
            finished["Score_Verification"]
            .astype(str).eq("VERIFIED")
            & finished["Identity_Approved"]
            .astype(str).str.lower().eq("true")
            & finished["Feature_Update_Authorized"]
            .astype(str).str.lower().eq("true")
        )

        # These three flags are necessary, but never sufficient:
        # each completed match also needs independent corroboration,
        # source-to-world ID proof, and canonical history deduplication.
        #
        # The currently available original research provider marks
        # finished scores UNVERIFIED_SINGLE_PROVIDER.
        #
        # No external approval/crosswalk authority has yet been
        # installed in the hosted release. Fail closed.

        eligible_for_approval_review = finished.loc[
            approved_flags
        ].copy()

        # All provider results remain quarantined until the
        # independent verification service is available.
        finished["Historical_Admission_Status"] = (
            "QUARANTINED_AWAITING_INDEPENDENT_EVIDENCE"
        )

        finished.to_csv(
            OUTPUT / "finished_scores_quarantine.csv",
            index=False,
        )

        rows.to_csv(
            OUTPUT / "provider_snapshot.csv",
            index=False,
        )

        now = pd.Timestamp.now(tz="UTC")
        kickoff = pd.to_datetime(
            rows["Kickoff_UTC"],
            utc=True, errors="coerce"
        )

        upcoming = rows.loc[
            rows["Source_Status"]
            .astype(str).isin(["SCHEDULED", "UPCOMING"])
            & kickoff.gt(now)
            & kickoff.le(now + pd.Timedelta(days=7))
        ].copy()

        upcoming.to_csv(
            OUTPUT / "upcoming_fixtures.csv",
            index=False,
        )

        report = {
            "run_utc": datetime.now(
                timezone.utc
            ).isoformat(),
            "source_revision": REVISION,
            "history_sha256": checksum(history_path),
            "history_rows": len(history),
            "provider_rows": len(rows),
            "upcoming_fixtures": len(upcoming),
            "finished_provider_rows": len(finished),
            "duplicate_provider_ids": duplicates,
            "flagged_for_independent_review": len(
                eligible_for_approval_review
            ),
            "approved_historical_appends": 0,
            "historical_admission_status": (
                "BLOCKED_PENDING_INDEPENDENT_VERIFICATION"
            ),
            "canonical_history_modified": False,
            "original_production_modified": False,
            "models_retrained": False,
        }

        (OUTPUT / "refresh_report.json").write_text(
            json.dumps(report, indent=2),
            encoding="utf-8",
        )

        print("=" * 68)
        print("GOOGLESAADA V8 — SCHEDULED REFRESH RESULT")
        print("=" * 68)
        for key, value in report.items():
            print(key, ":", value)
        print("=" * 68)

if __name__ == "__main__":
    run()
