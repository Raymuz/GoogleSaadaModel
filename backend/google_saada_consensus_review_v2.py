"""Comparison-only consensus review; no unvalidated pooling of model probabilities."""
from __future__ import annotations
import math

def review(whiteboard_rows):
    if not isinstance(whiteboard_rows, list):
        raise TypeError('Whiteboard rows must be list')
    grouped = {}
    for row in whiteboard_rows:
        if not isinstance(row, dict) or not all(k in row for k in ('fixture_id','market','specialist','value','evidence_id')):
            raise ValueError('Unverified whiteboard row')
        grouped.setdefault((row['fixture_id'],row['market']), []).append(row)
    comparisons=[]
    for (fixture_id,market), rows in sorted(grouped.items()):
        variants={row['specialist']:row['value'] for row in rows}
        if market == 'match_result':
            home=[float(v['H']) for v in variants.values() if isinstance(v,dict) and set(v)=={'H','D','A'}]
            spread=max(home)-min(home) if len(home)>=2 else None
            if spread is not None and not math.isfinite(spread):raise ValueError('Invalid specialist spread')
        else:spread=None
        comparisons.append({'fixture_id':fixture_id,'market':market,'specialist_count':len(rows),
                            'specialists':sorted(variants),'home_probability_spread':spread,
                            'consensus_prediction':None,'validated_weights_applied':False})
    return comparisons
