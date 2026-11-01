"""Soccer AI research-only orchestration adapter.

Inputs are saved, checksum-pinned Step12C.1 research records, verified by the
Step12C.2 policy gate freshly per request. NO inference and NO GPT calls.
User text is an untrusted selection request, not model evidence/instructions.
"""
from datetime import datetime, timezone, timedelta
import re
import unicodedata

PROFILES = ('Low','Medium','High','Balanced')
SUPPORTED_MODES = ('recommend','my_matches','assistant')
MARKET_ORDER = ('FT_1X2_HOME','FT_1X2_DRAW','FT_1X2_AWAY',
                'FT_OVER_2_5','FT_UNDER_2_5','FT_BTTS_YES','FT_BTTS_NO')
PRODUCTION_BLOCKS = (
    'Club identity proposals not globally approved',
    'Historical match data not refreshed and stale for production',
    'No source-verified chronological calibration for the seven markets',
)


def _norm(txt):
    txt=unicodedata.normalize('NFKD',str(txt))
    txt=''.join(c for c in txt if not unicodedata.combining(c))
    return re.sub(r'[^a-z0-9]+',' ',txt.casefold()).strip()


def _utc(txt):
    if not isinstance(txt,str):
        raise ValueError('Timestamp must be ISO text')
    dt=datetime.fromisoformat(txt.replace('Z','+00:00'))
    if dt.tzinfo is None:
        raise ValueError('Fixture timestamp needs UTC offset')
    return dt.astimezone(timezone.utc)


def _strict_date(value):
    if value is None or value=='':
        return None
    if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):
        raise ValueError('date_utc must be YYYY-MM-DD')
    return datetime.strptime(value,'%Y-%m-%d').date()


def _strict_count(n):
    if type(n) is not int or not 1<=n<=5:
        raise ValueError('max_matches must be an integer from 1 to 5')
    return n


def _parse_pairs(text):
    if not text:
        return []
    if not isinstance(text,str) or len(text)>1500:
        raise ValueError('matches_text must be text of at most 1500 characters')
    lines=[line.strip() for line in text.splitlines() if line.strip()]
    if len(lines)>12:
        raise ValueError('At most 12 match lines supported per request')
    pairs=[]
    for line in lines:
        match=re.fullmatch(r'(.{2,100}?)\s+(?:vs\.?|versus|v\.)\s+(.{2,100}?)',line,flags=re.I)
        if not match:
            raise ValueError('Use one exact source matchup per line: Home vs Away')
        h,a=(_norm(match.group(1)),_norm(match.group(2)))
        if not h or not a or h==a:
            raise ValueError('Invalid supplied matchup')
        pairs.append((h,a))
    return pairs


def _parse_assistant_mode(prompt):
    if not isinstance(prompt,str) or not prompt.strip() or len(prompt)>1000:
        raise ValueError('Assistant prompt must contain 1–1000 characters')
    p=prompt.casefold()
    if any(word in p for word in ('my matches','these matches','selected matches')):
        return 'my_matches'
    if any(word in p for word in ('recommend','fixtures','games','matches','prediction','predict','research')):
        return 'recommend'
    return None


def _parse_assistant_date(prompt,now_utc):
    p=prompt.casefold()
    today='today' in p
    tomorrow='tomorrow' in p
    if today and tomorrow:
        raise ValueError('Assistant date contains both today and tomorrow')
    explicit=re.findall(r'(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)',p)
    if len(set(explicit))>1 or (explicit and (today or tomorrow)):
        raise ValueError('Conflicting request dates; use date_utc explicitly')
    if explicit:
        return _strict_date(explicit[0])
    if tomorrow:
        return now_utc.date()+timedelta(days=1)
    if today:
        return now_utc.date()
    return None


def orchestrate(request,router_payload,policy_module,now_utc):
    """Return a new strictly research-only UI contract from verified stored data.

    Validates stored market candidates and risk previews using policy_module;
    rejects unsupported league selection instead of silently ignoring it.
    """
    if not isinstance(request,dict):
        raise ValueError('Request must be a dictionary')
    if not isinstance(now_utc,datetime) or now_utc.tzinfo is None:
        raise ValueError('now_utc must be timezone-aware')
    now_utc=now_utc.astimezone(timezone.utc)
    approved_fields={'mode','profile','max_matches','fixture_ids','matches_text',
                     'prompt','date_utc','league_ids'}
    unknown=set(request)-approved_fields
    if unknown:
        raise ValueError('Unsupported input field(s): '+','.join(sorted(unknown)))
    if request.get('league_ids') not in (None,[],()):
        return _held('HOLD_LEAGUE_FILTER_NO_VERIFIED_SOURCE_LEAGUE_ID',
                     'League filtering is not enabled for this verified research snapshot.',now_utc)
    mode=request.get('mode','recommend')
    if mode not in SUPPORTED_MODES:
        raise ValueError('mode must be recommend, my_matches, or assistant')
    max_n=_strict_count(request.get('max_matches',5))
    date_filter=_strict_date(request.get('date_utc'))
    profile=request.get('profile','Balanced')
    if profile not in PROFILES:
        raise ValueError('Unknown risk profile')
    if mode=='assistant':
        prompt=request.get('prompt','')
        parsed_mode=_parse_assistant_mode(prompt)
        if parsed_mode is None:
            return _held('NEEDS_CLARIFICATION','Ask for recommendations or select matches explicitly.',now_utc)
        mode=parsed_mode
        inferred_date=_parse_assistant_date(prompt,now_utc)
        if date_filter is not None and inferred_date is not None and date_filter!=inferred_date:
            raise ValueError('Explicit date conflicts with assistant text date')
        date_filter=date_filter or inferred_date
        if mode=='my_matches' and not request.get('fixture_ids') and not request.get('matches_text'):
            return _held('NEEDS_VERIFIED_MATCH_SELECTION','Choose saved fixture IDs or supply Home vs Away lines.',now_utc)
    gate=policy_module.verify_and_summarize(router_payload,now_utc,max_matches=5)
    if gate.get('status')!='PASS_PRIVATE_RESEARCH_ROUTING_WITH_PRODUCTION_BLOCKS':
        raise ValueError('Research policy gate did not pass')
    if (gate.get('public_recommendations')!=[] or gate.get('betting_picks')!=[]
            or gate.get('market_specific_calibration_verified') is not False
            or gate.get('identity_approvals') is not False
            or gate.get('historical_results_refreshed') is not False
            or gate.get('production_prediction_approved') is not False):
        raise ValueError('Production safety flags are inconsistent')
    items=router_payload['research_market_catalog_by_fixture']
    future={fid:row for fid,row in items.items()
            if row['fixture_status']=='PREMATCH_UNAPPROVED_FROZEN_HISTORY_RESEARCH'
            and _utc(row['kickoff_utc'])>now_utc}

    def permitted(fid):
        return fid in future and (date_filter is None or _utc(future[fid]['kickoff_utc']).date()==date_filter)

    selected=[]
    holds=[]
    if mode=='recommend':
        options=gate['private_research_previews'][profile]
        # Gate-produced shortlist is capped at five and never invents entries.
        for item in options:
            fid=item['match_id']
            if not permitted(fid):
                continue
            selected.append({
                'fixture_id':fid,'home':item['home'],'away':item['away'],
                'kickoff_utc':item['kickoff_utc'],
                'research_hypothesis':item['research_hypothesis'],
                'market_code':item['research_market'],
                'raw_uncalibrated_probability':item['uncalibrated_model_probability'],
                'research_only':True,'betting_pick':False,
                'identity_approved':False,'market_calibrated':False,
            })
        selected=selected[:max_n]
    else:
        ids=request.get('fixture_ids',[])
        if not isinstance(ids,list) or len(ids)>12 or not all(isinstance(x,str) and x for x in ids):
            raise ValueError('fixture_ids must be a list of at most 12 source IDs')
        if len(set(ids))!=len(ids):
            raise ValueError('Repeated fixture IDs are not accepted')
        pair_index={}
        for fid,row in future.items():
            pair=(_norm(row['provider_home']),_norm(row['provider_away']))
            pair_index.setdefault(pair,[]).append(fid)
        requested=list(ids)
        for h,a in _parse_pairs(request.get('matches_text','')):
            matches=[fid for fid in pair_index.get((h,a),[]) if permitted(fid)]
            if len(matches)==1:
                requested.append(matches[0])
            else:
                holds.append({'source_matchup':f'{h} vs {a}',
                              'status':'HOLD_NO_EXACT_OR_AMBIGUOUS_UPCOMING_MATCH'})
        if len(set(requested))!=len(requested):
            raise ValueError('A selected fixture occurs more than once')
        for fid in requested:
            if len(selected)>=max_n:
                holds.append({'fixture_id':fid,'status':'HOLD_REQUEST_COUNT_LIMIT'})
                continue
            if not permitted(fid):
                holds.append({'fixture_id':fid,'status':'HOLD_NOT_CURRENT_OR_NOT_IN_SOURCE'})
                continue
            row=future[fid]
            markets={v['market_code']:{
                'outcome':v['outcome'],
                'raw_uncalibrated_probability':v['raw_model_probability'],
                'evidence_method':v['evidence_method']}
                for v in row['market_candidates']}
            if set(markets)!=set(MARKET_ORDER):
                raise ValueError('Incomplete seven-market match record')
            selected.append({'fixture_id':fid,'home':row['provider_home'],
                             'away':row['provider_away'],'kickoff_utc':row['kickoff_utc'],
                             'markets':markets,'research_only':True,
                             'betting_pick':False,'identity_approved':False,
                             'market_calibrated':False})
    result={
        'status':'PRIVATE_RESEARCH_RESULTS_ONLY' if selected else 'NO_VERIFIED_RESEARCH_RESULTS_FOR_REQUEST',
        'mode':mode,'risk_profile':profile,'source_risk_threshold':router_payload['risk_profiles'][profile],
        'request_date_utc':str(date_filter) if date_filter else None,
        'evaluated_fixture_count':len(items),'currently_upcoming_source_fixtures':len(future),
        'results':selected,'held_requests':holds,
        'source_history_age_days':gate['history_age_days'],
        'market_specific_calibration_status':gate['calibration_gates_by_market'],
        'research_only':True,'public_recommendations':[],
        'published_betting_picks':[],'production_predictions_enabled':False,
        'gpt_output_publication_authorized':False,
        'source_models_rerun':False,
        'constraints':list(PRODUCTION_BLOCKS),
        'as_of_utc':now_utc.isoformat(),
    }
    return result


def _held(status,reason,now_utc):
    return {'status':status,'message':reason,'mode':None,'results':[],
            'held_requests':[],'research_only':True,'public_recommendations':[],
            'published_betting_picks':[], 'production_predictions_enabled':False,
            'gpt_output_publication_authorized':False,
            'as_of_utc':now_utc.isoformat(),
            'constraints':list(PRODUCTION_BLOCKS)}


def safe_explanation_input(result):
    """Only whitelisted, untrusted-data-free fields leave orchestration.

    GPT may explain these model observations after an explicit adapter is built,
    but the adapter MUST NOT edit numeric results or publication status.
    No credentials, raw user prompts, prompt-file contents or source files.
    """
    if (not isinstance(result,dict) or result.get('research_only') is not True
            or result.get('production_predictions_enabled') is not False
            or result.get('public_recommendations')!=[]
            or result.get('published_betting_picks')!=[]):
        raise ValueError('Refusing unsafe GPT explanation envelope')
    safe=[]
    for row in result.get('results',[]):
        if (row.get('research_only') is not True or row.get('betting_pick') is not False
                or row.get('market_calibrated') is not False
                or row.get('identity_approved') is not False):
            raise ValueError('Unsafe row blocked from GPT context')
        r={k:row[k] for k in ('fixture_id','home','away','kickoff_utc')}
        if 'raw_uncalibrated_probability' in row:
            r.update(market_code=row['market_code'],
                     raw_uncalibrated_probability=row['raw_uncalibrated_probability'])
        else:
            r['markets']=row['markets']
        safe.append(r)
    return {'scope':'EXPLANATION_ONLY_NOT_RECOMMENDATIONS',
            'immutable_publication_gate':False,
            'research_only':True,'as_of_utc':result['as_of_utc'],
            'history_age_days':result.get('source_history_age_days'),
            'models_are_uncalibrated_for_these_markets':True,
            'source_backed_rows':safe,
            'do_not_infer_missing_fixtures_or_statistics':True,
            'model_may_not_create_or_change_probabilities':True}
