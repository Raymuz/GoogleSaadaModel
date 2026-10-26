"""GoogleSaada V9 Phase D: task-aware, evidence-grounded match reasoning.
Pure Python. No model inference, invented data, odds, or unverified real-world claims.
"""
from __future__ import annotations
from collections import defaultdict
import math


def task_group(task):
    s = str(task or '').lower().replace('-', '_').replace(' ', '_')
    if any(x in s for x in ('ht_home', 'halftime_home', 'half_time_home')): return 'ht_home'
    if any(x in s for x in ('ht_away', 'halftime_away', 'half_time_away')): return 'ht_away'
    if any(x in s for x in ('sh_home', 'second_half_home')): return 'sh_home'
    if any(x in s for x in ('sh_away', 'second_half_away')): return 'sh_away'
    if any(x in s for x in ('ft_home', 'full_time_home')): return 'ft_home'
    if any(x in s for x in ('ft_away', 'full_time_away')): return 'ft_away'
    if 'btts' in s or 'both_teams' in s: return 'btts'
    if any(x in s for x in ('over_2_5', 'over2_5', 'over25')): return 'over_2_5'
    if any(x in s for x in ('match_result', '1x2', 'outcome', 'winner')): return 'match_result'
    return 'unclassified'


def _scalar(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(float(x))


def _safe_probs(item):
    p = item.get('probabilities')
    if not isinstance(p, dict) or not p: return None
    if not all(_scalar(v) and 0 <= float(v) <= 1 for v in p.values()): return None
    if abs(sum(p.values()) - 1) > 1e-4: return None
    return {str(k): float(v) for k, v in p.items()}


def analyze_match(predictions, context=None, *, contract_lookup=None):
    """Explain existing valid predictions; never run missing specialists or fabricate facts.

    Same-task peer opinions are compared but not blended unless separately validated.
    Context is caller supplied, clearly labeled and not independently verified.
    """
    context = context if isinstance(context, dict) else {}
    contract_lookup = contract_lookup or {}
    groups = defaultdict(list)
    unsupported = []
    for p in predictions:
        mid = str(p.get('model_id', ''))
        c = contract_lookup.get(mid, {})
        group = task_group(c.get('task') or p.get('task'))
        if group == 'unclassified':
            unsupported.append(mid)
        else:
            groups[group].append(p)
    observations, disagreements = [], []
    scalar_values = {}
    for group, students in sorted(groups.items()):
        values = [(p['model_id'], float(p['value'])) for p in students if _scalar(p.get('value'))]
        probs = [(p['model_id'], _safe_probs(p)) for p in students]
        probs = [(mid, val) for mid, val in probs if val]
        if values:
            vals = [v for _, v in values]
            entry = {'task': group, 'models': [mid for mid,_ in values], 'values': dict(values), 'range': [min(vals), max(vals)]}
            # No implicit unweighted avg; these may be on different calibrated scales.
            observations.append(entry)
            if len(vals) > 1 and max(vals)-min(vals) > 0.5:
                disagreements.append({'task':group, 'kind':'numeric_spread','spread':round(max(vals)-min(vals), 5), 'model_ids':entry['models']})
            if len(vals) == 1: scalar_values[group] = vals[0]
        if probs:
            entry = {'task': group, 'models':[mid for mid,_ in probs], 'probabilities_by_model': dict(probs)}
            observations.append(entry)
            if len(probs) > 1:
                label_sets = {tuple(sorted(p)) for _, p in probs}
                if len(label_sets) > 1:
                    disagreements.append({'task': group, 'kind':'incompatible_class_labels', 'model_ids':entry['models']})
                else:
                    favored = {mid:max(pr, key=pr.get) for mid, pr in probs}
                    if len(set(favored.values())) > 1:
                        disagreements.append({'task':group, 'kind':'different_favored_outcomes', 'favorites':favored})
    findings = []
    if all(k in scalar_values for k in ('ft_home','ft_away')):
        home, away = scalar_values['ft_home'], scalar_values['ft_away']
        findings.append({'type':'expected_goals', 'home':round(home, 3), 'away':round(away, 3),
                         'goal_difference':round(home-away, 3), 'interpretation':(
                             'Home expected goals are higher' if home>away else
                             'Away expected goals are higher' if home<away else 'Expected goals are equal')})
    if all(k in scalar_values for k in ('ht_home','ht_away')):
        findings.append({'type':'first_half_expected_goals', 'home':round(scalar_values['ht_home'],3),
                         'away':round(scalar_values['ht_away'],3)})
    if all(k in scalar_values for k in ('sh_home','sh_away')):
        findings.append({'type':'second_half_expected_goals', 'home':round(scalar_values['sh_home'],3),
                         'away':round(scalar_values['sh_away'],3)})
    if 'ft_home' in scalar_values and 'ht_home' in scalar_values and 'sh_home' in scalar_values:
        delta = scalar_values['ft_home']-scalar_values['ht_home']-scalar_values['sh_home']
        if abs(delta) > .25: disagreements.append({'task':'home_goals_consistency', 'kind':'cross_period_prediction_mismatch', 'difference':round(delta,3)})
    if 'ft_away' in scalar_values and 'ht_away' in scalar_values and 'sh_away' in scalar_values:
        delta = scalar_values['ft_away']-scalar_values['ht_away']-scalar_values['sh_away']
        if abs(delta) > .25: disagreements.append({'task':'away_goals_consistency', 'kind':'cross_period_prediction_mismatch', 'difference':round(delta,3)})
    narrative = []
    for f in findings:
        if f['type']=='expected_goals': narrative.append(f"Projected full-time goals: home {f['home']}, away {f['away']}. {f['interpretation']}.")
        elif f['type']=='first_half_expected_goals': narrative.append(f"Projected first-half goals: home {f['home']}, away {f['away']}.")
        elif f['type']=='second_half_expected_goals': narrative.append(f"Projected second-half goals: home {f['home']}, away {f['away']}.")
    if disagreements: narrative.append('Some specialists disagree or have inconsistent projections; review their separate outputs instead of averaging them.')
    if not narrative: narrative.append('The available models produced task-specific predictions; insufficient compatible evidence exists for a combined match conclusion.')
    # Do not repeat free text as established fact. Only expose a small whitelist of identifying context.
    labels = {k: str(v)[:120] for k,v in context.items() if k in ('home_team','away_team','league','competition','kickoff') and isinstance(v,(str,int,float))}
    return {'method':'contract_aware_peer_review_v1', 'fixture_context_unverified':labels,
            'narrative':' '.join(narrative), 'derived_findings':findings, 'model_evidence':observations,
            'peer_disagreements':disagreements, 'unclassified_model_ids':unsupported,
            'decision_policy':'No automatic consensus promotion or cross-task probability blending.',
            'confidence':'not_calibrated_for_joint_reasoning',
            'limitations':['Model predictions are estimates, not guarantees.',
                           'Fixture context is user-supplied and not independently verified.',
                           'No injuries, news, live data, bookmaker prices, or team form are invented.',
                           'Per-task performance and a validated model-selection policy are required before automated betting or recommendations.']}
