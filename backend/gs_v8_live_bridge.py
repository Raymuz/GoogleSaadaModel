
"""V8 guarded research refresh and live-fixture inference.

No scored-history mutations. No source identity guessing.
No public prediction endpoint. Restartable background worker.
"""

import hmac
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from backend.gs_v8_verified_runtime import (
    predict_verified,
    PredictionAbstained,
)

ROOT = Path(__file__).resolve().parents[1]
PROVIDER = ROOT / "backend/live_data_service.py"

CACHE = Path("/tmp/googlesaada_v8_live_research")
CACHE.mkdir(parents=True, exist_ok=True)

SNAPSHOT = CACHE / "latest_fixtures.json"

_LOCK = threading.Lock()
_STARTED = False
_MAX_HTTP = 12
_REFRESH_INTERVAL = 3600

REQUIRED_PROVIDER = [
    "backend/live_data_service.py",
    "app_ui/config/leagues.json",
    "deployment_files/feature_state/matches.parquet",
    "deployment_files/manifest.json",
]

def safe_write(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(document, default=str, indent=2),
        encoding="utf-8"
    )
    os.replace(temporary, path)

def provider_fetch():
    # Only copy trusted immutable inputs to a temporary workspace.
    # Original model history is never an output of this function.
    with tempfile.TemporaryDirectory(prefix="gs_v8_refresh_") as tmp:
        work = Path(tmp)
        for relative in REQUIRED_PROVIDER:
            src = ROOT / relative
            if not src.is_file():
                raise RuntimeError(
                    "Original provider prerequisite missing: " + relative
                )
            dst = work / relative
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

        wrapper = work / "run_provider.py"
        wrapper.write_text("""
import importlib.util
import sys
from pathlib import Path

root = Path(sys.argv[1])
output = Path(sys.argv[2])
source = root / "backend/live_data_service.py"

spec = importlib.util.spec_from_file_location(
    "gs_original_provider_refresh", source
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

        output = work / "research"
        result = subprocess.run(
            [
                sys.executable,
                str(wrapper),
                str(work),
                str(output),
            ],
            cwd=str(work),
            timeout=240,
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode:
            raise RuntimeError(
                "Original provider refresh unsuccessful: "
                + (result.stdout + result.stderr)[-450:]
            )

        csvs = sorted(
            output.rglob("step12a1_provider_matches_*.csv"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        if not csvs:
            raise RuntimeError("Original provider research CSV absent")

        # Preserve source IDs as strings; do not turn IDs into floats.
        df = pd.read_csv(csvs[0], dtype={
            "Source_Match_ID": str,
            "Source_Competition_ID": str,
            "Source_Home_Team_ID": str,
            "Source_Away_Team_ID": str,
        })

        required = {
            "Source", "Source_Competition_ID",
            "Source_Match_ID", "Source_Home_Team_ID",
            "Source_Away_Team_ID", "Source_Status",
            "Kickoff_UTC",
        }
        if not required.issubset(df.columns):
            raise RuntimeError("Original provider schema changed")

        now = pd.Timestamp.now(tz="UTC")
        kickoff = pd.to_datetime(
            df["Kickoff_UTC"], utc=True, errors="coerce"
        )

        eligible = df.loc[
            df["Source"].astype(str).eq("ESPN")
            & df["Source_Status"].astype(str).eq("SCHEDULED")
            & kickoff.gt(now)
            & kickoff.le(now + pd.Timedelta(days=7))
        ]

        fixtures = {}
        for _, row in eligible.iterrows():
            comp = str(row["Source_Competition_ID"]).strip()
            match = str(row["Source_Match_ID"]).strip()
            home = str(row["Source_Home_Team_ID"]).strip()
            away = str(row["Source_Away_Team_ID"]).strip()

            if not (
                comp and match
                and re.fullmatch(r"\d{1,20}", home)
                and re.fullmatch(r"\d{1,20}", away)
                and home != away
            ):
                continue

            key = f"{comp}|{match}"

            record = {
                "fixture_key": key,
                "source": "ESPN",
                "competition": comp,
                "home_team_id": "ESPN:" + comp + ":" + home,
                "away_team_id": "ESPN:" + comp + ":" + away,
                "home_name": str(row.get("Home_Team", "")),
                "away_name": str(row.get("Away_Team", "")),
                "kickoff_utc": str(row["Kickoff_UTC"]),
            }

            if key in fixtures and fixtures[key] != record:
                raise RuntimeError(
                    "Conflicting source-qualified fixture: " + key
                )

            fixtures[key] = record

        return {
            "status": "RESEARCH_ONLY",
            "retrieved_utc": now.isoformat(),
            "provider_rows": len(df),
            "eligible_espn_fixtures": len(fixtures),
            "fixtures": fixtures,
            "verified_history_modified": False,
            "source_scores_approved": False,
        }

def refresh_once():
    if not _LOCK.acquire(blocking=False):
        return {"status": "REFRESH_IN_PROGRESS"}
    try:
        data = provider_fetch()
        safe_write(SNAPSHOT, data)
        return {
            "status": "REFRESH_COMPLETED",
            "retrieved_utc": data["retrieved_utc"],
            "provider_rows": data["provider_rows"],
            "eligible_espn_fixtures": data[
                "eligible_espn_fixtures"
            ],
            "approved_historical_appends": 0,
        }
    finally:
        _LOCK.release()

def snapshot():
    if not SNAPSHOT.is_file():
        raise PredictionAbstained(
            "Verified provider snapshot unavailable"
        )
    data = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    if data.get("status") != "RESEARCH_ONLY":
        raise PredictionAbstained("Invalid fixture evidence status")

    timestamp = pd.Timestamp(data["retrieved_utc"])
    if timestamp.tzinfo is None:
        raise PredictionAbstained("Untimed provider evidence")
    age = pd.Timestamp.now(tz="UTC") - timestamp.tz_convert("UTC")
    if not pd.Timedelta(0) <= age <= pd.Timedelta(minutes=90):
        raise PredictionAbstained("Provider snapshot is stale")
    return data

def fixtures_summary():
    data = snapshot()
    return {
        "status": "RESEARCH_ONLY",
        "retrieved_utc": data["retrieved_utc"],
        "eligible_espn_fixtures": data["eligible_espn_fixtures"],
        "fixtures": [
            {
                "fixture_key": f["fixture_key"],
                "home_name": f["home_name"],
                "away_name": f["away_name"],
                "kickoff_utc": f["kickoff_utc"],
            }
            for f in data["fixtures"].values()
        ],
    }

def forecast(fixture_key):
    # The requester cannot supply arbitrary model-team IDs.
    # The server selects a fixture from the provider snapshot.
    if not isinstance(fixture_key, str):
        raise PredictionAbstained("Invalid fixture key")
    data = snapshot()
    fixture = data["fixtures"].get(fixture_key)
    if fixture is None:
        raise PredictionAbstained("Fixture not provider-verified")

    kickoff = pd.Timestamp(fixture["kickoff_utc"])
    now = pd.Timestamp.now(tz="UTC")

    if kickoff.tzinfo is None or kickoff <= now:
        raise PredictionAbstained("Fixture kickoff invalid or elapsed")

    if kickoff > now + pd.Timedelta(days=7):
        raise PredictionAbstained("Fixture outside eligible window")

    # Runtime enforces real historical-feature availability,
    # trained model feature order and probability validity.
    result = predict_verified(
        home_team_id=fixture["home_team_id"],
        away_team_id=fixture["away_team_id"],
        kickoff_utc=kickoff.isoformat(),
        evidence_cutoff_utc=now.isoformat(),
        historical_test=False,
    )

    return {
        "fixture_key": fixture_key,
        "source": "ESPN",
        "home_name": fixture["home_name"],
        "away_name": fixture["away_name"],
        **result,
    }

def authorized(key):
    expected = os.getenv(
        "GOOGLESAADA_V8_INTERNAL_API_KEY", ""
    )
    return bool(
        expected and key
        and hmac.compare_digest(expected, key)
    )

def _worker():
    import time
    while True:
        try:
            summary = refresh_once()
            print(
                "[GS V8 LIVE REFRESH]",
                json.dumps(summary),
                flush=True,
            )
        except Exception as exc:
            print(
                "[GS V8 LIVE REFRESH BLOCKED]",
                type(exc).__name__,
                str(exc)[:220],
                flush=True,
            )
        time.sleep(_REFRESH_INTERVAL)

def start_refresh():
    global _STARTED
    if _STARTED:
        return
    _STARTED = True
    threading.Thread(
        target=_worker,
        daemon=True,
        name="gs-v8-source-research-refresh",
    ).start()
