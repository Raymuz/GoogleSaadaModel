"""Worldwide provider-sourced fixture discovery and live scores.

Different from original 10-model three-league inference coverage. Never
returns or fabricates model probabilities. ESPN source availability varies.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date, timedelta, timezone
from html import escape
from urllib.request import urlopen,Request
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
from threading import RLock
import json, re, time

COMPETITIONS={
 'Premier League':'eng.1','La Liga':'esp.1','Bundesliga':'ger.1',
 'Serie A':'ita.1','Ligue 1':'fra.1','Primeira Liga':'por.1',
 'Eredivisie':'ned.1','English Championship':'eng.2',
 'Scottish Premiership':'sco.1','Major League Soccer':'usa.1',
 'Liga MX':'mex.1','Brazil Série A':'bra.1',
 'Argentine Primera División':'arg.1',
 'UEFA Champions League':'uefa.champions',
 'UEFA Europa League':'uefa.europa',
 'UEFA Conference League':'uefa.europa.conf',
 'Copa Libertadores':'conmebol.libertadores',
 'NWSL':'usa.nwsl','Women’s Champions League':'uefa.wchampions',
}
ALL='All available competitions'
_CACHE={}
_LOCK=RLock()

def _fetch(competition,ymd):
    key=(competition,ymd)
    with _LOCK:
        val=_CACHE.get(key)
        if val and time.monotonic()-val[0]<65:return val[1]
    code=COMPETITIONS[competition]
    url=('https://site.api.espn.com/apis/site/v2/sports/soccer/'+code+
         '/scoreboard?'+urlencode({'dates':ymd,'limit':500}))
    req=Request(url,headers={'User-Agent':'GoogleSaada/1.0','Accept':'application/json'})
    with urlopen(req,timeout=7) as resp:
        if resp.status!=200:raise RuntimeError('Provider response '+str(resp.status))
        raw=resp.read(4000001)
    if len(raw)>4000000:raise ValueError('Provider response too large')
    data=json.loads(raw.decode('utf-8'))
    if not isinstance(data,dict) or not isinstance(data.get('events'),list):
        raise ValueError('Invalid provider response')
    with _LOCK:_CACHE[key]=(time.monotonic(),data['events'])
    return data['events']

def _score(x):
    if isinstance(x,dict):x=x.get('value')
    try:
        if isinstance(x,bool) or x is None:return None
        y=float(x)
        return int(y) if y>=0 and y.is_integer() else None
    except (ValueError,TypeError,OverflowError):return None

def _parse(event,competition):
    try:
        comp=event['competitions'][0]
        teams={x['homeAway']:x for x in comp['competitors'] if x.get('homeAway') in ('home','away')}
        if set(teams)!={'home','away'}:return None
        home=teams['home']['team']['displayName'];away=teams['away']['team']['displayName']
        if not home or not away:return None
        mid=str(event['id'])
        if not re.fullmatch(r'\d{4,20}',mid):return None
        dt=datetime.fromisoformat(str(event['date']).replace('Z','+00:00'))
        if dt.tzinfo is None:return None
        state=event.get('status',{}).get('type',{})
        short=str(state.get('state','')).lower()
        detail=str(state.get('shortDetail') or state.get('description') or '')
        status=str(state.get('name') or '').upper()
        if any(v in (status+' '+detail).upper() for v in ('POSTPON','CANCEL','ABANDON','SUSPEND')):phase='OTHER'
        elif state.get('completed') is True and short=='post':phase='FINAL'
        elif short=='in':phase='LIVE'
        elif short=='pre':phase='UPCOMING'
        else:phase='OTHER'
        return {'key':COMPETITIONS[competition]+'|'+mid,'league':competition,
           'home':home,'away':away,'kickoff_utc':dt.astimezone(timezone.utc).isoformat(),
           'phase':phase,'description':detail,'home_score':_score(teams['home'].get('score')),
           'away_score':_score(teams['away'].get('score')),'source':'ESPN'}
    except (KeyError,TypeError,ValueError,IndexError):return None

def collect(day,league,tz,search='',live_now=False):
    if tz not in ('Africa/Douala','America/Chicago','UTC','Europe/London',
                  'America/New_York','Europe/Paris','Africa/Lagos'):
        raise ValueError('Unsupported time zone')
    if league!=ALL and league not in COMPETITIONS:raise ValueError('Unsupported competition')
    target=date.fromisoformat(day)
    now=datetime.now(timezone.utc)
    zone=ZoneInfo(tz)
    if live_now:target=now.astimezone(zone).date()
    # Three UTC dates cover the selected local calendar day and live midnight.
    utc_days={(target+timedelta(days=n)).strftime('%Y%m%d') for n in (-1,0,1)}
    names=[league] if league in COMPETITIONS else list(COMPETITIONS)
    jobs=[(name,ymd) for name in names for ymd in sorted(utc_days)]
    found={};failed=[]
    with ThreadPoolExecutor(max_workers=10) as pool:
        fs={pool.submit(_fetch,n,d):(n,d) for n,d in jobs}
        for future in as_completed(fs):
            name,ymd=fs[future]
            try:
                for event in future.result():
                    row=_parse(event,name)
                    if row is None:continue
                    stamp=datetime.fromisoformat(row['kickoff_utc'])
                    # Live-now may be cross-midnight locally.
                    if live_now:
                        if row['phase']!='LIVE':continue
                    elif stamp.astimezone(zone).date()!=target:continue
                    if row['key'] in found and found[row['key']]!=row:
                        # Same ESPN event from multiple date queries; newer
                        # snapshot may differ. Use later processing snapshot.
                        pass
                    found[row['key']]=row
            except (OSError,ValueError,RuntimeError,TimeoutError) as exc:
                failed.append(name+' '+ymd+' ('+type(exc).__name__+')')
    rows=sorted(found.values(),key=lambda x:(x['kickoff_utc'],x['league'],x['key']))
    if search:
        query=search.casefold().strip()
        rows=[r for r in rows if query in (r['home']+' '+r['away']+' '+r['league']).casefold()]
    return rows,failed,len(jobs)

def fixture_table(rows,tz):
    zone=ZoneInfo(tz)
    out=[]
    for r in rows:
        if r['phase']!='UPCOMING':continue
        dt=datetime.fromisoformat(r['kickoff_utc']).astimezone(zone)
        if datetime.fromisoformat(r['kickoff_utc'])<=datetime.now(timezone.utc):continue
        out.append([r['league'],dt.strftime('%Y-%m-%d %H:%M'),r['home'],r['away'],'Scheduled'])
    return out

def cards(rows,tz):
    zone=ZoneInfo(tz)
    bits=['<div class="gs-global-scores" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px">']
    for r in rows:
        t=datetime.fromisoformat(r['kickoff_utc']).astimezone(zone).strftime('%H:%M')
        hs='—' if r['home_score'] is None or r['phase']=='UPCOMING' else str(r['home_score'])
        aw='—' if r['away_score'] is None or r['phase']=='UPCOMING' else str(r['away_score'])
        status=escape(r['description'] or r['phase'].title())
        lines=(f'<div style="padding:14px;border:1px solid #d8dfe6;border-radius:13px">'
           f'<div style="font-size:12px;opacity:.7">{escape(r["league"])} · {t} · {status}</div>'
           f'<div style="display:flex;justify-content:space-between;margin-top:11px"><strong>{escape(r["home"])}</strong><strong>{hs}</strong></div>'
           f'<div style="display:flex;justify-content:space-between;margin-top:5px"><strong>{escape(r["away"])}</strong><strong>{aw}</strong></div>'
           f'</div>')
        bits.append(lines)
    bits.append('</div>')
    return ''.join(bits) if rows else 'No matches returned within the selected coverage. Try another date or filter.'
