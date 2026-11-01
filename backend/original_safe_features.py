from __future__ import annotations
# Original Soccer AI notebook feature functions.
# Research-only serialization.
import numpy as np
import pandas as pd
from collections import defaultdict

_DAY_NS = 86400000000000

def _utc_day_number(values):
    """UTC calendar days as int64, independent of input datetime resolution."""
    ts = pd.to_datetime(values, utc=True, errors="raise")
    if ts.isna().any():
        raise ValueError("Missing datetime in historical merge key")
    ts = ts.astype("datetime64[ns, UTC]").dt.floor("D")
    return (ts.astype("int64") // _DAY_NS).to_numpy(dtype=np.int64)


def _prior_day_asof(left, right, *, left_on, right_on, by):
    """Attach the most recent same-entity history from strictly earlier UTC day."""
    l = left.copy()
    r = right.copy()
    if any(c in l.columns or c in r.columns for c in ("_query_day_int", "_history_day_int")):
        raise ValueError("Temporary history join column already in use")
    l["_query_day_int"] = _utc_day_number(l[left_on])
    r["_history_day_int"] = _utc_day_number(r[right_on])
    l = l.sort_values("_query_day_int", kind="mergesort")
    r = r.sort_values("_history_day_int", kind="mergesort")
    out = pd.merge_asof(
        l, r,
        left_on="_query_day_int",
        right_on="_history_day_int",
        by=by,
        direction="backward",
        allow_exact_matches=False,
    )
    found = out["_history_day_int"].notna()
    if found.any() and not (
        out.loc[found, "_history_day_int"].to_numpy(dtype=np.int64)
        < out.loc[found, "_query_day_int"].to_numpy(dtype=np.int64)
    ).all():
        raise AssertionError("Same-day or future history was included")
    if len(out) != len(left):
        raise AssertionError("Historical merge unexpectedly changed row count")
    return out.drop(columns=["_query_day_int", "_history_day_int"])


def build_safe_soccer_features(matches_df):
    required = [
        "match_id", "date_utc", "league_id", "home_team_key", "away_team_key",
        "is_played", "home_goals", "away_goals",
    ]
    absent = sorted(set(required) - set(matches_df.columns))
    if absent:
        raise ValueError(f"Missing source fields: {absent}")

    df = matches_df.copy()
    df["date_utc"] = pd.to_datetime(df["date_utc"], utc=True, errors="raise").astype("datetime64[ns, UTC]")
    if df["date_utc"].isna().any():
        raise ValueError("Missing match kickoffs")
    if df["match_id"].isna().any() or df["match_id"].duplicated().any():
        raise ValueError("Missing or duplicate match IDs")
    for key in ("league_id", "home_team_key", "away_team_key"):
        if df[key].isna().any():
            raise ValueError(f"Missing identity: {key}")
        df[key] = df[key].astype(str)
    for col in ("home_goals", "away_goals"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["match_day"] = df["date_utc"].dt.floor("D")
    df = df.sort_values(["match_day", "date_utc", "match_id"]).reset_index(drop=True)

    is_played = df["is_played"].fillna(False).eq(True)
    valid_result = (is_played & df["home_goals"].notna() & df["away_goals"].notna()).to_numpy()
    played = df.loc[valid_result].copy()
    if played.empty:
        raise ValueError("No completed matches found")
    goals = played[["home_goals", "away_goals"]].to_numpy(dtype=float)
    if not (np.isfinite(goals).all() and (goals >= 0).all() and (goals == np.floor(goals)).all()):
        raise ValueError("Completed matches contain invalid goal counts")

    print("Historical records:", len(df))
    print("Completed matches:", len(played))

    # A. DATE-FROZEN ELO: calculate all pre-match ratings for the day,
    # then apply that day's outcomes together only after those predictions.
    ratings = {}
    home_elo = np.empty(len(df), dtype=float)
    away_elo = np.empty(len(df), dtype=float)
    elo_diff = np.empty(len(df), dtype=float)
    home_keys = df["home_team_key"].to_numpy()
    away_keys = df["away_team_key"].to_numpy()
    hg = df["home_goals"].to_numpy(dtype=float)
    ag = df["away_goals"].to_numpy(dtype=float)

    for _, positions in df.groupby("match_day", sort=False).indices.items():
        rh = np.array([ratings.get(t, 1500.0) for t in home_keys[positions]], dtype=float)
        ra = np.array([ratings.get(t, 1500.0) for t in away_keys[positions]], dtype=float)
        home_elo[positions] = rh
        away_elo[positions] = ra
        diffs = rh + 65.0 - ra
        elo_diff[positions] = diffs
        mask = valid_result[positions]
        if not mask.any():
            continue
        actual = np.where(hg[positions][mask] > ag[positions][mask], 1.0,
                          np.where(hg[positions][mask] == ag[positions][mask], 0.5, 0.0))
        expected = 1.0 / (1.0 + 10.0 ** (-diffs[mask] / 400.0))
        pending = defaultdict(float)
        for home, away, delta in zip(home_keys[positions][mask], away_keys[positions][mask],
                                     20.0 * (actual - expected)):
            pending[home] += float(delta)
            pending[away] -= float(delta)
        for team, delta in pending.items():
            ratings[team] = ratings.get(team, 1500.0) + delta

    safe = df[["match_id", "date_utc", "match_day", "league_id"]].copy()
    safe["home_elo_pre"] = home_elo
    safe["away_elo_pre"] = away_elo
    safe["elo_diff_pre"] = elo_diff
    print("Date-frozen Elo: complete")

    # B. HISTORICAL TEAM FORM. Create completed-match team events.
    def source(column):
        if column in played.columns:
            return pd.to_numeric(played[column], errors="coerce").to_numpy(dtype=float)
        return np.full(len(played), np.nan, dtype=float)

    home_data = {
        "match_id": played["match_id"].to_numpy(),
        "date_utc": played["date_utc"].to_numpy(),
        "match_day": played["match_day"].to_numpy(),
        "team_key": played["home_team_key"].to_numpy(),
        "side": "home",
        "gf": played["home_goals"].to_numpy(dtype=float),
        "ga": played["away_goals"].to_numpy(dtype=float),
    }
    away_data = {
        "match_id": played["match_id"].to_numpy(),
        "date_utc": played["date_utc"].to_numpy(),
        "match_day": played["match_day"].to_numpy(),
        "team_key": played["away_team_key"].to_numpy(),
        "side": "away",
        "gf": played["away_goals"].to_numpy(dtype=float),
        "ga": played["home_goals"].to_numpy(dtype=float),
    }
    metric_sources = [
        ("shots_for", "home_shots_total", "away_shots_total"),
        ("shots_against", "away_shots_total", "home_shots_total"),
        ("sot_for", "home_shots_on_goal", "away_shots_on_goal"),
        ("sot_against", "away_shots_on_goal", "home_shots_on_goal"),
        ("corners_for", "home_corners", "away_corners"),
        ("corners_against", "away_corners", "home_corners"),
        ("xg_for", "home_xg", "away_xg"),
        ("xg_against", "away_xg", "home_xg"),
    ]
    for metric, h_src, a_src in metric_sources:
        home_data[metric] = source(h_src)
        away_data[metric] = source(a_src)

    events = pd.concat([pd.DataFrame(home_data), pd.DataFrame(away_data)], ignore_index=True)
    # Prevent NumPy array construction from silently changing timezone/precision.
    events["date_utc"] = pd.to_datetime(events["date_utc"], utc=True).astype("datetime64[ns, UTC]")
    events["match_day"] = pd.to_datetime(events["match_day"], utc=True).astype("datetime64[ns, UTC]")
    events["points"] = np.where(events["gf"] > events["ga"], 3.0,
                                np.where(events["gf"] == events["ga"], 1.0, 0.0))
    events = events.sort_values(["team_key", "date_utc", "match_id", "side"]).reset_index(drop=True)
    metrics = ["points", "gf", "ga", "shots_for", "shots_against", "sot_for",
               "sot_against", "corners_for", "corners_against", "xg_for", "xg_against"]
    rolling_columns = []
    for window in (5, 10):
        rolled = (
            events.groupby("team_key", sort=False)[metrics]
            .rolling(window, min_periods=1).mean()
            .reset_index(level=0, drop=True)
            .reindex(events.index)
        )
        rolled.columns = [f"{m}_avg_{window}" for m in metrics]
        events = pd.concat([events, rolled], axis=1)
        rolling_columns.extend(rolled.columns.tolist())

    snapshots = (
        events.drop_duplicates(["team_key", "match_day"], keep="last")
        [["team_key", "match_day", "date_utc"] + rolling_columns]
        .rename(columns={"match_day": "history_day", "date_utc": "history_kickoff"})
    )
    home_queries = df[["match_id", "match_day", "date_utc", "home_team_key"]].rename(
        columns={"home_team_key": "team_key"}).copy()
    home_queries["side"] = "home"
    away_queries = df[["match_id", "match_day", "date_utc", "away_team_key"]].rename(
        columns={"away_team_key": "team_key"}).copy()
    away_queries["side"] = "away"
    queries = pd.concat([home_queries, away_queries], ignore_index=True)

    history = _prior_day_asof(
        queries, snapshots, left_on="match_day", right_on="history_day", by="team_key"
    )
    # Exclude unavailable same-day events by construction.
    history["rest_days"] = (
        history["date_utc"] - history["history_kickoff"]
    ).dt.total_seconds().div(86400).clip(lower=1, upper=30)

    for side in ("home", "away"):
        part = history.loc[history["side"].eq(side),
                           ["match_id", "history_day", "rest_days"] + rolling_columns].copy()
        part = part.rename(columns={c: f"{side}_{c}"
                                    for c in ["history_day", "rest_days"] + rolling_columns})
        safe = safe.merge(part, on="match_id", how="left", validate="one_to_one", sort=False)
    print("Prior-day team form: complete")

    # C. LEAGUE GOAL AVERAGES. The snapshots include that day's games,
    # but the asof merge attaches only an EARLIER day's snapshot.
    daily = (
        played.groupby(["league_id", "match_day"], as_index=False)
        .agg(home_sum=("home_goals", "sum"), away_sum=("away_goals", "sum"),
             n=("match_id", "size"))
        .sort_values(["league_id", "match_day"]).reset_index(drop=True)
    )
    grouped = daily.groupby("league_id", sort=False)
    daily["n_cum"] = grouped["n"].cumsum()
    daily["home_cum"] = grouped["home_sum"].cumsum()
    daily["away_cum"] = grouped["away_sum"].cumsum()
    daily["league_home_goals_prior"] = np.where(
        daily["n_cum"] >= 20, daily["home_cum"] / daily["n_cum"], np.nan)
    daily["league_away_goals_prior"] = np.where(
        daily["n_cum"] >= 20, daily["away_cum"] / daily["n_cum"], np.nan)
    daily = daily.rename(columns={"match_day": "league_history_day"})
    prior_league = _prior_day_asof(
        safe[["match_id", "match_day", "league_id"]],
        daily[["league_id", "league_history_day", "league_home_goals_prior",
               "league_away_goals_prior"]],
        left_on="match_day", right_on="league_history_day", by="league_id"
    )
    safe = safe.merge(
        prior_league[["match_id", "league_history_day", "league_home_goals_prior",
                      "league_away_goals_prior"]],
        on="match_id", how="left", validate="one_to_one", sort=False
    )
    print("Prior-day league averages: complete")

    # D. SAFE CALENDAR FEATURES
    month = safe["date_utc"].dt.month
    safe["month_sin"] = np.sin(2 * np.pi * month / 12)
    safe["month_cos"] = np.cos(2 * np.pi * month / 12)
    safe = safe.sort_values(["date_utc", "match_id"]).reset_index(drop=True)

    # E. INTERNAL VALIDATION
    if len(safe) != len(df) or not safe["match_id"].is_unique:
        raise AssertionError("The corrected feature dataset lost/duplicated matches")
    for col in ("home_history_day", "away_history_day", "league_history_day"):
        if not (safe[col].isna() | (safe[col] < safe["match_day"])).all():
            raise AssertionError(f"Same-day or future history detected in {col}")
    print("Corrected feature rebuild: complete")
    return safe

