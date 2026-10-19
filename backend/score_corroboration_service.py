
import csv
import difflib
import hashlib
import importlib.util
import json
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path("/content/soccer_ai_hf_space").resolve()
STAGING = Path("/content/soccer_ai_live_staging").resolve()
REPORTS = STAGING / "reports"


def iso(value):

    if not value:
        return None

    try:
        dt = datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )

        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(timezone.utc)

    except (ValueError, TypeError):
        return None


def text_key(value):

    value = unicodedata.normalize(
        "NFKC",
        str(value or "")
    ).casefold()

    cleaned = "".join(
        ch if ch.isalnum() else " "
        for ch in value
    )

    return " ".join(cleaned.split())


def score_int(value):

    try:
        value = float(value)

        if value >= 0 and value.is_integer():
            return int(value)

    except (TypeError, ValueError):
        pass

    return None


def checksum(path):

    digest = hashlib.sha256()

    with path.open("rb") as stream:
        for chunk in iter(
            lambda: stream.read(1024 * 1024),
            b""
        ):
            digest.update(chunk)

    return digest.hexdigest()


def safe_stage_file(value):

    candidate = Path(value).resolve()

    if (
        REPORTS.resolve() not in candidate.parents
        or not candidate.is_file()
    ):
        raise RuntimeError(
            "Unsafe or missing original staging file: "
            + str(value)
        )

    return candidate


def write_json(path, payload):

    path.write_text(
        json.dumps(
            payload,
            indent=2,
            ensure_ascii=False,
            default=str
        ),
        encoding="utf-8"
    )


def main():

    # -----------------------------------------------------------
    # 1. LOAD THE ORIGINAL STEP 12A.1 RESULTS
    # -----------------------------------------------------------

    sources = sorted(
        REPORTS.glob(
            "step12a1_manifest_*.json"
        )
    )

    if not sources:
        raise FileNotFoundError(
            "Step 12A.1 staged manifest not found."
        )

    original_manifest_path = sources[-1]

    original_manifest = json.loads(
        original_manifest_path.read_text(
            encoding="utf-8"
        )
    )

    if (
        original_manifest.get("step") != "12A.1"
        or original_manifest.get(
            "production_predictions_enabled"
        ) is not False
    ):
        raise RuntimeError(
            "Unexpected Step 12A.1 manifest."
        )

    original_csv_path = safe_stage_file(
        original_manifest.get(
            "research_rows_file"
        )
    )

    with original_csv_path.open(
        newline="",
        encoding="utf-8"
    ) as stream:

        primary = list(
            csv.DictReader(stream)
        )

    if (
        len(primary) == 0
        or len(primary)
        != original_manifest["records_staged"]
    ):
        raise RuntimeError(
            "Original staged match count changed."
        )

    actual_statuses = Counter(
        row["Source_Status"]
        for row in primary
    )

    expected_statuses = Counter(
        original_manifest[
            "records_by_source_status"
        ]
    )

    if actual_statuses != expected_statuses:
        raise RuntimeError(
            "Original source status counts changed."
        )

    # -----------------------------------------------------------
    # 2. IMPORT ORIGINAL STEP 12A.1 PROVIDER CLIENT
    # -----------------------------------------------------------

    source_py = (
        ROOT
        / "backend/live_data_service.py"
    )

    if not source_py.is_file():
        raise FileNotFoundError(source_py)

    spec = (
        importlib.util.spec_from_file_location(
            "soccer_original_live_service",
            source_py
        )
    )

    live = (
        importlib.util.module_from_spec(
            spec
        )
    )

    spec.loader.exec_module(live)

    snapshot = iso(
        original_manifest["created_utc"]
    )

    start = iso(
        original_manifest[
            "research_collection_start_utc"
        ]
    )

    if (
        snapshot is None
        or start is None
        or snapshot < start
    ):
        raise RuntimeError(
            "Original staging chronology invalid."
        )

    # -----------------------------------------------------------
    # 3. COLLECT ESPN BUNDESLIGA MATCHES
    # -----------------------------------------------------------
    # This is a second publisher for OpenLigaDB.
    # Restrict observations to the original
    # Step 12A.1 snapshot time.
    # -----------------------------------------------------------

    client = live.ProviderClient(
        STAGING / "provider_cache"
    )

    secondary = []
    fetch_errors = []

    for month in live.month_codes(
        start,
        snapshot
    ):

        url = (
            "https://site.api.espn.com/"
            "apis/site/v2/sports/soccer/"
            "ger.1/scoreboard?dates="
            + month
            + "&limit=500"
        )

        try:

            payload = client.get_json(url)

            records = live.parse_espn(
                payload,
                "ger.1"
            )

            for record in records:

                kickoff = iso(
                    record["Kickoff_UTC"]
                )

                if (
                    kickoff is not None
                    and start <= kickoff <= snapshot
                    and record["Source_Status"]
                        == "FINISHED"
                ):

                    secondary.append({
                        **record,
                        "_source_url": url
                    })

        except Exception as exc:

            fetch_errors.append({
                "month": month,
                "error": (
                    f"{type(exc).__name__}: {exc}"
                )
            })

    # -----------------------------------------------------------
    # 4. QUARANTINE DUPLICATE SECONDARY IDs
    # -----------------------------------------------------------

    secondary_by_id = defaultdict(list)

    for record in secondary:

        secondary_by_id[
            record["Source_Match_ID"]
        ].append(record)

    duplicate_secondary_ids = {
        key
        for key, entries
        in secondary_by_id.items()
        if len(entries) > 1
    }

    secondary = [
        record for record in secondary
        if record["Source_Match_ID"]
            not in duplicate_secondary_ids
    ]

    # -----------------------------------------------------------
    # 5. CHECK ORIGINAL SOURCE ID UNIQUENESS
    # -----------------------------------------------------------

    primary_id_counts = Counter(
        (
            row["Source"],
            row["Source_Competition_ID"],
            row["Source_Match_ID"]
        )
        for row in primary
    )

    results = []
    proposed = []

    # Tracks possible exact source pairings.
    candidate_pairs = defaultdict(list)
    source_index = {}

    # -----------------------------------------------------------
    # 6. COMPARE COMPLETED MATCHES
    # -----------------------------------------------------------

    for index, row in enumerate(primary):

        item = dict(row)

        item.update({
            "Corroboration_Status":
                "NOT_APPLICABLE",

            "Secondary_Source": "",
            "Secondary_Match_ID": "",
            "Secondary_Home_Team": "",
            "Secondary_Away_Team": "",
            "Secondary_Home_Score": "",
            "Secondary_Away_Score": "",
            "Secondary_UTC": "",
            "Secondary_Evidence_URL": "",

            "Identity_Approved": "False",
            "Feature_Update_Authorized": "False",
            "Prediction_Authorized": "False"
        })

        results.append(item)

        identity = (
            row["Source"],
            row["Source_Competition_ID"],
            row["Source_Match_ID"]
        )

        if primary_id_counts[identity] > 1:

            item["Corroboration_Status"] = (
                "QUARANTINED_DUPLICATE_SOURCE_ID"
            )

            continue

        if row["Source_Status"] != "FINISHED":

            item["Corroboration_Status"] = (
                "QUARANTINED_SOURCE_STATUS"
                if row["Source_Status"] in (
                    "FUTURE_FINAL_CONFLICT",
                    "FINAL_SCORE_REVIEW"
                )
                else "NOT_FINISHED"
            )

            continue

        kickoff = iso(
            row["Kickoff_UTC"]
        )

        home_goals = score_int(
            row["Home_Score"]
        )

        away_goals = score_int(
            row["Away_Score"]
        )

        if (
            kickoff is None
            or kickoff > snapshot
            or home_goals is None
            or away_goals is None
        ):

            item["Corroboration_Status"] = (
                "QUARANTINED_BAD_PRIMARY_RESULT"
            )

            continue

        # Premier League and La Liga currently
        # have ESPN as their original provider.
        # Comparing ESPN with itself is forbidden.

        if (
            row["Source"] != "OpenLigaDB"
            or row["Source_Competition_ID"]
                != "bl1"
        ):

            item["Corroboration_Status"] = (
                "AWAITING_PERMITTED_SECOND_SOURCE"
            )

            continue

        # Same competition is established by the
        # explicit bl1 -> ger.1 adapter above.
        # Team identities must still be checked.

        same_day = [
            record for record in secondary
            if abs(
                iso(
                    record["Kickoff_UTC"]
                ) - kickoff
            ) <= timedelta(hours=24)
        ]

        # Strict matching only.
        # No aliases, fuzzy matching or inferred
        # team equivalences are approved here.

        exact = [
            record for record in same_day

            if (
                abs(
                    iso(
                        record["Kickoff_UTC"]
                    ) - kickoff
                ) <= timedelta(hours=8)

                and text_key(
                    record["Home_Team"]
                ) == text_key(
                    row["Home_Team"]
                )

                and text_key(
                    record["Away_Team"]
                ) == text_key(
                    row["Away_Team"]
                )
            )
        ]

        if len(exact) == 1:

            match = exact[0]

            candidate_pairs[
                match["Source_Match_ID"]
            ].append(index)

            source_index[index] = match

            item["Corroboration_Status"] = (
                "PENDING_UNIQUENESS_CHECK"
            )

        elif len(exact) > 1:

            item["Corroboration_Status"] = (
                "QUARANTINED_AMBIGUOUS_MATCH"
            )

        else:

            # Fuzzy comparisons are REVIEW SUGGESTIONS
            # only. They never verify scores or IDs.

            rankings = []

            for record in same_day:

                home_similarity = (
                    difflib.SequenceMatcher(
                        None,
                        text_key(
                            row["Home_Team"]
                        ),
                        text_key(
                            record["Home_Team"]
                        )
                    ).ratio()
                )

                away_similarity = (
                    difflib.SequenceMatcher(
                        None,
                        text_key(
                            row["Away_Team"]
                        ),
                        text_key(
                            record["Away_Team"]
                        )
                    ).ratio()
                )

                if min(
                    home_similarity,
                    away_similarity
                ) >= 0.50:

                    rankings.append((
                        min(
                            home_similarity,
                            away_similarity
                        ),
                        (
                            home_similarity
                            + away_similarity
                        ) / 2,
                        record
                    ))

            rankings.sort(
                key=lambda x: (
                    -x[0],
                    -x[1],
                    x[2]["Source_Match_ID"]
                )
            )

            if rankings:

                item["Corroboration_Status"] = (
                    "TEAM_NAME_MAPPING_REVIEW"
                )

                best = rankings[0][2]

                proposed.append({
                    "Primary_Source_Match_ID":
                        row["Source_Match_ID"],

                    "Primary_Home":
                        row["Home_Team"],

                    "Primary_Away":
                        row["Away_Team"],

                    "Primary_Kickoff_UTC":
                        row["Kickoff_UTC"],

                    "Primary_Score":
                        f"{home_goals}-{away_goals}",

                    "Proposed_Secondary_Match_ID":
                        best["Source_Match_ID"],

                    "Proposed_Secondary_Home":
                        best["Home_Team"],

                    "Proposed_Secondary_Away":
                        best["Away_Team"],

                    "Proposed_Secondary_Kickoff_UTC":
                        best["Kickoff_UTC"],

                    "Proposed_Secondary_Score":
                        (
                            f"{best['Home_Score']}"
                            f"-{best['Away_Score']}"
                        ),

                    "Name_Similarity":
                        round(
                            rankings[0][1],
                            5
                        ),

                    "Proposal_Only": True,

                    "Identity_Approved": False,

                    "Score_Verified": False
                })

            else:

                item["Corroboration_Status"] = (
                    "NO_EXACT_SECOND_SOURCE_MATCH"
                )

    # -----------------------------------------------------------
    # 7. APPLY UNIQUE MATCH AND SCORE CHECKS
    # -----------------------------------------------------------

    for index, match in source_index.items():

        item = results[index]

        item["Secondary_Source"] = "ESPN"

        item["Secondary_Match_ID"] = (
            match["Source_Match_ID"]
        )

        item["Secondary_Home_Team"] = (
            match["Home_Team"]
        )

        item["Secondary_Away_Team"] = (
            match["Away_Team"]
        )

        item["Secondary_Home_Score"] = str(
            match["Home_Score"]
        )

        item["Secondary_Away_Score"] = str(
            match["Away_Score"]
        )

        item["Secondary_UTC"] = (
            match["Kickoff_UTC"]
        )

        item["Secondary_Evidence_URL"] = (
            match["_source_url"]
        )

        # Never use a secondary match that was
        # paired with multiple primary results.

        if len(
            candidate_pairs[
                match["Source_Match_ID"]
            ]
        ) != 1:

            item["Corroboration_Status"] = (
                "QUARANTINED_AMBIGUOUS_MATCH"
            )

        elif (
            score_int(
                item["Home_Score"]
            ),
            score_int(
                item["Away_Score"]
            )
        ) == (
            score_int(
                match["Home_Score"]
            ),
            score_int(
                match["Away_Score"]
            )
        ):

            item["Corroboration_Status"] = (
                "SCORE_CORROBORATED_IDENTITY_PENDING"
            )

        else:

            item["Corroboration_Status"] = (
                "QUARANTINED_SCORE_CONFLICT"
            )

    # -----------------------------------------------------------
    # 8. WRITE REVIEW FILES OUTSIDE DEPLOYMENT PACKAGE
    # -----------------------------------------------------------

    REPORTS.mkdir(
        parents=True,
        exist_ok=True
    )

    stamp = datetime.now(
        timezone.utc
    ).strftime(
        "%Y%m%d_%H%M%S_%f"
    )

    output_csv = (
        REPORTS
        / f"step12a2_match_review_{stamp}.csv"
    )

    proposal_csv = (
        REPORTS
        / f"step12a2_name_proposals_{stamp}.csv"
    )

    manifest_path = (
        REPORTS
        / f"step12a2_manifest_{stamp}.json"
    )

    with output_csv.open(
        "w",
        newline="",
        encoding="utf-8"
    ) as stream:

        writer = csv.DictWriter(
            stream,
            fieldnames=list(
                results[0].keys()
            )
        )

        writer.writeheader()
        writer.writerows(results)

    proposal_headers = [
        "Primary_Source_Match_ID",
        "Primary_Home",
        "Primary_Away",
        "Primary_Kickoff_UTC",
        "Primary_Score",
        "Proposed_Secondary_Match_ID",
        "Proposed_Secondary_Home",
        "Proposed_Secondary_Away",
        "Proposed_Secondary_Kickoff_UTC",
        "Proposed_Secondary_Score",
        "Name_Similarity",
        "Proposal_Only",
        "Identity_Approved",
        "Score_Verified"
    ]

    with proposal_csv.open(
        "w",
        newline="",
        encoding="utf-8"
    ) as stream:

        writer = csv.DictWriter(
            stream,
            fieldnames=proposal_headers
        )

        writer.writeheader()
        writer.writerows(proposed)

    counts = dict(
        sorted(
            Counter(
                row["Corroboration_Status"]
                for row in results
            ).items()
        )
    )

    audit_status = (
        "RESEARCH_CROSSCHECK_COMPLETE"

        if (
            secondary
            and not fetch_errors
        )

        else "PARTIAL_SECONDARY_COVERAGE_REVIEW"
    )

    # -----------------------------------------------------------
    # 9. SAVE PROVENANCE AND SAFETY MANIFEST
    # -----------------------------------------------------------

    report = {
        "step": "12A.2A",

        "status": audit_status,

        "created_utc":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "primary_manifest_path":
            str(original_manifest_path),

        "primary_manifest_sha256":
            checksum(
                original_manifest_path
            ),

        "primary_csv_sha256":
            checksum(
                original_csv_path
            ),

        "primary_records":
            len(primary),

        "primary_finished":
            sum(
                row["Source_Status"]
                == "FINISHED"
                for row in primary
            ),

        "secondary_source":
            "ESPN Bundesliga ger.1",

        "secondary_completed_records":
            len(secondary),

        "secondary_duplicate_ids_quarantined":
            sorted(
                duplicate_secondary_ids
            ),

        "secondary_fetch_errors":
            fetch_errors,

        "secondary_evidence":
            client.evidence,

        "secondary_http_calls":
            client.http_calls,

        "secondary_cache_hits":
            client.cache_hits,

        "review_counts":
            counts,

        "team_name_proposals":
            len(proposed),

        "premier_league_second_source_required":
            True,

        "la_liga_second_source_required":
            True,

        "corroboration_not_team_identity_approval":
            True,

        "all_leagues_corroborated":
            False,

        "historical_results_modified":
            False,

        "elo_form_updated":
            False,

        "models_modified":
            False,

        "production_predictions_enabled":
            False,

        "google_drive_runtime_dependency":
            False,

        "review_csv":
            str(output_csv),

        "proposals_csv":
            str(proposal_csv)
    }

    write_json(
        manifest_path,
        report
    )

    # -----------------------------------------------------------
    # 10. FINAL DIAGNOSTICS
    # -----------------------------------------------------------

    print("=" * 70)
    print(
        "STEP 12A.2A — INDEPENDENT SCORE CROSSCHECK"
    )
    print("=" * 70)

    print(
        "Staged primary records:",
        len(primary)
    )

    print(
        "Finished primary records:",
        report["primary_finished"]
    )

    print(
        "Independent finished Bundesliga records:",
        len(secondary)
    )

    print(
        "ESPN HTTP requests/cache hits:",
        client.http_calls,
        "/",
        client.cache_hits
    )

    print(
        "ESPN provider errors:",
        len(fetch_errors)
    )

    for error in fetch_errors:
        print("PROVIDER ERROR:", error)

    print("\nScore review statuses:")

    for status, count in counts.items():
        print(
            " ",
            status,
            count
        )

    print(
        "\nUnapproved team-name proposals:",
        len(proposed)
    )

    print(
        "Premier League second-source coverage:",
        "PENDING"
    )

    print(
        "La Liga second-source coverage:",
        "PENDING"
    )

    print(
        "Historical matches modified:",
        "NO"
    )

    print(
        "Elo/form updated:",
        "NO"
    )

    print(
        "Production predictions:",
        "DISABLED"
    )

    print(
        "\nReview CSV:",
        output_csv
    )

    print(
        "Name proposals:",
        proposal_csv
    )

    print(
        "Manifest:",
        manifest_path
    )

    print(
        "Research audit status:",
        audit_status
    )

    if not secondary or fetch_errors:

        raise RuntimeError(
            "Secondary source collection incomplete. "
            "Review errors before proceeding."
        )

    print(
        "\nSTEP 12A.2A COMPLETE — "
        "BUNDESLIGA RESEARCH CROSSCHECK ONLY"
    )


if __name__ == "__main__":
    main()
