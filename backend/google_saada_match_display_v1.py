"""Verified seven-market probabilities -> one row per eligible match.
Never create missing model probabilities or expose held team names in user notes.
"""
from collections import OrderedDict
import math,re

MARKETS=('Home win','Draw','Away win','Over 2.5 goals','Under 2.5 goals','Both teams score — Yes','Both teams score — No')
HEADERS=['Match','Home win','Draw','Away win','Over 2.5','Under 2.5','BTTS Yes','BTTS No','Leading 1X2 outcome']
NOTICE=('The percentages are experimental research estimates from the original ten fitted ML models and historical feature pipeline. '
        'They are not calibrated, verified betting probabilities or guarantees. '
        'The leading full-time outcome is whichever of Home win, Draw and Away win has the highest displayed probability; '
        'that is a comparison of the model outputs, not a separate certainty score.')
DISCLAIMER='All predictions are for entertainment purposes only. GoogleSaada does not guarantee wins and is not responsible for any decisions made based on these predictions.'

def pct(text):
    if not isinstance(text,str) or not re.fullmatch(r'\d{1,3}(?:\.\d+)?%',text.strip()):
        raise ValueError('Unverified probability format')
    value=float(text.strip()[:-1])
    if not math.isfinite(value) or not 0<=value<=100:
        raise ValueError('Out-of-range model probability')
    return value

def compact(rows,full_set=False):
    """One line per match; — for markets not supplied by backend.

    A seven-market row must pass normalization consistency tests. Sparse
    recommendation rows are marked incomplete, never filled with guesses.
    """
    found=OrderedDict()
    for record in rows or []:
        if not isinstance(record,(list,tuple)) or len(record)<3:
            raise ValueError('Unexpected model row')
        match,market,value=record[:3]
        if not isinstance(match,str) or not match.strip() or market not in MARKETS:
            raise ValueError('Unrecognized original market schema')
        pct(value)
        bucket=found.setdefault(match,{})
        if market in bucket and bucket[market]!=value:
            raise ValueError('Conflicting original model estimate')
        bucket[market]=value
    wide=[];notes=[]
    for match,values in found.items():
        supplied=set(values)
        if supplied==set(MARKETS):
            ft=sum(pct(values[k]) for k in MARKETS[:3])
            ou=sum(pct(values[k]) for k in MARKETS[3:5])
            bt=sum(pct(values[k]) for k in MARKETS[5:])
            if any(abs(x-100)>0.25 for x in (ft,ou,bt)):
                raise ValueError('Original market sums invalid')
        elif full_set:
            raise ValueError('Seven original model markets not available')
        leading='—'
        if all(x in values for x in MARKETS[:3]):
            max_v=max(pct(values[k]) for k in MARKETS[:3])
            winners=[k for k in MARKETS[:3] if abs(pct(values[k])-max_v)<1e-9]
            leading=winners[0] if len(winners)==1 else 'Tie: '+', '.join(winners)
        wide.append([match]+[values.get(k,'—') for k in MARKETS]+[leading])
        if leading!='—':
            ftparts=', '.join(f'{x} {values[x]}' for x in MARKETS[:3])
            second=f' Goal total: Over 2.5 {values[MARKETS[3]]}, Under 2.5 {values[MARKETS[4]]}.' if all(x in values for x in MARKETS[3:5]) else ''
            third=f' Both teams to score: Yes {values[MARKETS[5]]}, No {values[MARKETS[6]]}.' if all(x in values for x in MARKETS[5:]) else ''
            notes.append(f'{match}: {ftparts}. Leading full-time outcome: {leading}.{second}{third}')
        else:
            notes.append(f'{match}: '+', '.join(f'{m} {values[m]}' for m in MARKETS if m in values)+'. Other markets were not supplied; no values were inferred.')
    return wide,notes

def public_notes(original_notes,wide,decisions,context='prediction'):
    """Do NOT echo raw withheld fixture names/rejection text into output."""
    raw=str(original_notes or '')
    withheld=sum(1 for l in raw.splitlines() if l.lstrip().startswith(('•','- ')) and (' — ' in l or ' – ' in l))
    bits=['How the model decision was made:']
    if decisions:
        bits.extend('• '+line for line in decisions)
    else:
        bits.append('• No eligible verified model probabilities were returned for this request.')
    bits.append(NOTICE)
    if withheld:
        bits.append(f'{withheld} requested fixture(s) were withheld because verified historical evidence or eligibility requirements were not satisfied. Individual withheld team names are omitted.')
    bits.append('MANDATORY DISCLAIMER: '+DISCLAIMER)
    return '\n\n'.join(bits)
