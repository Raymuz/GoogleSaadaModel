"""GoogleSaada V2G: proof-oriented 56-feature pre-match builder.

Reproduces the V3.5 semantics: historical team and same-venue metrics
aggregate simultaneous kickoff observations before taking last 5/10
historical timestamps. Uses ONLY completed matches before the evidence
cutoff and fixture kickoff. No synthetic defaults, no training, no remote IO.

This module does not establish live-source availability or authorize release.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import math
import unicodedata
import re

METRICS = ('gf','ga','points','win','draw','clean_sheet')
VENUE_METRICS = ('gf','ga','win','points')
BASE = [f'{metric}_last{window}' for window in (5,10) for metric in METRICS]
VENUES = [f'{venue}_{metric}_last5' for venue in ('home','away') for metric in VENUE_METRICS]
SIDE = ['matches_before_next','rest_days'] + BASE + VENUES
FEATURE_NAMES = tuple([f'{side}_{name}' for side in ('home','away') for name in SIDE] +
                      [f'diff_{metric}_last{window}' for metric in METRICS for window in (5,10)])
assert len(FEATURE_NAMES)==56 and len(set(FEATURE_NAMES))==56

class FeatureUnavailable(ValueError):
    pass

def sha256_file(path):
    d=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):d.update(b)
    return d.hexdigest()

def normalize_name(value):
    text=unicodedata.normalize('NFKD',str(value or '')).casefold()
    return ' '.join(re.sub('[^a-z0-9]+',' ',''.join(c for c in text if not unicodedata.combining(c))).split())

def utc_timestamp(value):
    import pandas as pd
    t=pd.Timestamp(value)
    if t.tzinfo is None:
        raise FeatureUnavailable('Timezone-aware UTC timestamps required')
    return t.tz_convert('UTC')

class GlobalHistory56:
    """Pre-match feature builder for a verified cleaned history parquet.

    Explicit team IDs are required. Caller must authenticate and verify
    fixture identities externally; names are never used as unique IDs.
    """
    def __init__(self, history_file, *, expected_sha256, feature_names,
                 min_prior_matches=5):
        import pandas as pd
        if tuple(feature_names)!=FEATURE_NAMES:
            raise FeatureUnavailable('Feature schema/order differs from V3.5')
        self.path=Path(history_file)
        if not self.path.is_file() or sha256_file(self.path)!=expected_sha256:
            raise FeatureUnavailable('Historical dataset absent or checksum mismatch')
        required=['id','date_utc','home_team_id','away_team_id','goals_home','goals_away']
        self.history=pd.read_parquet(self.path,columns=required)
        if len(self.history)==0 or self.history['id'].duplicated().any():
            raise FeatureUnavailable('History absent or duplicate fixture IDs')
        self.history['date_utc']=pd.to_datetime(self.history['date_utc'],utc=True,errors='raise')
        if self.history[required].isna().any().any():
            raise FeatureUnavailable('Incomplete historical fixture data')
        self.history=self.history.sort_values(['date_utc','id']).reset_index(drop=True)
        self.min_prior_matches=int(min_prior_matches)
        if self.min_prior_matches<1:
            raise FeatureUnavailable('Invalid minimum history')
        self.history_sha256=expected_sha256
        self.features=FEATURE_NAMES

    @staticmethod
    def _team_metrics(df,team_id,query_cutoff):
        import pandas as pd
        import numpy as np
        h=df.loc[df['home_team_id'].eq(team_id),['id','date_utc','goals_home','goals_away']].copy()
        a=df.loc[df['away_team_id'].eq(team_id),['id','date_utc','goals_home','goals_away']].copy()
        h['gf']=h['goals_home'];h['ga']=h['goals_away'];h['venue']='home'
        a['gf']=a['goals_away'];a['ga']=a['goals_home'];a['venue']='away'
        events=pd.concat([h,a],ignore_index=True)
        if events.empty:
            raise FeatureUnavailable('Team has no prior completed matches')
        events=events.sort_values(['date_utc','id'])
        events['points']=np.where(events['gf']>events['ga'],3,np.where(events['gf']==events['ga'],1,0))
        events['win']=(events['gf']>events['ga']).astype(int)
        events['draw']=(events['gf']==events['ga']).astype(int)
        events['clean_sheet']=(events['ga']==0).astype(int)
        timed=events.groupby('date_utc',sort=True).agg(
            **{m:(m,'mean') for m in METRICS},
            matches_at_timestamp=('id','count'))
        result={'matches_before_next':float(timed['matches_at_timestamp'].sum()),
                'rest_days':float((query_cutoff-timed.index[-1]).total_seconds()/86400)}
        for window in (5,10):
            subset=timed.tail(window)
            for metric in METRICS:
                result[f'{metric}_last{window}']=float(subset[metric].mean())
        for venue in ('home','away'):
            venue_events=events.loc[events['venue'].eq(venue)]
            if venue_events.empty:
                raise FeatureUnavailable('Team lacks required home/away history')
            venue_timed=venue_events.groupby('date_utc',sort=True)[list(VENUE_METRICS)].mean().tail(5)
            for metric in VENUE_METRICS:
                result[f'{venue}_{metric}_last5']=float(venue_timed[metric].mean())
        if not math.isfinite(result['rest_days']) or result['rest_days']<=0:
            raise FeatureUnavailable('Invalid or leaking team rest-days evidence')
        return result

    def build(self, *, home_team_id, away_team_id, kickoff_utc, evidence_cutoff_utc):
        import numpy as np
        if str(home_team_id)==str(away_team_id):
            raise FeatureUnavailable('Identical home and away team identities')
        kickoff=utc_timestamp(kickoff_utc)
        cutoff=utc_timestamp(evidence_cutoff_utc)
        if cutoff>=kickoff:
            raise FeatureUnavailable('Features must be verified before kickoff')
        df=self.history.loc[self.history['date_utc'].lt(cutoff) & self.history['date_utc'].lt(kickoff)]
        if df.empty:
            raise FeatureUnavailable('No completed match prior to evidence cutoff')
        home=self._team_metrics(df,home_team_id,kickoff)
        away=self._team_metrics(df,away_team_id,kickoff)
        if home['matches_before_next']<self.min_prior_matches or away['matches_before_next']<self.min_prior_matches:
            raise FeatureUnavailable('Insufficient history: fewer than five previous matches')
        result={f'home_{key}':value for key,value in home.items()}
        result.update({f'away_{key}':value for key,value in away.items()})
        for metric in METRICS:
            for window in (5,10):
                name=f'{metric}_last{window}'
                result[f'diff_{name}']=home[name]-away[name]
        if set(result)!=set(self.features):
            raise FeatureUnavailable('Engine did not produce exactly 56 trained features')
        result={name:float(result[name]) for name in self.features}
        if not np.isfinite(list(result.values())).all():
            raise FeatureUnavailable('Not all 56 trained inputs are finite')
        return result


def evidence_digest(fixture,features,source_sha256):
    payload=json.dumps({'fixture':fixture,'features':features,'source_sha256':source_sha256},
                       sort_keys=True,separators=(',',':'),default=str).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def exact_identity_lookup(dictionary, *, source_id=None, name=None, league_id=None, participation=None):
    """Resolve by validated source ID or unique normalized name, never guess.

    This is a staging lookup, not a real-time fixture-source identity bridge.
    """
    if source_id is not None:
        found=dictionary.loc[dictionary['id'].astype(str).eq(str(source_id))]
    elif name is not None and str(name).strip():
        found=dictionary.loc[dictionary['search_key'].eq(normalize_name(name))]
    else:
        raise FeatureUnavailable('Provide a source team/league ID or name')
    if league_id is not None and participation is not None and 'id' in dictionary:
        valid=set(participation.loc[participation['league_id'].astype(str).eq(str(league_id)),'team_id'].astype(str))
        found=found.loc[found['id'].astype(str).isin(valid)]
    if len(found)!=1:
        raise FeatureUnavailable('Unknown or ambiguous identity; explicit verified IDs required')
    return found.iloc[0].to_dict()
