
import csv
import json
import hashlib
import os
import urllib.request
import urllib.parse
import urllib.error
import unicodedata
import difflib

from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = Path(
    "/content/soccer_ai_live_staging/reports"
).resolve()

CACHE = Path(
    "/content/soccer_ai_live_staging/secondary_source_cache"
)

SOURCE_MAP = {
    "eng.1": "PL",
    "esp.1": "PD"
}

TOKEN = os.environ.get(
    "SOCCER_FOOTBALL_DATA_TOKEN", ""
).strip()


# ---------------------------------------------------------------
# 1. VALIDATION HELPERS
# ---------------------------------------------------------------

def read_json(path):
    return json.loads(
        path.read_text(encoding="utf-8")
    )


def digest(path):
    return hashlib.sha256(
        path.read_bytes()
    ).hexdigest()


def parse_time(value):
    try:
        dt = datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(timezone.utc)

    except (TypeError, ValueError):
        return None


def norm(value):
    value = unicodedata.normalize(
        "NFKC", str(value or "")
    ).casefold()

    cleaned = "".join(
        character
        if character.isalnum()
        else " "
        for character in value
    )

    return " ".join(cleaned.split())


def goal(value):
    if isinstance(value, bool):
        return None

    try:
        number = float(value)

        if number.is_integer() and number >= 0:
            return int(number)

    except (TypeError, ValueError):
        pass

    return None


def ensure_report(path):
    path = Path(path).resolve()

    if (
        REPORTS not in path.parents
        or not path.is_file()
    ):
        raise RuntimeError(
            "Original review artifact missing "
            "or outside staging reports."
        )

    return path


def write_json(path, obj):
    path.write_text(
        json.dumps(
            obj,
            indent=2,
            ensure_ascii=False,
            default=str
        ),
        encoding="utf-8"
    )


# ---------------------------------------------------------------
# 2. GET AUTHORIZED INDEPENDENT SOURCE RESULTS
# ---------------------------------------------------------------

def retrieve(code, start_date, end_date):

    url = (
        "https://api.football-data.org/"
        "v4/competitions/"
        + code
        + "/matches?"
        + urllib.parse.urlencode({
            "dateFrom": start_date,
            "dateTo": end_date,
            "status": "FINISHED"
        })
    )

    # Token is transmitted in the header.
    # It is not saved inside the service source or URL.

    request = urllib.request.Request(
        url,
        headers={
            "X-Auth-Token": TOKEN,
            "Accept": "application/json",
            "User-Agent": "SoccerAIResearch/1.0"
        }
    )

    try:
        with urllib.request.urlopen(
            request, timeout=30
        ) as response:
            raw = response.read(5_000_001)

    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"Football-Data.org {code}: "
            f"HTTP {exc.code}. "
            "Check API access and quota."
        ) from None

    if len(raw) > 5_000_000:
        raise RuntimeError(
            f"{code}: oversized provider response."
        )

    payload = json.loads(raw)

    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("matches"), list)
    ):
        raise RuntimeError(
            f"{code}: unsupported API response."
        )

    competition = payload.get("competition") or {}

    if competition.get("code") not in (None, code):
        raise RuntimeError(
            f"{code}: competition mismatch."
        )

    result_set = payload.get("resultSet") or {}

    if (
        result_set.get("count") is not None
        and int(result_set["count"])
        != len(payload["matches"])
    ):
        raise RuntimeError(
            f"{code}: incomplete match result set."
        )

    CACHE.mkdir(parents=True, exist_ok=True)

    cache_path = CACHE / (
        f"football_data_{code}_"
        + datetime.now(
            timezone.utc
        ).strftime("%Y%m%d_%H%M%S_%f")
        + ".json"
    )

    cache_path.write_bytes(raw)

    evidence = {
        "source": "football-data.org",
        "url": url,
        "sha256": digest(cache_path),
        "raw_snapshot": str(cache_path),
        "response_count": len(payload["matches"]),
        "retrieved_utc":
            datetime.now(timezone.utc).isoformat()
    }

    return payload["matches"], evidence


# ---------------------------------------------------------------
# 3. LOAD VERIFIED PREVIOUS STAGING
# ---------------------------------------------------------------

def main():

    if not TOKEN:
        raise RuntimeError(
            "Football-Data.org API token missing."
        )

    manifests = sorted(
        REPORTS.glob("step12a2_manifest_*.json")
    )

    if not manifests:
        raise RuntimeError(
            "Successful Step 12A.2A results required."
        )

    prior_path = manifests[-1]
    prior = read_json(prior_path)

    if (
        prior.get("step") != "12A.2A"
        or prior.get(
            "production_predictions_enabled"
        ) is not False
    ):
        raise RuntimeError(
            "Unexpected Step 12A.2A manifest."
        )

    review_path = ensure_report(
        prior["review_csv"]
    )

    with review_path.open(
        newline="",
        encoding="utf-8"
    ) as stream:
        rows = list(csv.DictReader(stream))

    if (
        not rows
        or len(rows) != prior["primary_records"]
    ):
        raise RuntimeError(
            "Original staged match count changed."
        )

    primary_manifest_path = ensure_report(
        prior["primary_manifest_path"]
    )

    if (
        digest(primary_manifest_path)
        != prior["primary_manifest_sha256"]
    ):
        raise RuntimeError(
            "Primary manifest integrity mismatch."
        )

    primary = read_json(primary_manifest_path)

    if len(rows) != primary["records_staged"]:
        raise RuntimeError(
            "Primary record count mismatch."
        )

    match_ids = {
        (
            row["Source"],
            row["Source_Competition_ID"],
            row["Source_Match_ID"]
        )
        for row in rows
    }

    if len(match_ids) != len(rows):
        raise RuntimeError(
            "Duplicate primary match identifiers."
        )

    protected = (
        "Identity_Approved",
        "Feature_Update_Authorized",
        "Prediction_Authorized"
    )

    for row in rows:
        for field in protected:
            if str(
                row.get(field, "")
            ).strip().lower() in (
                "true", "1", "yes"
            ):
                raise RuntimeError(
                    "Unapproved production authorization found."
                )

    snapshot = parse_time(
        primary["created_utc"]
    )

    start = parse_time(
        primary["research_collection_start_utc"]
    )

    if (
        snapshot is None
        or start is None
        or snapshot < start
    ):
        raise RuntimeError(
            "Invalid historical snapshot chronology."
        )

    # -----------------------------------------------------------
    # 4. FETCH BOTH COMPETITIONS
    # -----------------------------------------------------------

    fetched = {}
    evidence = {}
    errors = {}

    for source_competition, code in SOURCE_MAP.items():

        try:
            matches, source_evidence = retrieve(
                code,
                start.date().isoformat(),
                snapshot.date().isoformat()
            )

            accepted = []

            for match in matches:

                if (
                    not isinstance(match, dict)
                    or match.get("status") != "FINISHED"
                ):
                    continue

                kickoff = parse_time(
                    match.get("utcDate")
                )

                if (
                    kickoff is None
                    or not start <= kickoff <= snapshot
                ):
                    continue

                score = match.get("score") or {}
                full = score.get("fullTime") or {}

                if score.get("duration") not in (
                    None, "REGULAR"
                ):
                    continue

                home = match.get("homeTeam") or {}
                away = match.get("awayTeam") or {}

                if not all((
                    match.get("id"),
                    home.get("id"),
                    away.get("id")
                )):
                    continue

                home_goals = goal(
                    full.get("home")
                )

                away_goals = goal(
                    full.get("away")
                )

                if (
                    home_goals is None
                    or away_goals is None
                ):
                    continue

                accepted.append({
                    "id": str(match["id"]),
                    "utc": kickoff,
                    "home": (
                        home.get("name")
                        or home.get("shortName")
                        or ""
                    ),
                    "away": (
                        away.get("name")
                        or away.get("shortName")
                        or ""
                    ),
                    "home_id": str(home["id"]),
                    "away_id": str(away["id"]),
                    "home_goals": home_goals,
                    "away_goals": away_goals,
                    "evidence_url":
                        source_evidence["url"]
                })

            counts = Counter(
                item["id"]
                for item in accepted
            )

            duplicates = {
                identity
                for identity, number in counts.items()
                if number > 1
            }

            fetched[source_competition] = [
                item
                for item in accepted
                if item["id"] not in duplicates
            ]

            evidence[source_competition] = {
                **source_evidence,
                "eligible_finished_records":
                    len(fetched[source_competition]),
                "duplicate_secondary_ids":
                    sorted(duplicates)
            }

        except Exception as exc:
            errors[source_competition] = (
                f"{type(exc).__name__}: {exc}"
            )
            fetched[source_competition] = []

    # -----------------------------------------------------------
    # 5. STRICT IDENTITY AND SCORE COMPARISON
    # -----------------------------------------------------------

    proposals = []
    pending_links = defaultdict(list)
    matched = {}

    for index, row in enumerate(rows):

        row.update({
            "PL_LaLiga_Second_Source": "",
            "PL_LaLiga_Secondary_Match_ID": "",
            "PL_LaLiga_Secondary_Score": "",
            "PL_LaLiga_Evidence_URL": ""
        })

        competition = row.get(
            "Source_Competition_ID"
        )

        if (
            competition not in SOURCE_MAP
            or row.get("Source_Status") != "FINISHED"
        ):
            continue

        # Preserve the earlier pending classification
        # if the second source failed.

        if (
            competition in errors
            or not fetched.get(competition)
        ):
            continue

        kickoff = parse_time(
            row["Kickoff_UTC"]
        )

        if (
            kickoff is None
            or kickoff > snapshot
            or goal(row.get("Home_Score")) is None
            or goal(row.get("Away_Score")) is None
        ):
            row["Corroboration_Status"] = (
                "QUARANTINED_BAD_PRIMARY_RESULT"
            )
            continue

        nearby = [
            item
            for item in fetched[competition]
            if abs(
                item["utc"] - kickoff
            ) <= timedelta(hours=24)
        ]

        # No aliases or fuzzy matches are automatically
        # considered an approved identity.

        exact = [
            item
            for item in nearby

            if (
                abs(item["utc"] - kickoff)
                <= timedelta(hours=8)

                and norm(item["home"])
                == norm(row["Home_Team"])

                and norm(item["away"])
                == norm(row["Away_Team"])
            )
        ]

        if len(exact) > 1:

            row["Corroboration_Status"] = (
                "QUARANTINED_AMBIGUOUS_MATCH"
            )

        elif len(exact) == 1:

            item = exact[0]

            pending_links[
                (competition, item["id"])
            ].append(index)

            matched[index] = item

        else:

            suggestions = []

            for item in nearby:

                home_similarity = (
                    difflib.SequenceMatcher(
                        None,
                        norm(row["Home_Team"]),
                        norm(item["home"])
                    ).ratio()
                )

                away_similarity = (
                    difflib.SequenceMatcher(
                        None,
                        norm(row["Away_Team"]),
                        norm(item["away"])
                    ).ratio()
                )

                if min(
                    home_similarity,
                    away_similarity
                ) >= 0.50:

                    suggestions.append((
                        min(
                            home_similarity,
                            away_similarity
                        ),
                        (
                            home_similarity
                            + away_similarity
                        ) / 2,
                        item
                    ))

            suggestions.sort(
                key=lambda value: (
                    -value[0],
                    -value[1],
                    value[2]["id"]
                )
            )

            if suggestions:

                row["Corroboration_Status"] = (
                    "TEAM_NAME_MAPPING_REVIEW"
                )

                candidate = suggestions[0][2]

                proposals.append({
                    "Competition": competition,

                    "Primary_Source_ID":
                        row["Source_Match_ID"],

                    "Primary_Home":
                        row["Home_Team"],

                    "Primary_Away":
                        row["Away_Team"],

                    "Second_Source_ID":
                        candidate["id"],

                    "Second_Home":
                        candidate["home"],

                    "Second_Away":
                        candidate["away"],

                    "Primary_Score":
                        f"{row['Home_Score']}-"
                        f"{row['Away_Score']}",

                    "Second_Score":
                        f"{candidate['home_goals']}-"
                        f"{candidate['away_goals']}",

                    "Name_Similarity":
                        round(
                            suggestions[0][1],
                            4
                        ),

                    "Identity_Approved": False,
                    "Score_Verified": False
                })

            else:

                row["Corroboration_Status"] = (
                    "NO_EXACT_SECOND_SOURCE_MATCH"
                )

    # -----------------------------------------------------------
    # 6. APPLY UNIQUE PAIRING AND SCORE COMPARISON
    # -----------------------------------------------------------

    for index, item in matched.items():

        row = rows[index]
        competition = row["Source_Competition_ID"]

        row.update({
            "PL_LaLiga_Second_Source":
                "football-data.org",

            "PL_LaLiga_Secondary_Match_ID":
                item["id"],

            "PL_LaLiga_Secondary_Score":
                f"{item['home_goals']}-"
                f"{item['away_goals']}",

            "PL_LaLiga_Evidence_URL":
                item["evidence_url"]
        })

        if len(
            pending_links[
                (competition, item["id"])
            ]
        ) != 1:

            row["Corroboration_Status"] = (
                "QUARANTINED_AMBIGUOUS_MATCH"
            )

        elif (
            goal(row["Home_Score"])
            == item["home_goals"]

            and goal(row["Away_Score"])
            == item["away_goals"]
        ):

            row["Corroboration_Status"] = (
                "SCORE_CORROBORATED_IDENTITY_PENDING"
            )

        else:

            row["Corroboration_Status"] = (
                "QUARANTINED_SCORE_CONFLICT"
            )

    # -----------------------------------------------------------
    # 7. PRESERVE ALL RESEARCH SAFETY FLAGS
    # -----------------------------------------------------------

    for row in rows:

        for field in protected:

            if str(
                row.get(field, "")
            ).lower() in ("true", "1", "yes"):

                raise RuntimeError(
                    "An identity or production approval "
                    "was unexpectedly enabled."
                )

    # -----------------------------------------------------------
    # 8. SAVE CONSOLIDATED RESEARCH OUTPUT
    # -----------------------------------------------------------

    REPORTS.mkdir(
        parents=True,
        exist_ok=True
    )

    stamp = datetime.now(
        timezone.utc
    ).strftime("%Y%m%d_%H%M%S_%f")

    combined = REPORTS / (
        f"step12a2b_consolidated_review_{stamp}.csv"
    )

    proposal_file = REPORTS / (
        f"step12a2b_name_proposals_{stamp}.csv"
    )

    manifest_file = REPORTS / (
        f"step12a2b_manifest_{stamp}.json"
    )

    with combined.open(
        "w",
        newline="",
        encoding="utf-8"
    ) as stream:

        writer = csv.DictWriter(
            stream,
            fieldnames=list(rows[0])
        )

        writer.writeheader()
        writer.writerows(rows)

    proposal_columns = [
        "Competition",
        "Primary_Source_ID",
        "Primary_Home",
        "Primary_Away",
        "Second_Source_ID",
        "Second_Home",
        "Second_Away",
        "Primary_Score",
        "Second_Score",
        "Name_Similarity",
        "Identity_Approved",
        "Score_Verified"
    ]

    with proposal_file.open(
        "w",
        newline="",
        encoding="utf-8"
    ) as stream:

        writer = csv.DictWriter(
            stream,
            fieldnames=proposal_columns
        )

        writer.writeheader()
        writer.writerows(proposals)

    all_counts = dict(
        Counter(
            row["Corroboration_Status"]
            for row in rows
        )
    )

    by_league = {
        key: dict(Counter(
            row["Corroboration_Status"]
            for row in rows
            if row["Source_Competition_ID"] == key
        ))

        for key in (
            "eng.1",
            "esp.1",
            "bl1"
        )
    }

    coverage_complete = (
        not errors
        and all(
            fetched.get(key)
            for key in SOURCE_MAP
        )
    )

    report = {
        "step": "12A.2B",

        "status": (
            "RESEARCH_CROSSCHECK_COMPLETE"
            if coverage_complete
            else "PARTIAL_SOURCE_COVERAGE_REVIEW"
        ),

        "created_utc":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "previous_review_manifest_sha256":
            digest(prior_path),

        "primary_snapshot_utc":
            snapshot.isoformat(),

        "rows_reviewed": len(rows),

        "secondary_provider":
            "football-data.org",

        "secondary_evidence":
            evidence,

        "provider_errors":
            errors,

        "all_leagues_second_source_fetched":
            coverage_complete,

        "counts": all_counts,

        "counts_by_league":
            by_league,

        "new_name_proposals":
            len(proposals),

        "all_scores_verified": False,

        "identity_approved": False,

        "historical_records_modified": False,

        "elo_form_updated": False,

        "models_retrained": 0,

        "production_predictions_enabled": False,

        "google_drive_runtime_dependency": False,

        "consolidated_review_csv":
            str(combined),

        "proposals_csv":
            str(proposal_file)
    }

    write_json(
        manifest_file,
        report
    )

    # -----------------------------------------------------------
    # 9. RESULTS
    # -----------------------------------------------------------

    print("=" * 70)
    print(
        "STEP 12A.2B — PREMIER LEAGUE / LA LIGA CROSSCHECK"
    )
    print("=" * 70)

    print(
        "Records reviewed:",
        len(rows)
    )

    for league in SOURCE_MAP:

        print(
            league,
            "secondary finished records:",
            len(fetched[league])
        )

        print(
            "Statuses:",
            by_league[league]
        )

    print(
        "\nBundesliga statuses retained:",
        by_league["bl1"]
    )

    print(
        "\nNew team-name proposals:",
        len(proposals)
    )

    print(
        "Combined score-review statuses:",
        all_counts
    )

    print(
        "Provider errors:",
        errors or "NONE"
    )

    print("Historical matches modified: NO")
    print("Elo/form updated: NO")
    print("Models retrained: 0")
    print("Production predictions: DISABLED")

    print("\nCombined review:", combined)
    print("New name proposals:", proposal_file)
    print("Manifest:", manifest_file)

    if not coverage_complete:
        raise RuntimeError(
            "Second-source collection incomplete. "
            "Review the errors or provider coverage."
        )

    print(
        "\nSTEP 12A.2B COMPLETE — "
        "RESEARCH CROSSCHECK ONLY"
    )


if __name__ == "__main__":
    main()

