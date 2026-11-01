
import argparse
import csv
import hashlib
import json
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd


COMPETITIONS = {
    "eng.1": {
        "league_id": "1",
        "name": "Premier League",
        "provider": "ESPN"
    },
    "esp.1": {
        "league_id": "5",
        "name": "La Liga",
        "provider": "ESPN"
    },
    "bl1": {
        "league_id": "11",
        "name": "Bundesliga",
        "provider": "OpenLigaDB"
    }
}

CACHE_SECONDS = 3 * 60 * 60
REQUEST_TIMEOUT = 20
MAX_HTTP_CALLS = 18


def utc_now():
    return datetime.now(timezone.utc)


def parse_utc(value):
    if not value:
        return None

    try:
        result = datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )

        if result.tzinfo is None:
            result = result.replace(
                tzinfo=timezone.utc
            )

        return result.astimezone(timezone.utc)

    except (ValueError, TypeError):
        return None


def int_score(value):
    if isinstance(value, bool):
        return None

    try:
        number = float(value)

        if (
            number >= 0
            and number.is_integer()
        ):
            return int(number)

    except (TypeError, ValueError):
        pass

    return None


def sha256_bytes(value):
    return hashlib.sha256(
        value
    ).hexdigest()


def json_write(path, payload):
    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    tmp = path.with_suffix(
        path.suffix + ".tmp"
    )

    tmp.write_text(
        json.dumps(
            payload,
            indent=2,
            ensure_ascii=False,
            default=str
        ),
        encoding="utf-8"
    )

    tmp.replace(path)


def month_codes(start, end):
    result = []
    year = start.year
    month = start.month

    while (
        (year, month)
        <= (end.year, end.month)
    ):
        result.append(
            f"{year:04d}{month:02d}"
        )

        month += 1

        if month == 13:
            year += 1
            month = 1

    return result


class ProviderClient:

    def __init__(self, cache_directory):
        self.cache = cache_directory
        self.cache.mkdir(
            parents=True,
            exist_ok=True
        )

        self.http_calls = 0
        self.cache_hits = 0
        self.errors = []
        self.evidence = []

    def get_json(self, url):

        key = sha256_bytes(
            url.encode("utf-8")
        )

        path = self.cache / (key + ".json")

        if path.exists():
            age = (
                utc_now().timestamp()
                - path.stat().st_mtime
            )

            if 0 <= age < CACHE_SECONDS:
                raw = path.read_bytes()

                try:
                    payload = json.loads(raw)
                    self.cache_hits += 1

                    self.evidence.append({
                        "url": url,
                        "sha256": sha256_bytes(raw),
                        "retrieval": "CACHE",
                        "cache_age_seconds": round(age)
                    })

                    return payload

                except json.JSONDecodeError:
                    pass

        if self.http_calls >= MAX_HTTP_CALLS:
            raise RuntimeError(
                "HTTP request budget exceeded."
            )

        self.http_calls += 1

        request = urllib.request.Request(
            url,
            headers={
                "User-Agent":
                    "SoccerAIResearch/1.0",
                "Accept": "application/json"
            }
        )

        with urllib.request.urlopen(
            request,
            timeout=REQUEST_TIMEOUT
        ) as response:
            raw = response.read(
                8_000_001
            )

        if len(raw) > 8_000_000:
            raise RuntimeError(
                "Provider response size limit exceeded."
            )

        payload = json.loads(raw)

        # Cache the exact response payload locally.
        path.write_bytes(raw)

        self.evidence.append({
            "url": url,
            "sha256": sha256_bytes(raw),
            "retrieval": "HTTP",
            "retrieved_utc": utc_now().isoformat()
        })

        return payload


def base_record(
    source, comp_id, match_id,
    kickoff, home_id, away_id,
    home_name, away_name, status,
    home_score=None, away_score=None
):

    return {
        "Source": source,
        "Source_Competition_ID": comp_id,
        "Source_Match_ID": str(match_id),
        "Kickoff_UTC": kickoff.isoformat(),
        "Source_Home_Team_ID": str(home_id),
        "Source_Away_Team_ID": str(away_id),
        "Home_Team": str(home_name),
        "Away_Team": str(away_name),
        "Source_Status": status,
        "Home_Score": home_score,
        "Away_Score": away_score,

        # Never treat one provider as independent
        # corroboration of its own result.
        "Score_Verification":
            "UNVERIFIED_SINGLE_PROVIDER"
            if status == "FINISHED"
            else "NOT_APPLICABLE",

        "Identity_Approved": False,
        "Feature_Update_Authorized": False,
        "Prediction_Authorized": False
    }


def parse_espn(payload, competition_id):

    events = payload.get("events", [])

    if not isinstance(events, list):
        raise ValueError(
            "ESPN events must be a list."
        )

    records = []

    for event in events:

        if not isinstance(event, dict):
            continue

        event_id = event.get("id")
        kickoff = parse_utc(
            event.get("date")
        )

        competitions = event.get(
            "competitions"
        ) or []

        if (
            not event_id
            or kickoff is None
            or not competitions
        ):
            continue

        competitors = competitions[0].get(
            "competitors"
        ) or []

        sides = {
            entry.get("homeAway"): entry
            for entry in competitors
            if isinstance(entry, dict)
        }

        if not {
            "home", "away"
        }.issubset(sides):
            continue

        home = sides["home"]
        away = sides["away"]

        home_team = home.get("team") or {}
        away_team = away.get("team") or {}

        home_id = home_team.get("id")
        away_id = away_team.get("id")

        if not home_id or not away_id:
            continue

        status_type = (
            event.get("status", {})
            .get("type", {})
        )

        completed = (
            status_type.get("completed")
            is True
        )

        state = str(
            status_type.get("state", "")
        ).lower()

        if completed:
            status = "FINISHED"

        elif state == "in":
            status = "LIVE"

        elif state == "pre":
            status = "SCHEDULED"

        else:
            status = "UNKNOWN"

        home_score = None
        away_score = None

        if completed:
            home_score = int_score(
                home.get("score")
            )

            away_score = int_score(
                away.get("score")
            )

            if (
                home_score is None
                or away_score is None
            ):
                status = "FINAL_SCORE_REVIEW"

        records.append(
            base_record(
                "ESPN",
                competition_id,
                event_id,
                kickoff,
                home_id,
                away_id,
                home_team.get(
                    "displayName", ""
                ),
                away_team.get(
                    "displayName", ""
                ),
                status,
                home_score,
                away_score
            )
        )

    return records


def parse_openligadb(payload):

    if not isinstance(payload, list):
        raise ValueError(
            "OpenLigaDB response must be a list."
        )

    records = []

    for match in payload:

        if not isinstance(match, dict):
            continue

        match_id = match.get(
            "matchID",
            match.get("matchId")
        )

        kickoff = parse_utc(
            match.get("matchDateTimeUTC")
        )

        home = match.get("team1") or {}
        away = match.get("team2") or {}

        home_id = home.get("teamId")
        away_id = away.get("teamId")

        if (
            not match_id
            or kickoff is None
            or not home_id
            or not away_id
        ):
            continue

        finished = (
            match.get("matchIsFinished")
            is True
        )

        status = (
            "FINISHED"
            if finished
            else "UNKNOWN_NOT_FINISHED"
        )

        home_score = None
        away_score = None

        if finished:

            full_time = [
                value
                for value in (
                    match.get("matchResults")
                    or []
                )
                if (
                    isinstance(value, dict)
                    and (
                        value.get("resultTypeID")
                        == 2
                        or value.get("resultTypeId")
                        == 2
                    )
                )
            ]

            # Do not assume the final result
            # is simply the last array element.

            if len(full_time) == 1:

                result = full_time[0]

                home_score = int_score(
                    result.get("pointsTeam1")
                )

                away_score = int_score(
                    result.get("pointsTeam2")
                )

            if (
                home_score is None
                or away_score is None
            ):
                status = "FINAL_SCORE_REVIEW"

        records.append(
            base_record(
                "OpenLigaDB",
                "bl1",
                match_id,
                kickoff,
                home_id,
                away_id,
                home.get("teamName", ""),
                away.get("teamName", ""),
                status,
                home_score,
                away_score
            )
        )

    return records


def run(root, output):

    config_path = (
        root / "app_ui/config/leagues.json"
    )

    history_path = (
        root
        / "deployment_files/feature_state"
        / "matches.parquet"
    )

    model_manifest_path = (
        root / "deployment_files/manifest.json"
    )

    for path in (
        config_path,
        history_path,
        model_manifest_path
    ):
        if not path.is_file():
            raise FileNotFoundError(path)

    model_manifest = json.loads(
        model_manifest_path.read_text(
            encoding="utf-8"
        )
    )

    if model_manifest.get(
        "production_predictions_enabled"
    ) is not False:
        raise RuntimeError(
            "Production must remain disabled."
        )

    league_config = json.loads(
        config_path.read_text(
            encoding="utf-8"
        )
    )

    options = {
        str(entry["value"]): entry
        for entry in league_config["leagues"]
    }

    for competition_id, info in COMPETITIONS.items():

        entry = options.get(
            info["league_id"]
        )

        if not entry:
            raise RuntimeError(
                "Expected league missing: "
                + competition_id
            )

        if not (
            entry.get(
                "fixture_discovery_available"
            ) is True
            and entry.get(
                "source_competition_id"
            ) == competition_id
        ):
            raise RuntimeError(
                "Original Step 10D league integration "
                "differs: " + competition_id
            )

    history = pd.read_parquet(
        history_path,
        columns=[
            "date_utc",
            "is_played"
        ]
    )

    played = (
        history["is_played"]
        .astype("string")
        .str.lower()
        .isin(["true", "1", "yes"])
    )

    dates = pd.to_datetime(
        history["date_utc"],
        utc=True,
        errors="coerce"
    )

    latest = dates.loc[played].max()

    if pd.isna(latest):
        raise RuntimeError(
            "No completed historical result date."
        )

    now = utc_now()

    if latest.to_pydatetime() > now:
        raise RuntimeError(
            "Historical completed match is future-dated."
        )

    start = (
        latest.to_pydatetime()
        + timedelta(seconds=1)
    )

    end = now + timedelta(days=7)

    # Prevent accidental unbounded scraping.
    if (end - start).days > 184:
        raise RuntimeError(
            "Historical gap exceeds 184 days. "
            "Backfill in bounded research windows."
        )

    cache = output / "provider_cache"
    client = ProviderClient(cache)

    records = []

    # ESPN: fetch by calendar MONTH and then
    # filter each event to the requested period.
    # This avoids assuming date-range query support.

    months = month_codes(start, end)

    for competition in ("eng.1", "esp.1"):

        for month in months:

            url = (
                "https://site.api.espn.com/"
                "apis/site/v2/sports/soccer/"
                + competition
                + "/scoreboard?dates="
                + month
                + "&limit=500"
            )

            try:
                payload = client.get_json(url)

                records.extend(
                    parse_espn(
                        payload,
                        competition
                    )
                )

            except Exception as exc:
                client.errors.append({
                    "source": competition,
                    "period": month,
                    "error": (
                        type(exc).__name__
                        + ": "
                        + str(exc)
                    )
                })

    # Bundesliga: season-based requests.
    # One season may overlap the start/end window.

    seasons = sorted({
        date.year
        - (1 if date.month < 7 else 0)
        for date in (start, end)
    } | {
        start.year - 1
    })

    for season in seasons:

        url = (
            "https://api.openligadb.de/"
            "getmatchdata/bl1/"
            + str(season)
        )

        try:
            payload = client.get_json(url)

            records.extend(
                parse_openligadb(payload)
            )

        except Exception as exc:
            client.errors.append({
                "source": "bl1",
                "period": str(season),
                "error": (
                    type(exc).__name__
                    + ": "
                    + str(exc)
                )
            })

    # Filter the returned provider records.
    filtered = []

    for record in records:

        kickoff = parse_utc(
            record["Kickoff_UTC"]
        )

        if kickoff is None:
            continue

        if not (
            start <= kickoff <= end
        ):
            continue

        if (
            record["Source_Status"]
            == "FINISHED"
            and kickoff > now
        ):
            record["Source_Status"] = (
                "FUTURE_FINAL_CONFLICT"
            )

            record["Score_Verification"] = (
                "QUARANTINED"
            )

        filtered.append(record)

    # Fail closed on conflicting IDs from the
    # same source and competition.

    unique = {}
    conflicts = []

    for record in filtered:

        key = (
            record["Source"],
            record["Source_Competition_ID"],
            record["Source_Match_ID"]
        )

        if key in unique:

            if unique[key] != record:
                conflicts.append({
                    "key": key,
                    "previous": unique[key],
                    "incoming": record
                })

            continue

        unique[key] = record

    final_records = list(
        unique.values()
    )

    if conflicts:
        # Do not silently stage conflicting
        # records as clean match observations.
        bad_keys = {
            tuple(item["key"])
            for item in conflicts
        }

        final_records = [
            record for record in final_records
            if (
                record["Source"],
                record["Source_Competition_ID"],
                record["Source_Match_ID"]
            ) not in bad_keys
        ]

    final_records.sort(
        key=lambda item: (
            item["Kickoff_UTC"],
            item["Source"],
            item["Source_Match_ID"]
        )
    )

    output.mkdir(
        parents=True,
        exist_ok=True
    )

    reports = output / "reports"
    reports.mkdir(
        parents=True,
        exist_ok=True
    )

    stamp = now.strftime(
        "%Y%m%d_%H%M%S_%f"
    )

    rows_path = (
        reports
        / f"step12a1_provider_matches_{stamp}.csv"
    )

    fields = [
        "Source",
        "Source_Competition_ID",
        "Source_Match_ID",
        "Kickoff_UTC",
        "Source_Home_Team_ID",
        "Source_Away_Team_ID",
        "Home_Team",
        "Away_Team",
        "Source_Status",
        "Home_Score",
        "Away_Score",
        "Score_Verification",
        "Identity_Approved",
        "Feature_Update_Authorized",
        "Prediction_Authorized"
    ]

    with rows_path.open(
        "w",
        newline="",
        encoding="utf-8"
    ) as stream:

        writer = csv.DictWriter(
            stream,
            fieldnames=fields
        )

        writer.writeheader()
        writer.writerows(final_records)

    by_status = {}

    for record in final_records:

        status = record["Source_Status"]

        by_status[status] = (
            by_status.get(status, 0) + 1
        )

    manifest = {
        "step": "12A.1",

        "created_utc": now.isoformat(),

        "historical_latest_completed_utc":
            latest.isoformat(),

        "research_collection_start_utc":
            start.isoformat(),

        "research_collection_end_utc":
            end.isoformat(),

        "historical_gap_fully_closed": False,

        "provider_integrations": list(
            COMPETITIONS
        ),

        "records_staged": len(final_records),

        "records_by_source_status":
            by_status,

        "provider_http_calls":
            client.http_calls,

        "provider_cache_hits":
            client.cache_hits,

        "provider_errors":
            client.errors,

        "source_evidence":
            client.evidence,

        "source_identity_conflicts":
            conflicts,

        "score_corroboration_completed": False,

        "team_identity_approval_completed": False,

        "historical_matches_modified": False,

        "elo_form_updated": False,

        "models_modified": False,

        "gpt_api_calls": 0,

        "production_predictions_enabled": False,

        "google_drive_runtime_dependency": False,

        "research_rows_file": str(
            rows_path
        )
    }

    manifest_path = (
        reports
        / f"step12a1_manifest_{stamp}.json"
    )

    json_write(
        manifest_path,
        manifest
    )

    print("=" * 70)
    print(
        "STEP 12A.1 — LIVE DATA & GAP AUDIT"
    )
    print("=" * 70)

    print(
        "Historical latest completed:",
        latest.isoformat()
    )

    print(
        "Research window:",
        start.isoformat(),
        "to",
        end.isoformat()
    )

    print(
        "Provider records staged:",
        len(final_records)
    )

    print(
        "Source statuses:",
        by_status
    )

    print(
        "HTTP calls:",
        client.http_calls
    )

    print(
        "Cache hits:",
        client.cache_hits
    )

    print(
        "Provider errors:",
        len(client.errors)
    )

    for error in client.errors:
        print("PROVIDER ERROR:", error)

    print(
        "Conflicting source IDs:",
        len(conflicts)
    )

    print(
        "Score verification:",
        "PENDING"
    )

    print(
        "Identity approval:",
        "PENDING"
    )

    print(
        "Historical feature updates:",
        "NONE"
    )

    print(
        "Production predictions:",
        "DISABLED"
    )

    print(
        "Google Drive dependency:",
        "NONE"
    )

    print(
        "Research CSV:",
        rows_path
    )

    print(
        "Manifest:",
        manifest_path
    )

    if not client.evidence:
        raise RuntimeError(
            "No valid provider response was "
            "collected or retrieved from cache."
        )

    if not final_records:
        raise RuntimeError(
            "No usable match records were parsed. "
            "Review the provider errors and evidence."
        )

    print(
        "\nSTEP 12A.1 COMPLETE — "
        "RESEARCH STAGING ONLY."
    )


if __name__ == "__main__":

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--root",
        type=Path,
        required=True
    )

    parser.add_argument(
        "--output",
        type=Path,
        required=True
    )

    args = parser.parse_args()

    run(
        args.root.resolve(),
        args.output.resolve()
    )
