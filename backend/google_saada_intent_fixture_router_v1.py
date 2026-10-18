"""GoogleSaada request router for source-proven upcoming soccer fixtures.

UI names/aliases match ONLY to server-fetched provider fixtures. This module
never maps fuzzy names to original training-history identities and never creates
probabilities. The original historical gate and quota service remain mandatory.
"""
from difflib import SequenceMatcher
import re, unicodedata

MARKETS = ('Full-time result','Exact score','Half-time / Full-time')
DISCLAIMER = ('MANDATORY DISCLAIMER: All predictions are for entertainment purposes only. '
              'GoogleSaada does not guarantee wins and is not responsible for any '
              'decisions made based on these predictions.')
_TRAINED_CODES=frozenset(('eng.1','esp.1','ger.1'))
_WORD_NUMBERS={'one':1,'two':2,'three':3,'four':4,'five':5,'six':6,'seven':7,'eight':8,
               'nine':9,'ten':10,'fifteen':15,'twenty':20}

def norm(name):
    s=unicodedata.normalize('NFKD', str(name or '').casefold())
    s=''.join(c for c in s if not unicodedata.combining(c))
    s=' '.join(re.findall(r'[a-z0-9]+',s))
    for old,new in ((r'\bman\s+(?:utd|united)\b','manchester united'),
                    (r'\bman\s+city\b','manchester city'),
                    (r'\butd\b','united'), (r'\bst\b','saint')):
        s=re.sub(old,new,s)
    return ' '.join(x for x in s.split() if x not in ('fc','afc','cf','sc'))

def similarity(user_name,source_name):
    a,b=norm(user_name),norm(source_name)
    if not a or not b or len(a)<5 or len(b)<5:return 0.0
    if a==b:return 1.0
    return SequenceMatcher(None,a,b).ratio()

def _names(fixture,side):
    # Caller supplies only server-fetched source fixtures, NEVER arbitrary user JSON.
    names={str(fixture.get(side,'')).strip()}
    variants=fixture.get('source_name_options')
    if isinstance(variants,dict) and isinstance(variants.get(side),list):
        for name in variants[side]:
            if isinstance(name,str) and 2<=len(name)<=100:names.add(name.strip())
    return {x for x in names if x}

def _trusted_fixture(fx):
    if not isinstance(fx,dict):return False
    code=str(fx.get('source_competition_id',''))
    mid=str(fx.get('source_match_id',''))
    home_id=str(fx.get('home_team_id',''))
    away_id=str(fx.get('away_team_id',''))
    return (fx.get('source')=='ESPN' and fx.get('phase')=='UPCOMING'
            and code in _TRAINED_CODES and bool(re.fullmatch(r'\d{3,20}',mid))
            and bool(re.fullmatch(r'\d{1,20}',home_id))
            and bool(re.fullmatch(r'\d{1,20}',away_id)) and home_id!=away_id
            and fx.get('key')==code+'|'+mid and bool(fx.get('home')) and bool(fx.get('away')))

def find_fixture(home,away,source_fixtures):
    """Returns (provider fixture or None, status); never guesses model identity."""
    if not isinstance(source_fixtures,(tuple,list)):
        raise TypeError('Fixtures must come from the trusted source schedule.')
    if not (3<=len(str(home).strip())<=100 and 3<=len(str(away).strip())<=100):
        return None,'unrecognized'
    options=[];seen=set()
    for fx in source_fixtures:
        if not _trusted_fixture(fx):continue
        scores=(max(similarity(home,n) for n in _names(fx,'home')),
                max(similarity(away,n) for n in _names(fx,'away')))
        if min(scores)<0.89:continue
        if fx['key'] in seen:continue
        seen.add(fx['key']);options.append((min(scores),fx))
    if not options:return None,'not_verified'
    # Do not silently choose between two provider fixture identities.
    if len(options)>1:return None,'ambiguous'
    return options[0][1],'verified_source_fixture'

def extract_pair(message):
    raw=str(message or '').strip()[:500]
    marker=re.compile(r'\s+(?:vs\.?|versus)\s+',re.I)
    chunks=marker.split(raw)
    if len(chunks)!=2:return None
    left,right=chunks
    # Handle instructions ending with 'for' or 'between' before first team.
    match=re.search(r'(?i)\b(?:for|between)\s+(.+)$',left)
    if match:left=match.group(1)
    left=re.sub(r'(?i)^(?:(?:please|can you|would you|give me|show me)\s+)*'
                r'(?:(?:predict|forecast|analyse|analyze|estimate|calculate|give|show)\s+)*'
                r'(?:(?:the|a)\s+)?(?:(?:exact|correct)\s+score|full[- ]time\s+(?:result|outcome)|'
                r'ht\s*/\s*ft|half[- ]time\s*/\s*full[- ]time)?\s*', '',left).strip()
    # Trailing market and punctuation are not part of the second club.
    right=re.sub(r'(?i)\s+(?:(?:exact|correct)\s+score|full[- ]time\s+(?:result|outcome)|'
                 r'ht\s*/\s*ft|half[- ]time\s*/\s*full[- ]time)(?:\s+prediction)?\s*[?.!]*$','',right).strip()
    left=left.strip(' .?!,:');right=right.strip(' .?!,:')
    if not left or not right:return None
    return left,right

def market_choices(question,selected=None):
    raw=str(question or '').casefold()
    found=[]
    if re.search(r'\b(?:full[- ]time(?:\s+(?:result|outcome|win|draw))?|1x2|match\s+winner)\b',raw):
        found.append('Full-time result')
    if re.search(r'\b(?:exact\s+score|correct\s+score|predicted\s+score(?:line)?|scoreline\s+prediction)\b',raw):
        found.append('Exact score')
    if re.search(r'\b(?:half[- ]time\s*/\s*full[- ]time|half[- ]time\s+full[- ]time|ht\s*/\s*ft|htft)\b',raw):
        found.append('Half-time / Full-time')
    if re.search(r'\b(?:all\s+(?:three|3)|every\s+(?:prediction\s+)?type)\b',raw):
        found=list(MARKETS)
    if found:return [k for k in MARKETS if k in found]
    selected=selected if isinstance(selected,(tuple,list)) else []
    valid=[m for m in MARKETS if m in selected]
    return valid or ['Full-time result']

def _requested_count(raw):
    match=re.search(r'\b(\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten|fifteen|twenty)\s+'
                    r'(?:matches|games|fixtures)\b',raw,re.I)
    if match:
        x=match.group(1).lower()
        return int(x) if x.isdigit() else _WORD_NUMBERS[x]
    return None

def route_request(question, selected_markets=None, source_fixtures=()):
    """Deterministic, side-effect-free preprocessor before quota/model/GPT paths."""
    raw=str(question or '').strip()
    if not raw or len(raw)>500:
        return {'action':'clarify','message':'Please enter a short match or prediction request.',
                'markets':[],'fixture_key':None,'requested_count':None}
    low=raw.casefold()
    # Block unsupported first-player scorer predictions BEFORE fixtures, quota,
    # model calculations or any external-language-model requests.
    goalscorer=bool(re.search(
        r'\b(?:'
        r'first\s+(?:player\s+to\s+score|goal\s*scorer|goalscorer|scorer)\b'
        r'|(?:who|which\s+player)\s+(?:(?:will|might|could)\s+|is\s+going\s+to\s+)?'
        r'(?:score|scores|net)\s+(?:the\s+)?(?:first|opening)(?:\s+goal)?\b'
        r'|(?:who|which\s+player)\s+(?:(?:will|might|could)\s+|is\s+going\s+to\s+)?'
        r'(?:score|scores|net)\s+first\b'
        r'|who\s+(?:(?:will|might|could)\s+)?open(?:s)?\s+the\s+scoring\b'
        r'|who\s+(?:is\s+|will\s+be\s+)?(?:the\s+)?first\s+(?:goalscorer|goal\s*scorer|scorer)\b'
        r'|opening\s+goal\s*scorer\b'
        r')', low))
    if goalscorer:
        return {'action':'unsupported_player_market','markets':[],'fixture_key':None,
                'requested_count':None,'message':'First-player-goalscorer forecasts need validated player data and a separately tested player model.'}
    predictive=bool(re.search(r'\b(?:predict|prediction|forecast|estimate|analy[sz]e|exact\s+score|correct\s+score|ht\s*/\s*ft|scoreline\s+prediction)\b',low))
    if re.search(r'\b(?:recommend|suggest|picks|top\s+\d+\s+matches)\b',low):
        return {'action':'recommendations','markets':market_choices(raw,selected_markets),
                'fixture_key':None,'requested_count':_requested_count(raw),'message':'Use verified model recommendations and the shared prediction quota.'}
    if not predictive and re.search(r'\b(?:live|current|ongoing|playing\s+now|latest\s+score|scores\s+today)\b',low):
        return {'action':'scores','markets':[],'fixture_key':None,
                'requested_count':None,'message':'Show provider-reported match scores.'}
    if re.search(r'\b(?:which\s+matches|what\s+matches)\b',low) or (not predictive and re.search(r'\b(?:fixtures|schedule|matches\s+today|playing\s+today)\b',low)):
        return {'action':'fixtures','markets':[],'fixture_key':None,
                'requested_count':None,'message':'Show available provider fixtures; do not spend prediction allowance.'}
    pair=extract_pair(raw)
    if pair is not None:
        verified,status=find_fixture(pair[0],pair[1],source_fixtures)
        if verified is None:
            return {'action':'clarify','markets':market_choices(raw,selected_markets),
                    'fixture_key':None,'requested_count':1,
                    'message':('Multiple current fixtures match this request. Please select the exact scheduled match.'
                               if status=='ambiguous' else
                               'I could not uniquely verify that upcoming fixture from the current source schedule.')}
        return {'action':'predict','markets':market_choices(raw,selected_markets),
                'fixture_key':verified['key'],'requested_count':1,
                'fixture_label':str(verified['home'])+' vs '+str(verified['away']),
                'message':'Check remaining shared quota, historical team identities and current model data before running prediction.'}
    if predictive:
        count=_requested_count(raw)
        if count is not None and count>0:
            return {'action':'predict_many','markets':market_choices(raw,selected_markets),
                    'fixture_key':None,'requested_count':count,
                    'message':'Discover current provider fixtures, validate each historical identity, and enforce the shared prediction allowance before inference.'}
        return {'action':'clarify','markets':market_choices(raw,selected_markets),
                'fixture_key':None,'requested_count':None,
                'message':'Please provide a scheduled fixture or choose a match from the fixture list.'}
    return {'action':'help','markets':market_choices(raw,selected_markets),
            'fixture_key':None,'requested_count':None,
            'message':'You can request fixtures, live scores, recommendations, or specific prediction markets.'}

def freshness_note(provenance):
    """Never claim that recent scores affected inference without source evidence."""
    if not isinstance(provenance,dict):return 'Recent-score model integration has not been verified for this prediction.'
    used=provenance.get('added',0)
    status=provenance.get('status')
    if status=='VERIFIED_RECENT_FINALS_OVERLAY' and isinstance(used,int) and used>0:
        return ('The feature builder incorporated '+str(used)+' verified completed matches '
                'from before the scheduled kickoff. The original trained models were not retrained. '
                'This does not guarantee improved prediction accuracy.')
    return ('Recent final scores did not pass every required check for this fixture; '
            'the original historical baseline was used.')
