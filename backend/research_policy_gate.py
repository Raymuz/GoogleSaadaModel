"""Fail-closed research-only consumer safety contract for Soccer AI.

This module intentionally DOES NOT certify market-specific calibration, identity
approval, current form or publication. It validates and safely summarizes existing
Step 12C.1 research outputs; never accepts fabricated GPT model probabilities.
"""
from datetime import datetime, timezone
import math

MARKETS = ('FT_1X2_HOME', 'FT_1X2_DRAW', 'FT_1X2_AWAY',
           'FT_OVER_2_5', 'FT_UNDER_2_5', 'FT_BTTS_YES', 'FT_BTTS_NO')
PROFILES = ('Low', 'Medium', 'High', 'Balanced')
VALID_STATUS = 'PREMATCH_UNAPPROVED_FROZEN_HISTORY_RESEARCH'
CALIBRATION_HOLD = 'HOLD_NO_VERIFIED_MARKET_SPECIFIC_CHRONOLOGICAL_CALIBRATION'
HISTORY_MAX_AGE_DAYS = 7  # research display freshness warning, not an approval policy


def _decimal(x, label):
    if isinstance(x, bool) or not isinstance(x, (int,float)) or not math.isfinite(float(x)):
        raise ValueError('Not a finite numeric value: '+label)
    return float(x)


def _prob(x, label):
    value = _decimal(x, label)
    if value < 0 or value > 1:
        raise ValueError('Probability outside [0,1]: '+label)
    return value


def _utc(value, label):
    if not isinstance(value,str):
        raise ValueError('Timestamp not a string: '+label)
    try:
        stamp = datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError as e:
        raise ValueError('Invalid timestamp: '+label) from e
    if stamp.tzinfo is None:
        raise ValueError('Naive timestamp not permitted: '+label)
    return stamp.astimezone(timezone.utc)


def _assert_close(a,b,label,tol=1e-7):
    if abs(a-b) > tol:
        raise ValueError('Inconsistent probabilities: '+label)


def verify_and_summarize(payload, now_utc, max_matches=5):
    """Verify Step 12C.1 unaltered semantic contract and return safe UI state.

    Raises on corrupted/unauthorized data. Outputs only private research preview,
    never an authorization for publicly actionable betting recommendations.
    """
    if not isinstance(now_utc,datetime) or now_utc.tzinfo is None:
        raise ValueError('now_utc must be timezone-aware')
    now_utc = now_utc.astimezone(timezone.utc)
    if type(max_matches) is not int or not 1 <= max_matches <= 25:
        raise ValueError('max_matches must be 1..25')
    if not isinstance(payload,dict):
        raise ValueError('Invalid router payload')
    expected = {
        'step':'12C.1', 'research_only':True,
        'production_predictions_enabled':False,
        'identity_approvals':False,'historical_results_refreshed':False,
        'market_specific_holdout_calibration_verified':False,
        'model_predictions_reexecuted':False,
    }
    for key,value in expected.items():
        if payload.get(key) != value or type(payload.get(key)) is not type(value):
            raise ValueError('Research-only source contract changed: '+key)
    if payload.get('production_recommendations') != [] or payload.get('published_betting_picks') != []:
        raise ValueError('Upstream contains unauthorized public recommendations')
    if payload.get('verified_original_label_map') != {'0':'AWAY','1':'DRAW','2':'HOME'}:
        raise ValueError('Unproven classifier class semantics')
    if payload.get('model_ensemble_source') != 'EXPLICIT_EXPORT_MANIFEST_WEIGHTS':
        raise ValueError('Unverified ensemble provenance')
    if payload.get('risk_profile_origin') != 'SAVED_STEP10D_RISK_PROFILES_PARSED':
        raise ValueError('Risk policy not confirmed from saved settings')
    profiles = payload.get('risk_profiles')
    saved_routes = payload.get('risk_profile_previews')
    items = payload.get('research_market_catalog_by_fixture')
    if not isinstance(profiles,dict) or set(profiles)!=set(PROFILES):
        raise ValueError('Missing original risk levels')
    if not isinstance(saved_routes,dict) or set(saved_routes)!=set(PROFILES):
        raise ValueError('Missing saved route profiles')
    if not isinstance(items,dict) or not items:
        raise ValueError('Missing research market catalog')
    for risk,p in profiles.items():
        threshold = _prob(p,'profile.'+risk)
        if not .5 <= threshold <= .99:
            raise ValueError('Unexpected research threshold: '+risk)

    history_end = _utc(payload.get('frozen_history_latest_completed_utc'), 'history_end')
    if history_end > now_utc:
        raise ValueError('History end occurs in the future')
    history_age = (now_utc-history_end).total_seconds()/86400
    history_stale = history_age > HISTORY_MAX_AGE_DAYS
    fixture_statuses = {}
    recorded_kicks = {}
    future_available = set()
    for fid,record in items.items():
        if not isinstance(fid,str) or not fid.strip() or not isinstance(record,dict):
            raise ValueError('Invalid research fixture id or record')
        if record.get('research_feature_row_id') != fid:
            raise ValueError('Fixture id / source reference mismatch')
        for k in ('publishable','approved_identity','calibrated'):
            if record.get(k) is not False:
                raise ValueError('Unauthorized match authorization: '+k)
        status = record.get('fixture_status')
        if not isinstance(status,str):
            raise ValueError('Invalid research fixture status')
        fixture_statuses[fid] = status
        candidates = record.get('market_candidates')
        if status == VALID_STATUS:
            if not isinstance(candidates,list) or len(candidates)!=len(MARKETS):
                raise ValueError('Incomplete research market list: '+fid)
            by_code = {}
            for c in candidates:
                if not isinstance(c,dict):
                    raise ValueError('Invalid market entry')
                code = c.get('market_code')
                if code not in MARKETS or code in by_code:
                    raise ValueError('Duplicate/unsupported market: '+str(code))
                _prob(c.get('raw_model_probability'),'market.'+code)
                if c.get('recommended_for_betting') is not False or c.get('out_of_time_market_calibration_verified') is not False:
                    raise ValueError('Unauthorized market research status')
                if not isinstance(c.get('evidence_method'),str) or not c['evidence_method']:
                    raise ValueError('Missing source method')
                by_code[code] = c
            if set(by_code)!=set(MARKETS):
                raise ValueError('Missing expected markets')
            probs = {k: float(v['raw_model_probability']) for k,v in by_code.items()}
            _assert_close(sum(probs[k] for k in MARKETS[:3]),1.0,fid+'.1X2')
            _assert_close(probs['FT_OVER_2_5']+probs['FT_UNDER_2_5'],1.0,fid+'.totals')
            _assert_close(probs['FT_BTTS_YES']+probs['FT_BTTS_NO'],1.0,fid+'.btts')
            kickoff = _utc(record.get('kickoff_utc'), 'fixture kickoff')
            recorded_kicks[fid] = kickoff
            if kickoff > now_utc:
                future_available.add(fid)
        else:
            if candidates != []:
                raise ValueError('Held fixture has unexpected candidate markets')

    # Recompute every original risk list instead of trusting the saved preview.
    checked_routes = {}
    public_preview = {}
    for risk, threshold in profiles.items():
        route = saved_routes[risk]
        if not isinstance(route,dict) or route.get('profile') != risk:
            raise ValueError('Missing/inconsistent saved risk route')
        if route.get('status') != 'RESEARCH_PREVIEW_ONLY_NOT_PUBLICATION_AUTHORIZED':
            raise ValueError('Route not marked research-only')
        if route.get('public_recommendations') != [] or route.get('betting_picks') != []:
            raise ValueError('Route illegally publishes betting selections')
        _assert_close(_prob(route.get('provisional_threshold'),risk+'.stored_threshold'),float(threshold),risk+'.threshold')
        ranked = []
        for fid,record in items.items():
            if record['fixture_status'] != VALID_STATUS:
                continue
            qualifiers = [v for v in record['market_candidates']
                          if float(v['raw_model_probability']) >= float(threshold)]
            if not qualifiers:
                continue
            best = sorted(qualifiers,key=lambda v:(-float(v['raw_model_probability']),v['market_code']))[0]
            ranked.append((fid,best))
        ranked.sort(key=lambda pair:(-float(pair[1]['raw_model_probability']),pair[0],pair[1]['market_code']))
        entries = route.get('research_hypotheses')
        if not isinstance(entries,list) or len(entries)>5:
            raise ValueError('Invalid saved research ranking')
        if route.get('eligible_before_display_limit') != len(ranked):
            raise ValueError('Risk profile source count inconsistency')
        expected_ranked = ranked[:5]
        if len(entries) != len(expected_ranked):
            raise ValueError('Stored and recomputed previews disagree')
        safe_entries = []
        for entry,(fid,best) in zip(entries,expected_ranked):
            if not isinstance(entry,dict):
                raise ValueError('Invalid risk preview entry')
            if entry.get('research_feature_row_id') != fid or entry.get('market_code') != best['market_code']:
                raise ValueError('Risk preview chosen market/fixture differs from raw data')
            _assert_close(_prob(entry.get('uncalibrated_raw_model_probability'),'preview_probability'),
                          float(best['raw_model_probability']),'rank_probability')
            _assert_close(_prob(entry.get('provisional_threshold'),'preview_threshold'),float(threshold),'preview_threshold')
            mandatory = {'research_status':'THRESHOLD_MET_BUT_PUBLICATION_BLOCKED',
                         'market_specific_calibration_verified':False,
                         'club_identities_approved':False,
                         'historical_results_updated':False,
                         'public_recommendation':False,'betting_pick':False}
            for field,val in mandatory.items():
                if entry.get(field)!=val or type(entry.get(field)) is not type(val):
                    raise ValueError('Risk preview authorization changed: '+field)
            if fid in future_available:
                if not all(isinstance(entry.get(k),str) and entry[k].strip()
                           for k in ('provider_home','provider_away')):
                    raise ValueError('Missing provider fixture names')
                _utc(entry.get('kickoff_utc'),'preview kickoff')
                if _utc(entry['kickoff_utc'],'preview kickoff') != recorded_kicks[fid]:
                    raise ValueError('Risk preview kickoff conflict')
                safe_entries.append({
                    'match_id':fid, 'home':entry['provider_home'],
                    'away':entry['provider_away'],
                    'kickoff_utc':recorded_kicks[fid].isoformat(),
                    'research_market':best['market_code'],
                    'research_hypothesis':best['outcome'],
                    'uncalibrated_model_probability':float(best['raw_model_probability']),
                    'display_label':'UNCALIBRATED RESEARCH ONLY — NOT A BETTING PICK',
                    'publication_blocked':True,
                })
        checked_routes[risk] = len(expected_ranked)
        public_preview[risk] = safe_entries[:max_matches]
    return {
        'status':'PASS_PRIVATE_RESEARCH_ROUTING_WITH_PRODUCTION_BLOCKS',
        'research_only':True,
        'source_fixture_count':len(items),
        'source_fixture_statuses':fixture_statuses,
        'source_research_counts_by_profile':checked_routes,
        'current_future_fixture_count':len(future_available),
        'history_age_days':round(history_age,3),
        'history_stale_for_production':history_stale,
        'market_specific_calibration_verified':False,
        'calibration_gates_by_market':{k:CALIBRATION_HOLD for k in MARKETS},
        'private_research_previews':public_preview,
        'public_recommendations':[],
        'betting_picks':[],
        'production_prediction_approved':False,
        'gpt_public_prediction_allowed':False,
        'identity_approvals':False,
        'historical_results_refreshed':False,
        'reason':'Unapproved club identity; frozen/stale history; no verified out-of-time market-specific calibration',
    }
