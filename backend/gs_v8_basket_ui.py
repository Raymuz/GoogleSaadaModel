"""Verified-provider, session-specific free bet basket for V8. No fake fixtures."""
from __future__ import annotations
import gradio as gr
from backend.gs_v8_live_bridge import fixtures_summary, forecast, PredictionAbstained
MAX_BASKET=16

def _snapshot():
    d=fixtures_summary()
    return {str(f['fixture_key']):f for f in d.get('fixtures',[]) if f.get('fixture_key')}

def load():
    try:
        rows=list(_snapshot().values())
        choices=[(f.get('home_name','')+' vs '+f.get('away_name','')+' · '+f.get('kickoff_utc',''), f['fixture_key']) for f in rows]
        return gr.update(choices=choices,value=None),f'**{len(choices)} upcoming verified fixtures available.**'
    except (PredictionAbstained,ValueError,OSError) as e:
        return gr.update(choices=[],value=None),'Fixture source unavailable: '+str(e)[:200]

def add(key,state):
    box=list(state or [])
    if not key or not isinstance(key,str):return box,f'**My Bet Basket: {len(box)}/16**','Select a fixture first.'
    if key in box:return box,f'**My Bet Basket: {len(box)}/16**','Fixture already in basket.'
    if len(box)>=MAX_BASKET:return box,'**My Bet Basket: 16/16**','Basket limit: 16 matches.'
    try: valid=_snapshot()
    except (PredictionAbstained,ValueError,OSError) as e:return box,f'**My Bet Basket: {len(box)}/16**','Provider unavailable: '+str(e)[:200]
    if key not in valid:return box,f'**My Bet Basket: {len(box)}/16**','Fixture is no longer source-verified.'
    box.append(key)
    return box,f'**My Bet Basket: {len(box)}/16**','Fixture added. Open My Bet Basket to review.'

def _render(box):
    try: known=_snapshot()
    except (PredictionAbstained,ValueError,OSError):known={}
    return [{'fixture_key':k,'match':known.get(k,{}).get('home_name','')+' vs '+known.get(k,{}).get('away_name',''),'kickoff_utc':known.get(k,{}).get('kickoff_utc','')} for k in box]

def _choices(box):
    rows=_render(box)
    return gr.update(choices=[(r['match'] or r['fixture_key'],r['fixture_key']) for r in rows],value=None)

def display(state):
    box=list(state or [])
    return _render(box),_choices(box),f'**{len(box)}/16 fixtures in basket.**'

def remove(key,state):
    box=[x for x in list(state or []) if x!=key] if key else list(state or [])
    return box,_render(box),_choices(box),f'**My Bet Basket: {len(box)}/16**','Basket updated.'

def clear():
    return [],[],gr.update(choices=[],value=None),'**My Bet Basket: 0/16**','Basket cleared.'

def analyze(state):
    box=list(state or [])
    if not box:return [],'Add a fixture before analyzing.'
    if len(box)>MAX_BASKET or len(set(box))!=len(box):return [],'Basket size or duplicate validation failed.'
    results=[];passed=0
    for key in box:
        try:
            real=forecast(key)
            results.append({'fixture_key':key,'status':'PREDICTED','result':real})
            passed+=1
        except PredictionAbstained as e:
            results.append({'fixture_key':key,'status':'ABSTAIN','reason':str(e)[:200]})
        except (ValueError,KeyError) as e:
            results.append({'fixture_key':key,'status':'ERROR','reason':type(e).__name__})
    return results,f'**{passed}/{len(box)} verified model predictions; {len(box)-passed} abstained/unavailable.** Predictions are uncertain.'
