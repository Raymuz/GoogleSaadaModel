"""Experimental derived soccer markets, not separately calibrated ML models.
Requires six original fitted goal-regressor outputs, source-verified eligible
fixture, full numerical checks and original seven-market probability evidence.
"""
from math import exp, fsum, isfinite

GOAL_KEYS=('full_time_home','full_time_away','first_half_home','first_half_away','second_half_home','second_half_away')
FULL_LABELS=('Home','Draw','Away')
HTFT_LABELS=tuple(a+'/'+b for a in FULL_LABELS for b in FULL_LABELS)

class MissingGoalRates(ValueError):pass

def verified_rates(item):
    if item.get('status')!='UNAPPROVED_FROZEN_HISTORY_RESEARCH_ESTIMATE':
        raise MissingGoalRates('No verified model research estimate')
    if item.get('goal_rate_provenance')!='ORIGINAL_TEN_MODEL_REGRESSOR_OUTPUTS_UNCALIBRATED':
        raise MissingGoalRates('Original goal-rate provenance not verified')
    rates=item.get('verified_goal_rates')
    if not isinstance(rates,dict) or set(rates)!=set(GOAL_KEYS):
        raise MissingGoalRates('Six original fitted goal-rate outputs unavailable')
    checked={}
    for key in GOAL_KEYS:
        v=rates[key]
        if isinstance(v,bool) or not isinstance(v,(float,int)) or not isfinite(v) or v<0 or v>25:
            raise MissingGoalRates('Goal-rate estimate invalid')
        checked[key]=float(v)
    probabilities=item.get('market_probabilities')
    if not isinstance(probabilities,dict) or set(probabilities)!={
        'FT_1X2_HOME','FT_1X2_DRAW','FT_1X2_AWAY',
        'FT_OVER_2_5','FT_UNDER_2_5','FT_BTTS_YES','FT_BTTS_NO'}:
        raise MissingGoalRates('Original seven-market evidence missing')
    if abs(fsum(probabilities[k] for k in ('FT_1X2_HOME','FT_1X2_DRAW','FT_1X2_AWAY'))-1)>1e-6:
        raise MissingGoalRates('Original 1X2 probabilities invalid')
    return checked

def poisson(rate,max_goals=100):
    if not 0<=rate<=25 or not isfinite(rate):raise ValueError('Invalid Poisson mean')
    p=exp(-rate);v=[p]
    for n in range(1,max_goals+1):
        p=p*rate/n;v.append(p)
    if abs(fsum(v)-1)>1e-9:raise ValueError('Numerical Poisson mass truncated')
    return v

def outcome(h,a):return 'Home' if h>a else 'Draw' if h==a else 'Away'

def derive(item):
    r=verified_rates(item)
    fh,fa=poisson(r['full_time_home']),poisson(r['full_time_away'])
    # Exact full-time score using original full-time goal estimates (not GPT).
    exact=[]
    for h,ph in enumerate(fh):
        for a,pa in enumerate(fa):
            exact.append((ph*pa,h,a))
    exact.sort(key=lambda x:(-x[0],x[1],x[2]))
    if abs(fsum(x[0] for x in exact)-1)>1e-8:raise ValueError('Exact-score mass invalid')
    # Two disjoint half score distributions; combined FT determined by their sum.
    hh,ha=poisson(r['first_half_home']),poisson(r['first_half_away'])
    sh,sa=poisson(r['second_half_home']),poisson(r['second_half_away'])
    # Compact outcome aggregation for second half by goal difference. No need
    # for a 100^4 Cartesian product and no arbitrary score truncation.
    diff_second={}
    for h,ph in enumerate(sh):
        for a,pa in enumerate(sa):
            d=h-a;diff_second[d]=diff_second.get(d,0)+ph*pa
    matrix={x:0.0 for x in HTFT_LABELS}
    for h,ph in enumerate(hh):
        for a,pa in enumerate(ha):
            ht=outcome(h,a);first_difference=h-a;first_mass=ph*pa
            for d,pd in diff_second.items():
                ft='Home' if first_difference+d>0 else 'Draw' if first_difference+d==0 else 'Away'
                matrix[ht+'/'+ft]+=first_mass*pd
    total=fsum(matrix.values())
    if abs(total-1)>1e-8:raise ValueError('HT/FT joint probability mass invalid')
    joint={k:matrix[k]/total for k in HTFT_LABELS}
    ranked=sorted(joint.items(),key=lambda pair:(-pair[1],pair[0]))
    return {
        'exact_score':f'{exact[0][1]}–{exact[0][2]}',
        'exact_probability':exact[0][0],
        'exact_top5':[(f'{h}–{a}',p) for p,h,a in exact[:5]],
        'htft':ranked[0][0],
        'htft_probability':ranked[0][1],
        'htft_nine_probabilities':joint,
        'assumption':'Independent Poisson goals, using fitted FT/1H/2H original goal regressors; not separately trained or calibrated.',
        'fitted_goal_rates':dict(r)
    }
