
import csv
import hashlib
import json
import re
import unicodedata
import urllib.parse
import urllib.request

from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path


ROOT = Path("/content/soccer_ai_hf_space")
STAGING = Path("/content/soccer_ai_live_staging")
REPORTS = STAGING / "reports"
RAW = STAGING / "free_secondary_snapshots"

# Officially published TheSportsDB free-development
# key. No account or personal key required.

PUBLIC_KEY = "123"

LEAGUES = {
    "eng.1": {
        "name": "Premier League",
        "sportsdb_id": "4328"
    },
    "esp.1": {
        "name": "La Liga",
        "sportsdb_id": "4335"
    }
}

FINISHED_STATUSES = {
    "match finished",
    "finished",
    "full time",
    "ft",
    "final"
}


def utc(value):
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


def normalized(value):
    text = unicodedata.normalize(
        "NFKC",
        str(value or "")
    ).casefold()

    text = "".join(
        ch if ch.isalnum() else " "
        for ch in text
    )

    return " ".join(text.split())


def score(value):
    if isinstance(value, bool):
        return None

    try:
        value = float(value)

        if value >= 0 and value.is_integer():
            return int(value)

    except (ValueError, TypeError):
        pass

    return None


def sha(data):
    return hashlib.sha256(data).hexdigest()


def save_json(path, obj):
    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    path.write_text(
        json.dumps(
            obj,
            indent=2,
            ensure_ascii=False,
            default=str
        ),
        encoding="utf-8"
    )


def save_csv(path, records, fields):
    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with path.open(
        "w",
        newline="",
        encoding="utf-8"
    ) as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=fields
        )
        writer.writeheader()
        writer.writerows(records)


def google_link(row, league_name):
    kickoff = utc(row["Kickoff_UTC"])

    day = (
        kickoff.date().isoformat()
        if kickoff
        else ""
    )

    query = (
        f'{row["Home_Team"]} vs '
        f'{row["Away_Team"]} '
        f'{league_name} {day} final score'
    )

    return (
        "https://www.google.com/search?q="
        + urllib.parse.quote_plus(query)
    )


def get_free_events(league_id, season):
    url = (
        "https://www.thesportsdb.com/"
        "api/v1/json/"
        + PUBLIC_KEY
        + "/eventsseason.php?"
        + urllib.parse.urlencode({
            "id": league_id,
            "s": season
        })
    )

    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "SoccerAIResearch/1.0"
        }
    )

    with urllib.request.urlopen(
        request,
        timeout=25
    ) as response:
        raw = response.read(5_000_001)

    if len(raw) > 5_000_000:
        raise RuntimeError(
            "Oversized second-source response."
        )

    data = json.loads(raw)

    if (
        not isinstance(data, dict)
        or not isinstance(
            data.get("events"), (list, type(None))
        )
    ):
        raise RuntimeError(
            "Unexpected free-source response format."
        )

    return data.get("events") or [], url, raw


def parse_event(event, expected_league):
    if not isinstance(event, dict):
        return None

    if str(
        event.get("idLeague", "")
    ) != expected_league:
        return None

    event_id = event.get("idEvent")

    if not event_id:
        return None

    # Some entries include a complete timestamp;
    # others include only date and time fields.

    kickoff = utc(
        event.get("strTimestamp")
    )

    precise_time = kickoff is not None

    if kickoff is None:
        date = event.get("dateEvent")
        time = (
            event.get("strTime")
            or "00:00:00"
        )

        kickoff = utc(
            str(date) + "T" + str(time)
            if date else None
        )

    if kickoff is None:
        return None

    return {
        "id": str(event_id),
        "league": expected_league,
        "home": str(
            event.get("strHomeTeam") or ""
        ),
        "away": str(
            event.get("strAwayTeam") or ""
        ),
        "home_score": score(
            event.get("intHomeScore")
        ),
        "away_score": score(
            event.get("intAwayScore")
        ),
        "kickoff": kickoff,
        "precise_time": precise_time,
        "finished": normalized(
            event.get("strStatus")
        ) in FINISHED_STATUSES,
        "raw_status": event.get("strStatus")
    }


def main():
    REPORTS.mkdir(
        parents=True,
        exist_ok=True
    )
    RAW.mkdir(
        parents=True,
        exist_ok=True
    )

    # -----------------------------------------------------------
    # 1. LOAD SUCCESSFUL STEP 12A.2A RESEARCH REVIEW
    # -----------------------------------------------------------

    manifests = sorted(
        REPORTS.glob("step12a2_manifest_*.json")
    )

    if not manifests:
        raise FileNotFoundError(
            "Step 12A.2A manifest not found."
        )

    manifest_path = manifests[-1]
    manifest = json.loads(
        manifest_path.read_text(
            encoding="utf-8"
        )
    )

    if (
        manifest.get("step") != "12A.2A"
        or manifest.get(
            "production_predictions_enabled"
        ) is not False
    ):
        raise RuntimeError(
            "Unexpected prior research manifest."
        )

    review_path = Path(
        manifest["review_csv"]
    ).resolve()

    if (
        REPORTS.resolve() not in review_path.parents
        or not review_path.is_file()
    ):
        raise RuntimeError(
            "Original review CSV is missing or unsafe."
        )

    with review_path.open(
        newline="",
        encoding="utf-8"
    ) as stream:
        records = list(
            csv.DictReader(stream)
        )

    if len(records) != manifest["primary_records"]:
        raise RuntimeError(
            "Prior review record count changed."
        )

    primary_manifest_path = Path(
        manifest["primary_manifest_path"]
    ).resolve()

    if (
        REPORTS.resolve()
            not in primary_manifest_path.parents
        or sha(
            primary_manifest_path.read_bytes()
        ) != manifest["primary_manifest_sha256"]
    ):
        raise RuntimeError(
            "Original research provenance changed."
        )

    primary_manifest = json.loads(
        primary_manifest_path.read_text(
            encoding="utf-8"
        )
    )

    snapshot = utc(
        primary_manifest["created_utc"]
    )

    if snapshot is None:
        raise RuntimeError(
            "Original snapshot timestamp missing."
        )

    season_start_year = (
        snapshot.year
        if snapshot.month >= 7
        else snapshot.year - 1
    )

    season = (
        f"{season_start_year}-"
        f"{season_start_year + 1}"
    )

    # -----------------------------------------------------------
    # 2. FETCH FREE RESEARCH DATA — TWO REQUESTS
    # -----------------------------------------------------------

    secondary = {}
    evidence = {}
    errors = {}

    stamp = datetime.now(
        timezone.utc
    ).strftime("%Y%m%d_%H%M%S_%f")

    for comp, info in LEAGUES.items():
        try:
            items, url, raw = get_free_events(
                info["sportsdb_id"],
                season
            )

            raw_file = RAW / (
                f"{comp.replace('.', '_')}_"
                f"{stamp}.json"
            )

            raw_file.write_bytes(raw)

            parsed = [
                parse_event(
                    item,
                    info["sportsdb_id"]
                )
                for item in items
            ]

            parsed = [
                item for item in parsed
                if (
                    item is not None
                    and item["kickoff"] <= snapshot
                )
            ]

            id_counts = Counter(
                item["id"]
                for item in parsed
            )

            duplicates = {
                key
                for key, n in id_counts.items()
                if n > 1
            }

            parsed = [
                item for item in parsed
                if item["id"] not in duplicates
            ]

            secondary[comp] = parsed

            evidence[comp] = {
                "source": "TheSportsDB",
                "url": url,
                "season": season,
                "raw_record_count": len(items),
                "eligible_record_count": len(parsed),
                "duplicate_ids": sorted(duplicates),
                "raw_sha256": sha(raw),
                "raw_file": str(raw_file),
                "retrieved_utc":
                    datetime.now(
                        timezone.utc
                    ).isoformat()
            }

        except Exception as exc:
            errors[comp] = (
                type(exc).__name__
                + ": "
                + str(exc)
            )
            secondary[comp] = []

    # -----------------------------------------------------------
    # 3. BUILD GOOGLE VERIFICATION QUEUE
    # -----------------------------------------------------------

    google_queue = []
    exact_pairs = defaultdict(list)
    candidates = {}

    for index, row in enumerate(records):

        competition = row.get(
            "Source_Competition_ID"
        )

        row["Free_Second_Source"] = ""
        row["Free_Second_Source_Match_ID"] = ""
        row["Free_Second_Source_Score"] = ""
        row["Free_Second_Source_URL"] = ""

        if competition not in LEAGUES:
            continue

        if row.get(
            "Source_Status"
        ) != "FINISHED":
            continue

        link = google_link(
            row,
            LEAGUES[competition]["name"]
        )

        google_queue.append({
            "Competition":
                LEAGUES[competition]["name"],
            "Primary_Match_ID":
                row["Source_Match_ID"],
            "Home_Team": row["Home_Team"],
            "Away_Team": row["Away_Team"],
            "Kickoff_UTC": row["Kickoff_UTC"],
            "Original_Home_Score":
                row["Home_Score"],
            "Original_Away_Score":
                row["Away_Score"],
            "Current_Status":
                row["Corroboration_Status"],
            "Google_Search_URL": link,
            "Reviewed_Source_URL": "",
            "Reviewed_Home_Score": "",
            "Reviewed_Away_Score": "",
            "Reviewer": "",
            "Reviewer_Decision": "PENDING"
        })

        # Only update the unverified original results.
        # Earlier Bundesliga decisions are untouched.

        if row["Corroboration_Status"] != (
            "AWAITING_PERMITTED_SECOND_SOURCE"
        ):
            continue

        primary_time = utc(
            row["Kickoff_UTC"]
        )

        if primary_time is None:
            continue

        exact = []

        for event in secondary[competition]:

            # Require precisely matching home/away
            # team names after harmless normalization.
            if (
                normalized(
                    event["home"]
                ) != normalized(
                    row["Home_Team"]
                )
                or normalized(
                    event["away"]
                ) != normalized(
                    row["Away_Team"]
                )
            ):
                continue

            # For a trusted timestamp: <=8 hours.
            # If precise time is unavailable:
            # same UTC date only.
            if event["precise_time"]:
                time_ok = (
                    abs(
                        event["kickoff"] - primary_time
                    ) <= timedelta(hours=8)
                )
            else:
                time_ok = (
                    event["kickoff"].date()
                    == primary_time.date()
                )

            if time_ok:
                exact.append(event)

        if len(exact) > 1:
            row["Corroboration_Status"] = (
                "QUARANTINED_AMBIGUOUS_MATCH"
            )

        elif len(exact) == 1:
            event = exact[0]

            exact_pairs[
                (competition, event["id"])
            ].append(index)

            candidates[index] = event

        # If there is no exact match, leave the
        # existing "AWAITING" status unchanged.

    # -----------------------------------------------------------
    # 4. APPLY UNIQUE MATCH AND SCORE VERIFICATION
    # -----------------------------------------------------------

    for index, event in candidates.items():

        row = records[index]
        competition = row[
            "Source_Competition_ID"
        ]

        row["Free_Second_Source"] = "TheSportsDB"

        row["Free_Second_Source_Match_ID"] = (
            event["id"]
        )

        row["Free_Second_Source_Score"] = (
            f"{event['home_score']}-"
            f"{event['away_score']}"
        )

        row["Free_Second_Source_URL"] = (
            evidence[competition]["url"]
        )

        if len(
            exact_pairs[
                (competition, event["id"])
            ]
        ) != 1:

            row["Corroboration_Status"] = (
                "QUARANTINED_AMBIGUOUS_MATCH"
            )

        elif (
            not event["finished"]
            or event["home_score"] is None
            or event["away_score"] is None
        ):

            row["Corroboration_Status"] = (
                "SECOND_SOURCE_FINAL_STATUS_REVIEW"
            )

        elif (
            score(row["Home_Score"])
            == event["home_score"]
            and score(row["Away_Score"])
            == event["away_score"]
        ):

            row["Corroboration_Status"] = (
                "SCORE_CORROBORATED_IDENTITY_PENDING"
            )

        else:

            row["Corroboration_Status"] = (
                "QUARANTINED_SCORE_CONFLICT"
            )

    # -----------------------------------------------------------
    # 5. VERIFY APPROVAL FLAGS ARE STILL DISABLED
    # -----------------------------------------------------------

    protected = (
        "Identity_Approved",
        "Feature_Update_Authorized",
        "Prediction_Authorized"
    )

    for row in records:
        for field in protected:
            if str(
                row.get(field, "")
            ).strip().lower() in (
                "true", "yes", "1"
            ):
                raise RuntimeError(
                    "Unexpected approval flag: "
                    + field
                )

    # -----------------------------------------------------------
    # 6. SAVE RESEARCH FILES
    # -----------------------------------------------------------

    combined_path = REPORTS / (
        f"step12a2b_free_review_{stamp}.csv"
    )

    google_path = REPORTS / (
        f"step12a2b_google_review_queue_{stamp}.csv"
    )

    manifest_output = REPORTS / (
        f"step12a2b_free_manifest_{stamp}.json"
    )

    save_csv(
        combined_path,
        records,
        list(records[0])
    )

    google_fields = [
        "Competition",
        "Primary_Match_ID",
        "Home_Team",
        "Away_Team",
        "Kickoff_UTC",
        "Original_Home_Score",
        "Original_Away_Score",
        "Current_Status",
        "Google_Search_URL",
        "Reviewed_Source_URL",
        "Reviewed_Home_Score",
        "Reviewed_Away_Score",
        "Reviewer",
        "Reviewer_Decision"
    ]

    save_csv(
        google_path,
        google_queue,
        google_fields
    )

    counts = dict(
        sorted(
            Counter(
                row["Corroboration_Status"]
                for row in records
            ).items()
        )
    )

    by_league = {
        comp: dict(
            Counter(
                row["Corroboration_Status"]
                for row in records
                if row["Source_Competition_ID"]
                    == comp
            )
        )
        for comp in ("eng.1", "esp.1", "bl1")
    }

    report = {
        "step": "12A.2B",
        "mode": "FREE_RESEARCH_AND_GOOGLE_REVIEW",
        "created_utc":
            datetime.now(
                timezone.utc
            ).isoformat(),
        "previous_manifest_sha256":
            sha(manifest_path.read_bytes()),
        "rows_reviewed": len(records),
        "google_review_links":
            len(google_queue),
        "secondary_source":
            "TheSportsDB free development",
        "secondary_evidence": evidence,
        "secondary_errors": errors,
        "status_counts": counts,
        "by_league": by_league,
        "google_results_scraped": False,
        "google_links_count_as_verification": False,
        "unresolved_results_remain_pending": True,
        "historical_data_modified": False,
        "elo_form_updated": False,
        "identity_approval_completed": False,
        "models_modified": False,
        "production_predictions_enabled": False,
        "commercial_data_rights_verified": False,
        "google_drive_runtime_dependency": False,
        "consolidated_review_csv":
            str(combined_path),
        "google_review_csv":
            str(google_path)
    }

    save_json(manifest_output, report)

    # -----------------------------------------------------------
    # 7. PRINT TEST OUTPUT
    # -----------------------------------------------------------

    print("=" * 70)
    print(
        "STEP 12A.2B — FREE DATA AND GOOGLE REVIEW"
    )
    print("=" * 70)

    print(
        "Original records retained:",
        len(records)
    )

    print(
        "Google search links generated:",
        len(google_queue)
    )

    for comp in LEAGUES:
        print(
            LEAGUES[comp]["name"],
            "free-source records:",
            len(secondary[comp])
        )

    print("\nCombined verification statuses:")

    for status, count in counts.items():
        print(f"  {status}: {count}")

    print("\nBy league:")

    for league, items in by_league.items():
        print(league, items)

    print(
        "\nSource errors:",
        errors or "NONE"
    )

    print(
        "Original Bundesliga reviews:",
        "PRESERVED"
    )

    print("Google scraped: NO")
    print("Google evidence automatically approved: NO")
    print("Historical data changed: NO")
    print("Elo/form updated: NO")
    print("Production predictions: DISABLED")

    print("\nGoogle queue:", google_path)
    print("Combined review:", combined_path)
    print("Manifest:", manifest_output)

    print(
        "\nSTEP 12A.2B RESEARCH STAGING COMPLETE."
    )

    print(
        "NOTE: Free secondary-source coverage "
        "may be incomplete."
    )


if __name__ == "__main__":
    main()
