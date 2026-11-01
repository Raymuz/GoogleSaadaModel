"""Request-local verified recent match overlay for the unchanged original model feature builder.

The caller MUST provide a trusted identity resolver implementing the proof
contract. A user-supplied alias or an LLM suggestion is not a proof.
Only the trained-league ESPN finals accepted by Stage A enter this overlay.
Never edit the frozen training Parquet or serialized model files.
"""
from datetime import datetime, timezone
from pathlib import Path
import hashlib, json, math
import pandas as pd
import numpy as np

SUPPORTED = frozenset(('eng.1', 'esp.1', 'ger.1'))
EVIDENCE = frozenset((
    'PROVIDER_ID_UNIQUE_HISTORICAL_ALIAS',
    'SOURCE_NAME_UNIQUE_EXACT_HISTORICAL_ALIAS',
))
REQUIRED_HISTORY = frozenset((
    'match_id', 'date_utc', 'league_id', 'home_team_key', 'away_team_key',
    'is_played', 'home_goals', 'away_goals',
    'league_name', 'home_team_name', 'away_team_name'
))

def utc(value):
    stamp = pd.Timestamp(value)
    if pd.isna(stamp) or stamp.tzinfo is None:
        raise ValueError('A timezone-aware timestamp is required')
    return stamp.tz_convert('UTC')

def _validate_proof(proof, event, side):
    if not isinstance(proof, dict):
        raise ValueError('Trusted historical identity proof is missing')
    required = (
        'source_competition_id', 'provider_team_id', 'canonical_id',
        'historical_league_id', 'historical_team_key',
        'historical_label', 'league_name', 'candidate_count', 'evidence_kind'
    )
    if any(field not in proof for field in required):
        raise ValueError('Incomplete trusted identity proof')
    if (str(proof['source_competition_id']) != event['source_competition_id']
        or str(proof['provider_team_id']) != event[side + '_team_id']
        or proof['candidate_count'] != 1
        or proof['evidence_kind'] not in EVIDENCE):
        raise ValueError('Source identity mismatch or ambiguous resolution')
    for key in ('canonical_id', 'historical_league_id',
                'historical_team_key', 'historical_label', 'league_name'):
        if not str(proof[key] or '').strip():
            raise ValueError('Empty historical identity or model category')
    return proof

def _evidence_integrity(event):
    if not isinstance(event, dict):
        return False
    digest = event.get('source_evidence_sha256')
    if not isinstance(digest, str) or len(digest) != 64:
        return False
    original = {k:v for k,v in event.items() if k != 'source_evidence_sha256'}
    return hashlib.sha256(json.dumps(original, sort_keys=True).encode()).hexdigest() == digest

def prepare_overlay(history_and_queries, finals, provider_metadata,
                    *, kickoff_utc, history_last_utc, trusted_identity_resolver):
    """Return (new input dataframe, provenance) for the ORIGINAL feature builder.

    The resolver is supplied by the original verified historical identity gate.
    It must never be supplied by GPT or directly from users.
    """
    if not isinstance(history_and_queries, pd.DataFrame):
        raise TypeError('Original feature-builder input must be a DataFrame')
    if REQUIRED_HISTORY - set(history_and_queries.columns):
        raise ValueError('Original model feature inputs have changed')
    if history_and_queries['match_id'].astype(str).duplicated().any():
        raise ValueError('Duplicate original research match ID')
    if not callable(trusted_identity_resolver):
        raise TypeError('Trusted registry-backed identity resolver is required')
    if not isinstance(provider_metadata, dict):
        raise ValueError('Missing recent-results source status')
    status = provider_metadata.get('status')
    if status == 'RECENT_SOURCE_PARTIAL_FROZEN_BASELINE':
        if finals:
            raise ValueError('Partial provider response cannot contain accepted finals')
        return history_and_queries.copy(deep=True), {
            'status': 'FROZEN_BASELINE_SOURCE_INCOMPLETE', 'added': 0,
            'examined': 0, 'rejected': 0
        }
    if status != 'RECENT_SOURCE_COMPLETE' or provider_metadata.get('source_errors', 0) != 0:
        raise ValueError('Recent score provider verification is incomplete')
    if not isinstance(finals, list):
        raise TypeError('Final match observations must be a list')
    cutoff = utc(kickoff_utc).floor('D')
    frozen_last = utc(history_last_utc)
    base_ids = set(history_and_queries['match_id'].astype(str))
    new_rows, seen_source, seen_clubs = [], set(), {}
    rejected = 0
    for event in sorted(finals, key=lambda f: (str(f.get('kickoff_utc')), str(f.get('key')))):
        if not _evidence_integrity(event):
            raise RuntimeError('Final result record changed after Stage A validation')
        code = event.get('source_competition_id')
        when = utc(event.get('kickoff_utc'))
        if (code not in SUPPORTED or event.get('source') != 'ESPN'
            or event.get('phase') != 'FINAL'
            or event.get('key') != code + '|' + str(event.get('source_match_id'))
            or when <= frozen_last or when.floor('D') >= cutoff):
            rejected += 1
            continue
        if event['key'] in seen_source:
            raise RuntimeError('Duplicate source final result')
        seen_source.add(event['key'])
        try:
            home = _validate_proof(trusted_identity_resolver(event, 'home'), event, 'home')
            away = _validate_proof(trusted_identity_resolver(event, 'away'), event, 'away')
        except (LookupError, ValueError):
            rejected += 1
            continue
        league = str(home['historical_league_id'])
        if (league != str(away['historical_league_id'])
            or home['canonical_id'] == away['canonical_id']
            or home['historical_team_key'] == away['historical_team_key']
            or home['league_name'] != away['league_name']):
            rejected += 1
            continue
        for side, proof in (('home', home), ('away', away)):
            observed = (code, event[side + '_team_id'])
            identity = (league, str(proof['historical_team_key']), str(proof['canonical_id']))
            if observed in seen_clubs and seen_clubs[observed] != identity:
                raise RuntimeError('Provider team ID mapped to different historical teams')
            seen_clubs[observed] = identity
        row_id = 'gs_recent:ESPN:' + event['key']
        if row_id in base_ids:
            raise RuntimeError('Recent score duplicates an existing history row')
        base_ids.add(row_id)
        values = {col: np.nan for col in history_and_queries.columns}
        values.update({
            'match_id': row_id, 'date_utc': when,
            'league_id': league,
            'home_team_key': str(home['historical_team_key']),
            'away_team_key': str(away['historical_team_key']),
            'is_played': True,
            'home_goals': int(event['home_goals']),
            'away_goals': int(event['away_goals']),
            'league_name': home['league_name'],
            'home_team_name': home['historical_label'],
            'away_team_name': away['historical_label'],
        })
        new_rows.append(values)
    if new_rows:
        combined = pd.concat((history_and_queries,
                              pd.DataFrame(new_rows, columns=history_and_queries.columns)),
                             ignore_index=True)
    else:
        combined = history_and_queries.copy(deep=True)
    if len(combined) != len(history_and_queries) + len(new_rows):
        raise RuntimeError('Recent final result overlay lost feature rows')
    return combined, {
        'status': 'VERIFIED_RECENT_FINALS_OVERLAY' if new_rows else 'FROZEN_BASELINE_NO_ELIGIBLE_FINALS',
        'added': len(new_rows), 'examined': len(finals), 'rejected': rejected,
        'last_added_final_utc': max((str(x['date_utc']) for x in new_rows), default=None),
        'original_training_data_modified': False,
        'uses_original_feature_builder': True,
    }

def build_model_features(original_input, finals, provider_metadata,
                         *, kickoff_utc, history_last_utc,
                         trusted_identity_resolver, original_feature_builder):
    """Perform the actual feature-building call through an unchanged function."""
    if not callable(original_feature_builder):
        raise TypeError('Original model feature builder is required')
    augmented, audit = prepare_overlay(
        original_input, finals, provider_metadata, kickoff_utc=kickoff_utc,
        history_last_utc=history_last_utc,
        trusted_identity_resolver=trusted_identity_resolver)
    features = original_feature_builder(augmented)
    if not isinstance(features, pd.DataFrame):
        raise RuntimeError('Original feature builder returned invalid output')
    original_ids = set(original_input['match_id'].astype(str))
    if not original_ids.issubset(set(features['match_id'].astype(str))):
        raise RuntimeError('Original query/history rows missing after feature construction')
    for col in ('home_history_day', 'away_history_day', 'league_history_day'):
        if col in features.columns and 'match_day' in features.columns:
            older = features[col].isna() | (pd.to_datetime(features[col], utc=True)
                                           < pd.to_datetime(features['match_day'], utc=True))
            if not bool(older.all()):
                raise RuntimeError('Same-day or future information leaked into features: ' + col)
    return features, audit
