# GOOGLESAADA V9 — STAGE 2: DIRECT PUBLIC HISTORICAL DATA
# Run as one Colab cell with: %run /content/GoogleSaada_V9_Stage2_Public_Datasets.py
# No uploads, training, model loading, or deployment.
import sys, subprocess, os, io, json, hashlib, sqlite3, time
from pathlib import Path
from datetime import datetime, timezone

for module, package in [('pandas','pandas>=2.2,<4'),('requests','requests>=2.31,<3'),('pyarrow','pyarrow>=15,<24')]:
    try: __import__(module)
    except ImportError: subprocess.run([sys.executable,'-m','pip','install','-q',package],check=True)
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT=Path('/content/GoogleSaada_V9')
CONFIG=ROOT/'config'
RAW=ROOT/'data/raw'
CURATED=ROOT/'data/curated'
REPORTS=ROOT/'data/reports'
QUARANTINE=ROOT/'data/quarantine'
for folder in [CONFIG,CURATED,REPORTS,QUARANTINE,RAW/'soccer',RAW/'basketball',RAW/'american_football']:
    folder.mkdir(parents=True,exist_ok=True)

# Expansion is controlled. Modify lists to add leagues/seasons, not the downloader.
SOCCER_LEAGUES={'E0':'Premier League','E1':'Championship','SP1':'La Liga','D1':'Bundesliga','I1':'Serie A','F1':'Ligue 1','N1':'Eredivisie','P1':'Primeira Liga'}
SOCCER_START_YEARS=range(2018,2027)  # 2018/19 through 2026/27
AS_OF=pd.Timestamp.now(tz='UTC').tz_localize(None).normalize()
MAX_FILE_BYTES=50*1024*1024
TIMEOUT=(15,75)

session=requests.Session()
session.headers.update({'User-Agent':'GoogleSaada-Research/1.0 (historical match dataset audit)'})
retries=Retry(total=2,backoff_factor=1,status_forcelist=[429,500,502,503,504],allowed_methods=['GET'])
session.mount('https://',HTTPAdapter(max_retries=retries))

def sha256(data): return hashlib.sha256(data).hexdigest()

def download(url,path):
    # Keep authentic source bytes and avoid rewriting files on repeat runs.
    if path.is_file() and path.stat().st_size>50:
        data=path.read_bytes()
        if len(data)<=MAX_FILE_BYTES and not data.lstrip().lower().startswith((b'<!doctype html',b'<html')):
            return data,'cached'
    response=session.get(url,timeout=TIMEOUT,stream=True)
    response.raise_for_status()
    chunks=[]; total=0
    for chunk in response.iter_content(1024*1024):
        if not chunk: continue
        total+=len(chunk)
        if total>MAX_FILE_BYTES: raise ValueError('Source exceeds 50 MB limit')
        chunks.append(chunk)
    data=b''.join(chunks)
    if len(data)<50 or data.lstrip().lower().startswith((b'<!doctype html',b'<html')):
        raise ValueError('Empty or HTML response, not valid CSV')
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_bytes(data); temporary.replace(path)
    return data,'downloaded'

def normalize(df,sport,league,date,home,away,home_score,away_score,source_id):
    missing=[x for x in [date,home,away,home_score,away_score] if x not in df.columns]
    if missing: raise ValueError('Required columns missing: '+str(missing))
    f=pd.DataFrame({'date':pd.to_datetime(df[date],errors='coerce',dayfirst=(sport=='soccer')),
                    'home_team':df[home].astype('string').str.strip(),
                    'away_team':df[away].astype('string').str.strip(),
                    'home_score':pd.to_numeric(df[home_score],errors='coerce'),
                    'away_score':pd.to_numeric(df[away_score],errors='coerce')})
    f['sport']=sport
    f['league']=league if isinstance(league,str) else df[league].astype('string')
    f['source_id']=source_id
    f['date']=f['date'].dt.tz_localize(None) if f['date'].dt.tz is not None else f['date']
    good=(f['date'].notna() & (f['date']<=AS_OF) & f['home_team'].notna() & f['away_team'].notna() &
          f['home_team'].ne('') & f['away_team'].ne('') & f['home_team'].ne(f['away_team']) &
          f['home_score'].notna() & f['away_score'].notna() &
          f['home_score'].ge(0) & f['away_score'].ge(0) &
          f['home_score'].le(250) & f['away_score'].le(250))
    if sport=='soccer': good &= f['home_score'].le(30)&f['away_score'].le(30)
    if sport=='american_football': good &= f['home_score'].le(100)&f['away_score'].le(100)
    f=f.loc[good].copy()
    f['home_score']=f['home_score'].astype(int)
    f['away_score']=f['away_score'].astype(int)
    return f

records=[]; data_frames={'soccer':[],'basketball':[],'american_football':[]}; errors=[]

def ingest(source_id,sport,league,url,raw_path,column_map,league_field=None):
    try:
        raw,status=download(url,raw_path)
        frame=pd.read_csv(io.BytesIO(raw),low_memory=False)
        if len(frame)==0: raise ValueError('No rows in CSV')
        output=normalize(frame,sport,league_field if league_field else league,
                         column_map['date'],column_map['home'],column_map['away'],
                         column_map['home_score'],column_map['away_score'],source_id)
        if len(output)==0: raise ValueError('No completed eligible games after validation')
        data_frames[sport].append(output)
        record={'id':source_id,'sport':sport,'league':league,'url':url,'raw_path':str(raw_path),
                'sha256':sha256(raw),'bytes':len(raw),'raw_rows':len(frame),
                'eligible_rows':len(output),'status':status,'retrieved_utc':datetime.now(timezone.utc).isoformat(),
                'provenance':'public historical results; research use; review provider terms before redistribution'}
        records.append(record)
        print(f'[PASS] {source_id}: {len(output):,} eligible games ({status})')
    except Exception as exc:
        errors.append({'id':source_id,'url':url,'error':f'{type(exc).__name__}: {str(exc)[:240]}'})
        print(f'[SKIPPED] {source_id}: {type(exc).__name__}: {str(exc)[:130]}')

print('='*76)
print('GOOGLESAADA — STAGE 2 DIRECT PUBLIC DATA COLLECTION')
print('No source_manifest.json upload required')
print('='*76)

# Soccer: completed games only, preserving each original seasonal CSV.
for league in SOCCER_LEAGUES:
    for year in SOCCER_START_YEARS:
        season=f'{year%100:02d}{(year+1)%100:02d}'
        url=f'https://www.football-data.co.uk/mmz4281/{season}/{league}.csv'
        ingest(f'soccer_{league}_{season}','soccer',league,url,
               RAW/'soccer'/league/f'{season}.csv',
               {'date':'Date','home':'HomeTeam','away':'AwayTeam','home_score':'FTHG','away_score':'FTAG'})

# NFL: verified public file from nflverse/nfldata; includes schedule/final scores.
ingest('nflverse_games','american_football','NFL',
       'https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv',
       RAW/'american_football'/'nflverse_games.csv',
       {'date':'gameday','home':'home_team','away':'away_team','home_score':'home_score','away_score':'away_score'})

# NBA: games.csv source; only completed games with actual final scores are accepted.
ingest('hf_nba_games','basketball','NBA',
       'https://huggingface.co/datasets/davdsdfd/nba-games/resolve/main/games.csv',
       RAW/'basketball'/'nba_games.csv',
       {'date':'GAME_DATE_EST','home':'HOME_TEAM_ID','away':'VISITOR_TEAM_ID','home_score':'PTS_home','away_score':'PTS_away'})

# Do not accept partial coverage as completion of a three-sport training foundation.
counts={}
cleaned={}
for sport,parts in data_frames.items():
    if not parts:
        counts[sport]=0
        continue
    merged=pd.concat(parts,ignore_index=True)
    merged=merged.sort_values(['date','source_id'])
    key=['sport','league','date','home_team','away_team']
    conflicts=merged.groupby(key,dropna=False)[['home_score','away_score']].nunique()
    conflicting_keys=conflicts[(conflicts>1).any(axis=1)]
    # Do not silently select one score if providers disagree.
    if not conflicting_keys.empty:
        bad_idx=pd.MultiIndex.from_frame(merged[key]).isin(conflicting_keys.index)
        bad=merged.loc[bad_idx]
        bad.to_csv(QUARANTINE/f'{sport}_conflicting_scores.csv',index=False)
        merged=merged.loc[~bad_idx].copy()
        print(f'[QUARANTINED] {sport}: {len(bad)} conflicting records')
    merged=merged.drop_duplicates(key,keep='first').sort_values('date').reset_index(drop=True)
    cleaned[sport]=merged
    counts[sport]=len(merged)
    output=CURATED/f'{sport}_historical_matches.parquet'
    merged.to_parquet(output,index=False)
    print(f'[CURATED] {sport}: {len(merged):,} unique completed games -> {output}')

# Source manifest is evidence of successfully retrieved original sources.
manifest={'project':'GoogleSaada','version':'V9-fresh-training','stage':'2-direct-public-data',
          'created_utc':datetime.now(timezone.utc).isoformat(),
          'sources':records,'failed_sources':errors,'counts':counts,
          'warning':'Scores/odds/post-match stats must not be used as pregame features. Store outcomes only as targets.'}
(CONFIG/'source_manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
(REPORTS/'V9_Stage2_Data_Audit.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')

# Portable SQLite match database for subsequent data-quality and feature engineering steps.
DB=ROOT/'data'/'googlesaada_historical.sqlite'
if cleaned:
    with sqlite3.connect(DB) as con:
        for sport,df in cleaned.items():
            df.to_sql(sport+'_matches',con,if_exists='replace',index=False,chunksize=5000)
            con.execute(f'CREATE INDEX IF NOT EXISTS idx_{sport}_date ON {sport}_matches(date)')
print('='*76)
print('STAGE 2 SUMMARY')
for sport in ('soccer','american_football','basketball'):
    print(f'[{"PASS" if counts.get(sport,0)>0 else "BLOCKED"}] {sport}: {counts.get(sport,0):,} matches')
print('[SOURCES SUCCESS]',len(records),'[SOURCES FAILED]',len(errors))
print('[MANIFEST]',CONFIG/'source_manifest.json')
print('[DATABASE]',DB if cleaned else 'NOT GENERATED')
print('[AUDIT]',REPORTS/'V9_Stage2_Data_Audit.json')
if any(counts.get(s,0)==0 for s in ('soccer','american_football','basketball')):
    print('STATUS: STAGE_2_PARTIAL — inspect failed sources; do not proceed to three-sport training')
else:
    print('STATUS: STAGE_2_DATA_DOWNLOADED — next: independently audit leakage, dates and coverage')
print('='*76)
