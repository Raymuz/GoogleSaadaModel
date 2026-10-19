"""Soccer AI: source-preserving OFFLINE explanatory renderer.

NO API calls. This module never authorizes production, calibrated probabilities,
or betting picks; it only renders a fixed format from the saved Step12D.1 contract.
"""
from datetime import datetime, timezone
import math

MARKET_LABELS = {
    'FT_1X2_HOME': 'Home win',
    'FT_1X2_DRAW': 'Draw',
    'FT_1X2_AWAY': 'Away win',
    'FT_OVER_2_5': 'Over 2.5 total goals',
    'FT_UNDER_2_5': 'Under 2.5 total goals',
    'FT_BTTS_YES': 'Both teams to score: Yes',
    'FT_BTTS_NO': 'Both teams to score: No',
}
BLOCK_NOTICE = ('PRIVATE RESEARCH ONLY — These are uncalibrated model outputs from '
                'frozen historical data; club mappings remain unapproved. '
                'Not a betting recommendation or a production prediction.')


def _probability(value):
    if isinstance(value,bool) or not isinstance(value,(float,int)):
        raise ValueError('Probability must be a number')
    v=float(value)
    if not math.isfinite(v) or not 0.0 <= v <= 1.0:
        raise ValueError('Invalid probability')
    return v


def _safe_text(value):
    if not isinstance(value,str) or not value.strip() or len(value)>170:
        raise ValueError('Invalid display text')
    if any(ord(ch)<32 for ch in value):
        raise ValueError('Unexpected control character in display text')
    return value.strip()


def _time(value):
    if not isinstance(value,str):
        raise ValueError('Missing UTC kickoff')
    dt=datetime.fromisoformat(value.replace('Z','+00:00'))
    if dt.tzinfo is None:
        raise ValueError('Kickoff must be timezone-aware')
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')


def build_offline_explanation(orch_result,orchestrator_module):
    """Use the original orchestrator's strict whitelist and nothing else."""
    if not isinstance(orch_result,dict):
        raise ValueError('Missing orchestration result')
    if (orch_result.get('production_predictions_enabled') is not False or
        orch_result.get('public_recommendations') != [] or
        orch_result.get('published_betting_picks') != [] or
        orch_result.get('research_only') is not True or
        orch_result.get('gpt_output_publication_authorized') is not False):
        raise ValueError('Orchestration publication safety flags inconsistent')
    envelope=orchestrator_module.safe_explanation_input(orch_result)
    if (envelope.get('scope')!='EXPLANATION_ONLY_NOT_RECOMMENDATIONS' or
        envelope.get('immutable_publication_gate') is not False or
        envelope.get('research_only') is not True or
        envelope.get('models_are_uncalibrated_for_these_markets') is not True or
        envelope.get('model_may_not_create_or_change_probabilities') is not True):
        raise ValueError('Unsafe explanation context')
    rows=envelope.get('source_backed_rows')
    if not isinstance(rows,list) or len(rows)>5:
        raise ValueError('Expected zero to five permitted research rows')
    output=[]
    for row in rows:
        fid=_safe_text(row['fixture_id'])
        home=_safe_text(row['home'])
        away=_safe_text(row['away'])
        kickoff=_time(row['kickoff_utc'])
        heading=f'{home} vs {away} | {kickoff} | fixture ID: {fid}'
        if 'market_code' in row:
            code=row['market_code']
            if code not in MARKET_LABELS:
                raise ValueError('Unknown research market')
            probability=_probability(row['raw_uncalibrated_probability'])
            detail=f'{MARKET_LABELS[code]} — uncalibrated model probability {probability:.1%}'
        else:
            markets=row.get('markets')
            if not isinstance(markets,dict) or set(markets)!=set(MARKET_LABELS):
                raise ValueError('Manual fixture lacks exactly seven original markets')
            details=[]
            for code,label in MARKET_LABELS.items():
                item=markets[code]
                if not isinstance(item,dict):
                    raise ValueError('Invalid market record')
                p=_probability(item['raw_uncalibrated_probability'])
                details.append(f'{label} {p:.1%}')
            detail='Seven uncalibrated research markets: '+', '.join(details)
        output.append({'fixture_id':fid,'explanation':heading+'\n'+detail+'\n'+BLOCK_NOTICE,
                       'source_backed_research_only':True,
                       'betting_pick':False,'published':False})
    return {'status':'PASS_DETERMINISTIC_PRIVATE_RESEARCH_EXPLANATIONS',
            'mode':orch_result.get('mode'), 'risk_profile':orch_result.get('risk_profile'),
            'explanation_count':len(output),'results':output,
            'research_only':True,'public_recommendations':[],
            'published_betting_picks':[], 'production_predictions_enabled':False,
            'gpt_calls':0, 'gpt_live_explanations_enabled':False,
            'source_as_of_utc':envelope['as_of_utc'],
            'warning':BLOCK_NOTICE}


def guard_gpt_prompt_provenance(prompt_candidate_manifest):
    """No GPT authorization merely because plausible original prompts were found."""
    if not isinstance(prompt_candidate_manifest,dict):
        return {'status':'HOLD_ORIGINAL_PROMPT_PROVENANCE_NOT_APPROVED',
                'gpt_execution_allowed':False}
    # Promotion must occur through a separately reviewed, explicit approval,
    # not editable fields in this discovery report.
    return {'status':'HOLD_ORIGINAL_PROMPT_PROVENANCE_NOT_APPROVED',
            'gpt_execution_allowed':False}
