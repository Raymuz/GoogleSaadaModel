"""Soccer AI research-risk router.

This module is a local, *non-production* scorer of already-validated research
probabilities. Its outputs are NOT calibrated betting probabilities, live picks,
recommendations or an authorization for user-facing publication. No I/O.
"""
from datetime import datetime, timezone
import math
import re

EXPECTED_PROFILES = ("Low", "Medium", "High", "Balanced")
DOCUMENTED_STEP10E_THRESHOLDS = {"Low": 0.80, "Medium": 0.70,
                                 "High": 0.60, "Balanced": 0.75}
ALLOWED_MARKETS = ("FT_1X2_HOME", "FT_1X2_DRAW", "FT_1X2_AWAY",
                   "FT_OVER_2_5", "FT_UNDER_2_5", "FT_BTTS_YES", "FT_BTTS_NO")


def _num(v, field, minv=0., maxv=1.):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
        raise ValueError("Non-finite or non-numeric " + field)
    value = float(v)
    if value < minv - 1e-10 or value > maxv + 1e-10:
        raise ValueError("Out-of-bounds " + field)
    return value


def _triple(obj, prefix):
    if not isinstance(obj, dict) or not all(k in obj for k in ('home_win', 'draw', 'away_win')):
        raise ValueError('Incomplete ' + prefix + ' 1X2 probability record')
    a = {k: _num(obj[k], prefix + '.' + k) for k in ('home_win', 'draw', 'away_win')}
    if abs(sum(a.values()) - 1.0) > 1e-6:
        raise ValueError('Invalid 1X2 probability mass: ' + prefix)
    return a


def _period(obj, prefix):
    a = _triple(obj, prefix)
    for x, y in (('over_2_5', 'under_2_5'), ('btts_yes', 'btts_no')):
        a[x] = _num(obj.get(x), prefix+'.'+x)
        a[y] = _num(obj.get(y), prefix+'.'+y)
        if abs(a[x]+a[y] - 1.0) > 1e-6:
            raise ValueError('Incorrect complementary market probabilities: '+prefix)
    if obj.get('independent_poisson_assumption') is not True:
        raise ValueError('Source did not mark independent Poisson assumption')
    return a


def _prob_threshold(raw):
    if isinstance(raw, str):
        raw = raw.strip().replace('%', '')
        try:
            raw = float(raw)
        except (TypeError, ValueError):
            raise ValueError('Unsupported threshold text')
    val = _num(raw, 'risk profile threshold', 0, 100)
    if val > 1:
        val /= 100
    if val < .5 or val > .99:
        raise ValueError('Risk profile threshold must be 50%–99% for this research selector')
    return val


def read_risk_profiles(payload):
    """Understand common Step 10D dictionary/list encodings; never invent settings.

    Returns (profile thresholds, status). If a config exists but its structure is
    unknown, return an explicit HOLD instead of overriding the user's settings.
    """
    if payload is None:
        return dict(DOCUMENTED_STEP10E_THRESHOLDS), 'DOCUMENTED_STEP10E_FALLBACK_NO_SAVED_FILE'
    if not isinstance(payload, dict):
        return None, 'HOLD_UNRECOGNIZED_SAVED_RISK_CONFIG'
    section = payload
    for container_key in ('risk_profiles', 'profiles', 'risk_levels', 'settings'):
        if isinstance(section.get(container_key), (dict, list)):
            section = section[container_key]
            break
    result = {}
    if isinstance(section, dict):
        items = list(section.items())
    elif isinstance(section, list):
        items = [(str(item.get('name', item.get('profile', item.get('risk_level', '')))), item)
                 for item in section if isinstance(item, dict)]
    else:
        return None, 'HOLD_UNRECOGNIZED_SAVED_RISK_CONFIG'
    canonical = {k.lower(): k for k in EXPECTED_PROFILES}
    for name, data in items:
        normal = re.sub(r'[^a-z]+', '', str(name).lower())
        profile = canonical.get(normal) or canonical.get(normal.replace('risk',''))
        if profile is None and isinstance(data, dict):
            alt = str(data.get('name', data.get('risk_profile', data.get('label', '')))).lower()
            profile = canonical.get(re.sub(r'[^a-z]+','',alt))
        if profile is None:
            continue
        if isinstance(data, dict):
            keys = ('min_probability_research', 'min_probability', 'minimum_probability', 'probability_threshold',
                    'min_confidence', 'confidence_threshold', 'min_confidence_score',
                    'min_probability_threshold', 'threshold', 'min_prob',
                    'confidence', 'minimum_confidence', 'minimum_probability_pct')
            matching = [data[k] for k in keys if k in data]
            if not matching:
                return None, 'HOLD_UNRECOGNIZED_SAVED_RISK_CONFIG_FIELDS'
            try:
                values = [_prob_threshold(t) for t in matching]
            except ValueError:
                return None, 'HOLD_INVALID_SAVED_RISK_THRESHOLD'
            if max(values) - min(values) > 1e-10:
                return None, 'HOLD_CONFLICTING_SAVED_RISK_THRESHOLDS'
            val = values[0]
        else:
            try:
                val = _prob_threshold(data)
            except ValueError:
                return None, 'HOLD_INVALID_SAVED_RISK_THRESHOLD'
        if profile in result and abs(result[profile]-val) > 1e-10:
            return None, 'HOLD_DUPLICATE_SAVED_RISK_PROFILE'
        result[profile] = val
    if set(result) != set(EXPECTED_PROFILES):
        return None, 'HOLD_MISSING_SAVED_RISK_PROFILES'
    return result, 'SAVED_STEP10D_RISK_PROFILES_PARSED'


def evaluate_match(match_id, provenance, audited, now_utc, weight_pair):
    """Do not accept standalone LLM/fabricated probabilities. Require lineage pairs."""
    result = {
        'research_feature_row_id': match_id,
        'fixture_status': 'HOLD_UNVERIFIED_RESEARCH_EVIDENCE',
        'reason': None, 'market_candidates': [], 'publishable': False,
        'approved_identity': False, 'calibrated': False,
    }
    if not isinstance(provenance, dict) or not isinstance(audited, dict):
        result['reason'] = 'SOURCE_EVIDENCE_NOT_PRESENT'
        return result
    for field in ('identities_approved', 'production_predictions_enabled',
                  'betting_picks_published'):
        if provenance.get(field) is not False:
            raise ValueError('Unexpected research / production authorization: '+field)
    if (audited.get('identity_approval') is not False
            or audited.get('historical_refresh_completed') is not False
            or audited.get('production_prediction_approved') is not False):
        raise ValueError('Unexpected original validation approvals')
    timestamp = provenance.get('kickoff_utc')
    try:
        kickoff = datetime.fromisoformat(str(timestamp).replace('Z', '+00:00'))
        if kickoff.tzinfo is None:
            raise ValueError('No kickoff timezone')
        kickoff = kickoff.astimezone(timezone.utc)
    except (ValueError, TypeError) as exc:
        raise ValueError('Invalid fixture source kickoff') from exc
    if kickoff.isoformat() != audited.get('fixture_kickoff_utc'):
        raise ValueError('Research and validation fixture kickoff conflict')
    result['kickoff_utc'] = kickoff.isoformat()
    if kickoff <= now_utc:
        result['fixture_status'] = 'HOLD_KICKOFF_PASSED'
        result['reason'] = 'Source kickoff is not in the future at routing time'
        return result
    if provenance.get('research_status') != 'PASS_MAPPED_RESEARCH_ONLY_UNVERIFIED_IDENTITIES_AND_FROZEN_HISTORY':
        result['reason'] = 'Original fitted class provenance or ensemble gate not passed'
        return result
    ensemble = _triple(provenance.get('research_only_ensemble_1x2'), 'mapped_ensemble')
    pois = _period(audited.get('independent_poisson_research_markets', {}).get('full_time'), 'full_time')
    orig_xgb = provenance.get('mapped_1x2_by_classifier', {}).get('full_time_xgboost')
    xgb = _triple(orig_xgb, 'xgboost')
    xw, pw = [_num(x, 'ensemble weight') for x in weight_pair]
    if abs(xw+pw-1) > 1e-9:
        raise ValueError('Invalid explicit source-model ensemble weight sum')
    for outcome in ('home_win', 'draw', 'away_win'):
        expected = xw*xgb[outcome] + pw*pois[outcome]
        if abs(expected-ensemble[outcome]) > 1e-7:
            raise ValueError('Mapped ensemble differs from source XGB+Poisson computation')
    types = (
        ('FT_1X2_HOME', 'Full-time home win', ensemble['home_win'], 'VERIFIED_CLASS_SEMANTICS_PLUS_RESEARCH_ENSEMBLE'),
        ('FT_1X2_DRAW', 'Full-time draw', ensemble['draw'], 'VERIFIED_CLASS_SEMANTICS_PLUS_RESEARCH_ENSEMBLE'),
        ('FT_1X2_AWAY', 'Full-time away win', ensemble['away_win'], 'VERIFIED_CLASS_SEMANTICS_PLUS_RESEARCH_ENSEMBLE'),
        ('FT_OVER_2_5', 'Full-time over 2.5 goals', pois['over_2_5'], 'INDEPENDENT_POISSON_RESEARCH'),
        ('FT_UNDER_2_5', 'Full-time under 2.5 goals', pois['under_2_5'], 'INDEPENDENT_POISSON_RESEARCH'),
        ('FT_BTTS_YES', 'Both teams score — yes', pois['btts_yes'], 'INDEPENDENT_POISSON_RESEARCH'),
        ('FT_BTTS_NO', 'Both teams score — no', pois['btts_no'], 'INDEPENDENT_POISSON_RESEARCH'),
    )
    result['market_candidates'] = [{
        'market_code': code, 'outcome': label, 'raw_model_probability': round(_num(p,code), 10),
        'evidence_method': method, 'out_of_time_market_calibration_verified': False,
        'recommended_for_betting': False,
    } for code,label,p,method in types]
    result['fixture_status'] = 'PREMATCH_UNAPPROVED_FROZEN_HISTORY_RESEARCH'
    result['reason'] = 'Original historical identity proposals unapproved; historical result refresh missing'
    return result


def route_profile(evaluations, risk_profiles, profile='Balanced', maximum=5):
    if risk_profiles is None:
        return {'profile': profile, 'status': 'HOLD_RISK_PROFILE_CONFIGURATION', 'research_hypotheses': []}
    if profile not in risk_profiles:
        raise ValueError('Unknown risk profile')
    if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < 1 or maximum > 25:
        raise ValueError('Research preview maximum must be 1–25')
    threshold = float(risk_profiles[profile])
    proposed, held = [], {}
    for entry in evaluations:
        fid = entry['research_feature_row_id']
        if entry['fixture_status'] != 'PREMATCH_UNAPPROVED_FROZEN_HISTORY_RESEARCH':
            held[fid] = entry['fixture_status']
            continue
        options = [x for x in entry['market_candidates'] if x['raw_model_probability'] >= threshold]
        if not options:
            held[fid] = 'BELOW_PROVISIONAL_RESEARCH_THRESHOLD'
            continue
        # One hypothesis per match; no forced diversification or invented games.
        best = sorted(options, key=lambda x: (-x['raw_model_probability'], x['market_code']))[0]
        proposed.append({
            'research_feature_row_id': fid,
            'market_code': best['market_code'], 'research_outcome': best['outcome'],
            'uncalibrated_raw_model_probability': best['raw_model_probability'],
            'provisional_threshold': threshold,
            'research_status': 'THRESHOLD_MET_BUT_PUBLICATION_BLOCKED',
            'market_specific_calibration_verified': False,
            'club_identities_approved': False,
            'historical_results_updated': False,
            'public_recommendation': False,
            'betting_pick': False,
        })
    proposed.sort(key=lambda x: (-x['uncalibrated_raw_model_probability'],
                                 x['research_feature_row_id'], x['market_code']))
    return {'profile': profile, 'status': 'RESEARCH_PREVIEW_ONLY_NOT_PUBLICATION_AUTHORIZED',
            'provisional_threshold': threshold,
            'eligible_before_display_limit': len(proposed),
            'research_hypotheses': proposed[:maximum],
            'held_fixture_statuses': held,
            'public_recommendations': [], 'betting_picks': []}
