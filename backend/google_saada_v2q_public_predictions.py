"""Public free predictions. No trusted login, billing or user-supplied model data.
The daily allowance is best-effort per signed BrowserState identity; not per human.
"""
from __future__ import annotations
from datetime import datetime,timezone,timedelta
import re

MARKETS={'Full-time result','Over/Under 2.5','Both Teams to Score'}
DISCLAIMER='Research probabilities are uncertain and never guarantee results.'

def handle(keys,raw,profile,day,league,zone,token,markets,*,week,old,ledger,engine_factory=None):
    uid,signed=ledger.identity(token)
    def balance():return f'**Free daily allowance: {ledger.remaining(uid)}/20 match analyses remaining (UTC).** Best-effort browser limit.'
    def reject(msg):return msg,[],DISCLAIMER+' No prediction was charged.',signed,balance()
    try:
        if not isinstance(keys,(list,tuple)) or len(keys)>16 or len(str(raw or ''))>2500:
            raise ValueError('Choose no more than 16 matches and 2,500 input characters')
        chosen_markets=list(markets) if isinstance(markets,(list,tuple)) else []
        if not chosen_markets or len(set(chosen_markets))!=len(chosen_markets) or not set(chosen_markets)<=MARKETS:
            raise ValueError('Choose at least one supported prediction market')
        available,_,_,errors=week.window(league,zone,old,force=True)
        if errors:raise ValueError('Fixture coverage is incomplete; refresh later')
        chosen=week._resolve(list(keys),str(raw or '').strip(),available)
        now=datetime.now(timezone.utc)
        ids=[r['key'] for r in chosen]
        if not 1<=len(ids)<=16 or len(set(ids))!=len(ids):
            raise ValueError('Select 1–16 distinct verified fixtures')
        if any(not now<week._utc(r['kickoff_utc'])<now+timedelta(hours=168) for r in chosen):
            raise ValueError('A fixture has started or is outside the rolling seven days')
        if len(chosen)>ledger.remaining(uid):
            raise ValueError('Your free daily match-analysis allowance is insufficient')
    except (ValueError,TypeError,KeyError,RuntimeError) as exc:
        return reject(str(exc))

    try:
        rid=ledger.reserve(uid,len(chosen))
        if not rid:return reject('Daily free prediction allowance exhausted')
    except Exception:
        return reject('Free quota storage temporarily unavailable; try again')

    count=0
    try:
        if engine_factory is None:
            raise RuntimeError('Verified worldwide prediction engine was not supplied')
        engine=engine_factory()
        values,abstentions=engine.predict(chosen,requested_markets=chosen_markets)

        if not isinstance(values,list) or not isinstance(abstentions,list):
            raise RuntimeError('Invalid engine result collections')

        expected={
            str(r['home'])+' vs '+str(r['away']):str(r['key'])
            for r in chosen
        }

        selected_keys={str(r['key']) for r in chosen}

        if len(expected)!=len(chosen) or len(selected_keys)!=len(chosen):
            raise RuntimeError('Ambiguous fixture names or duplicate IDs')

        predicted=[]

        for row in values:
            if not isinstance(row,list) or len(row)!=13 or row[0] not in expected:
                raise RuntimeError('Prediction row does not match selected fixture')

            predicted.append(expected[row[0]])

        abstained=[]

        for item in abstentions:
            if not isinstance(item,dict) or str(item.get('fixture','')) not in selected_keys:
                raise RuntimeError('Abstention does not match selected fixture ID')

            abstained.append(str(item['fixture']))

        if (
            len(predicted)!=len(set(predicted))
            or len(abstained)!=len(set(abstained))
            or set(predicted)&set(abstained)
            or (set(predicted)|set(abstained))!=selected_keys
        ):
            raise RuntimeError('Incomplete or duplicate fixture accounting')

        for v in values:
            if 'Full-time result' not in chosen_markets:
                for col in (1,2,3,8):v[col]='—'
            if 'Over/Under 2.5' not in chosen_markets:
                for col in (4,5):v[col]='—'
            if 'Both Teams to Score' not in chosen_markets:
                for col in (6,7):v[col]='—'
        count=len(values)
        msg=f'**{count} of {len(chosen)} selected matches analyzed** using verified worldwide models.'
        issues='; '.join(str(a.get('fixture','unknown'))[:65]+' ['+str(a.get('reason','UNAVAILABLE'))[:40]+']'
                         for a in (abstentions or [])[:16] if isinstance(a,dict))
        note=(f'{len(abstentions)} matches abstained because of unavailable or invalid evidence. '
              +('Unavailable fixture IDs: '+issues+'. ' if issues else '')
              +'No invented exact scores or half-time/full-time probabilities. '+DISCLAIMER)
        response=(msg,values,note)
    except Exception as exc:
        # Internal trace stays in the Space logs. UI gets a safe, actionable status.
        import logging
        logging.getLogger('googlesaada.predict').exception('Model invocation aborted: %s',type(exc).__name__)
        response=(f'Prediction currently unavailable ({type(exc).__name__}).',[],
                  DISCLAIMER+' No prediction was returned and no completed analysis should be charged. '
                  'Check model freshness and provider coverage in the Space Runtime Logs.')
        count=0
    try:
        ledger.settle(rid,count)
    except Exception:
        # Fail closed: do not claim allowance was refunded if settlement did not complete.
        return ('Prediction settlement needs verification. Please retry later.',[],DISCLAIMER,
                signed,'Quota settlement unavailable; do not assume a refund.')
    return (*response,signed,balance())
