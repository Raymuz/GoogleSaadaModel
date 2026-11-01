
import re
import unicodedata
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

MAX_MATCHES = 16

MARKETS = {
    "Over 2.5": (r"\bover\s*2[.,]5\b", r"\bo\s*2[.,]5\b"),
    "Under 2.5": (r"\bunder\s*2[.,]5\b", r"\bu\s*2[.,]5\b"),
    "Over 1.5": (r"\bover\s*1[.,]5\b",),
    "Under 1.5": (r"\bunder\s*1[.,]5\b",),
    "BTTS": (r"\bbtts\b", r"both teams to score"),
    "Home win": (r"\bhome win\b", r"\bhome to win\b"),
    "Draw": (r"\bdraw\b",),
    "Away win": (r"\baway win\b", r"\baway to win\b"),
    "Full-time result": (r"\b1x2\b", r"\bmatch result\b"),
}

def normalize(value):
    s = unicodedata.normalize("NFKD", str(value or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s.casefold()).split())

def parse_request(text, now=None, timezone="UTC"):
    raw = str(text or "").strip()
    if not raw:
        return {
            "status": "EMPTY_REQUEST",
            "maximum": MAX_MATCHES,
            "count": 0,
            "mode": None,
            "markets": [],
            "manual_pairs": []
        }

    try:
        clock = ZoneInfo(timezone)
    except Exception:
        return {"status": "INVALID_TIMEZONE", "maximum": MAX_MATCHES}

    now = now or datetime.now(clock)
    if now.tzinfo is None:
        now = now.replace(tzinfo=clock)
    today = now.astimezone(clock).date()
    lowered = raw.casefold()

    number_words = {
        "one": 1, "two": 2, "three": 3, "four": 4,
        "five": 5, "six": 6, "seven": 7, "eight": 8,
        "nine": 9, "ten": 10, "eleven": 11,
        "twelve": 12, "thirteen": 13, "fourteen": 14,
        "fifteen": 15, "sixteen": 16
    }

    count_match = re.search(
        r"\b(\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen)"
        r"\s+(?:games?|matches?|fixtures?|predictions?|picks?)\b",
        lowered
    )
    # Also understand "predict 10 matches" and "find 16 fixtures".
    requested = None
    if count_match:
        token = count_match.group(1)
        requested = int(token) if token.isdigit() else number_words[token]

    pairs = []
    # Parse explicitly separated fixture pairs.
    # Never assume these are scheduled or verified.
    for part in re.split(r"[\n;]+", raw):
        m = re.search(
            r"([A-Za-zÀ-ÿ0-9][A-Za-zÀ-ÿ0-9 .&'()-]{1,70}?)"
            r"\s+(?:vs\.?|v\.?)\s+"
            r"([A-Za-zÀ-ÿ0-9][A-Za-zÀ-ÿ0-9 .&'()-]{1,70})",
            part, re.I
        )
        if m:
            home = re.sub(
                r"^(?:predict|analyze|analyse|compare)\s+",
                "", m.group(1).strip(), flags=re.I
            )
            away = re.split(
                r"\s+(?:and explain|with probability|for btts|for over)\b",
                m.group(2).strip(), maxsplit=1, flags=re.I
            )[0].strip()
            pairs.append({"home": home, "away": away})

    if len(pairs) > MAX_MATCHES or (
        requested is not None and requested > MAX_MATCHES
    ):
        return {
            "status": "LIMIT_EXCEEDED",
            "maximum": MAX_MATCHES,
            "requested": requested if requested is not None else len(pairs),
            "manual_pairs": [],
            "markets": []
        }

    if requested == 0:
        return {"status": "INVALID_COUNT", "maximum": MAX_MATCHES}

    markets = [
        name for name, patterns in MARKETS.items()
        if any(re.search(p, lowered) for p in patterns)
    ]
    if not markets:
        markets = ["Full-time result"]

    date_start = None
    date_end = None
    if re.search(r"\btomorrow\b", lowered):
        date_start = date_end = today + timedelta(days=1)
    elif re.search(r"\btoday\b", lowered):
        date_start = date_end = today
    elif re.search(r"\bthis weekend\b", lowered):
        days_to_sat = (5 - today.weekday()) % 7
        date_start = today + timedelta(days=days_to_sat)
        date_end = date_start + timedelta(days=1)

    mode = "MANUAL_MATCHES" if pairs else "FIND_FIXTURES"
    return {
        "status": "PARSED",
        "mode": mode,
        "maximum": MAX_MATCHES,
        "count": requested or (len(pairs) if pairs else 5),
        "manual_pairs": pairs,
        "markets": markets,
        "date_start": date_start.isoformat() if date_start else None,
        "date_end": date_end.isoformat() if date_end else None,
        "timezone": timezone,
        "ranking_requested": bool(
            re.search(r"\b(best|highest|rank|top|most likely)\b", lowered)
        ),
        "fixture_verification_required": True,
        "prediction_required": True
    }

def safe_fixture_candidates(parsed, provider_fixtures):
    """
    Accept only supplied fixture records with IDs, valid team names,
    and explicit scheduled status. This function does not fetch fixtures
    or calculate probabilities.
    """
    if parsed.get("status") != "PARSED":
        return []
    if parsed.get("mode") != "FIND_FIXTURES":
        return []

    selected = []
    seen = set()
    for fixture in provider_fixtures:
        if not isinstance(fixture, dict):
            continue
        fid = fixture.get("id")
        home = fixture.get("home")
        away = fixture.get("away")
        status = str(fixture.get("status", "")).upper()
        kickoff = str(fixture.get("kickoff", ""))

        if not all((fid is not None, home, away, kickoff)):
            continue
        if status not in ("SCHEDULED", "NOT_STARTED", "NS"):
            continue
        if str(fid) in seen:
            continue

        try:
            dt = datetime.fromisoformat(kickoff.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                continue
            local_day = dt.astimezone(
                ZoneInfo(parsed["timezone"])
            ).date().isoformat()
        except (ValueError, TypeError):
            continue

        start = parsed.get("date_start")
        end = parsed.get("date_end")
        if start and local_day < start:
            continue
        if end and local_day > end:
            continue

        seen.add(str(fid))
        selected.append(dict(fixture))

    selected.sort(key=lambda x: x["kickoff"])
    return selected[:min(parsed["count"], MAX_MATCHES)]
