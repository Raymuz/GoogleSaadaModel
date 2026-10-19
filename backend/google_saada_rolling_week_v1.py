"""Verified rolling seven-day fixture browser and request boundary.

Uses pre-existing original fixture provider, not training membership.
Defines a strict half-open interval [server_now, server_now + 168 hours).
No external model or paid-Agent release flags modified.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from collections import defaultdict
import re
import unicodedata

DAYS = 7
MAXIMUM = 16

class WindowReject(ValueError):
    pass

def _now(t=None):
    t = datetime.now(timezone.utc) if t is None else t
    if not isinstance(t, datetime) or t.tzinfo is None or t.utcoffset() is None:
        raise WindowReject('A timezone-aware server clock is required')
    return t.astimezone(timezone.utc)

def _utc(value):
    if not isinstance(value, str):
        raise WindowReject('Missing source kickoff timestamp')
    try:
        t = datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError as exc:
        raise WindowReject('Invalid provider kickoff timestamp') from exc
    if t.tzinfo is None or t.utcoffset() is None:
        raise WindowReject('Naive kickoff timestamp rejected')
    return t.astimezone(timezone.utc)

def _validate(league, tz, source):
    if tz not in source.TZ_CHOICES:
        raise WindowReject('Choose a supported time zone')
    if league != source.ALL_LEAGUES and league not in source.LEAGUES:
        raise WindowReject('Choose a supported league')
    return ZoneInfo(tz)

def _normalize(value):
    value = unicodedata.normalize('NFKD', str(value))
    value = ''.join(c for c in value if not unicodedata.combining(c))
    return ' '.join(re.sub(r'[^a-z0-9]+', ' ', value.casefold()).split())

def window(league, tz, source, *, now=None, force=False):
    """Read all local dates overlapping 168h, filter UTC kickoff server-side.

    Returns (verified_fixture_rows, UTC_start, UTC_end, errors).
    If any date fails, browsing may be partial, but submission fail-closes.
    """
    zone = _validate(league,tz,source)
    start = _now(now)
    end = start + timedelta(days=DAYS)
    first = start.astimezone(zone).date()
    last = end.astimezone(zone).date()
    days = (last-first).days
    if not 6 <= days <= 9:
        raise WindowReject('Unexpected local calendar window')
    by_key={}; errors=[]
    for i in range(days+1):
        day = (first+timedelta(days=i)).isoformat()
        try:
            data=source.DAILY.day(day,league,tz,force=force)
            if not isinstance(data, list):
                raise WindowReject('Invalid provider fixture response')
        except Exception as exc:
            errors.append(day+': '+type(exc).__name__)
            continue
        for row in data:
            if not isinstance(row, dict) or row.get('phase')!='UPCOMING':
                continue
            try:
                kick=_utc(row.get('kickoff_utc'))
                if not start < kick < end:  # strictly future and inside horizon
                    continue
                key=row['key']
                if not isinstance(key,str) or not re.fullmatch(r'[a-z0-9.]+\|\d{4,20}',key):
                    continue
                if not all(isinstance(row.get(k),str) and row[k].strip()
                           for k in ('home','away','league')):
                    continue
                if league!=source.ALL_LEAGUES and row['league']!=league:
                    continue
                if row.get('source')!='ESPN':
                    continue
                previous=by_key.get(key)
                if previous is not None:
                    # Contradictory provider snapshots should never be silently used.
                    for field in ('kickoff_utc','home','away','league'):
                        if previous[field]!=row[field]:
                            raise WindowReject('Conflicting fixture identity: '+key)
                by_key[key]=row
            except WindowReject as exc:
                if str(exc).startswith('Conflicting fixture identity:'):
                    raise
                continue
            except (KeyError, TypeError):
                continue
    rows=sorted(by_key.values(),key=lambda r:(_utc(r['kickoff_utc']),r['key']))
    return rows,start,end,errors

def view(rows,tz,search='',limit=10,*,now=None):
    if not isinstance(limit,int) or isinstance(limit,bool) or not 1<=limit<=MAXIMUM:
        raise WindowReject('Choose 1 to 16 matches')
    zone=ZoneInfo(tz)
    start=_now(now)
    end=start+timedelta(days=DAYS)
    found=[]; term=_normalize(search or '')
    for r in rows or []:
        try:
            kick=_utc(r['kickoff_utc'])
            if r.get('phase')!='UPCOMING' or not start<kick<end:
                continue
            if term and term not in _normalize(r['home']+' '+r['away']+' '+r['league']):
                continue
            found.append(r)
        except (KeyError,TypeError,WindowReject):
            continue
    found.sort(key=lambda r:(_utc(r['kickoff_utc']),r['key']))
    table=[]; choices=[]
    for r in found:
        local=_utc(r['kickoff_utc']).astimezone(zone).strftime('%Y-%m-%d %H:%M')
        table.append([r['league'],local,r['home'],r['away'],'Scheduled'])
        choices.append((f"{local} · {r['home']} vs {r['away']} ({r['league']})",r['key']))
    return choices,table,[key for _,key in choices[:limit]]

def reload(day,league,tz,search,n,source,*,now=None):
    # day comes from the old (now hidden) control; it never anchors the server window.
    rows,start,end,errors=window(league,tz,source,now=now,force=False)
    choices,table,selected=view(rows,tz,search,n,now=start)
    status=(f'**{len(choices)} upcoming fixtures in the next 7×24 hours** '
            f'· {start.astimezone(ZoneInfo(tz)).strftime("%Y-%m-%d %H:%M %Z")} '
            f'to {end.astimezone(ZoneInfo(tz)).strftime("%Y-%m-%d %H:%M %Z")} '
            f'· {len(selected)}/{n} selected')
    if errors:
        status+=' · **Partial provider coverage:** '+str(len(errors))+' date requests failed.'
    return choices,selected,table,status,rows

def _request_count(raw):
    expression=re.fullmatch(r'\s*(?:predict|analy[sz]e|find|show)\s+(\d{1,3})\s+(?:upcoming\s+)?(?:games?|fixtures?|matches?)\s*',raw,re.I)
    if expression:return int(expression.group(1))
    return None

def _resolve(keys,raw,available):
    """Exact source-key or exact normalized match names. Ambiguity abstains."""
    if not isinstance(keys,(list,tuple)) or len(keys)>MAXIMUM:
        raise WindowReject('Maximum 16 matches per request')
    if len(keys)!=len(set(keys)):
        raise WindowReject('Duplicate fixture selections rejected')
    if any(not isinstance(k,str) for k in keys):
        raise WindowReject('Invalid fixture selection')
    lookup={r['key']:r for r in available}
    if any(k not in lookup for k in keys):
        raise WindowReject('Fixture is no longer upcoming or is outside the next 7 days. Refresh fixtures.')
    if raw and keys:
        raise WindowReject('Select fixtures OR enter match names, not both.')
    if keys:return [lookup[k] for k in keys]
    count=_request_count(raw)
    if count is not None:
        if not 1<=count<=MAXIMUM:raise WindowReject('Maximum 16 matches per request')
        return available[:count]
    if not raw.strip():raise WindowReject('Select or enter at least one upcoming fixture.')
    lines=[s.strip() for s in re.split(r'[\n;]+',raw) if s.strip()]
    if len(lines)>MAXIMUM:
        raise WindowReject('Maximum 16 matches per request')
    chosen=[]
    for line in lines:
        hit=re.fullmatch(r'(.{2,120}?)\s+(?:vs?\.?|versus)\s+(.{2,120}?)',line,flags=re.I)
        if not hit:
            raise WindowReject('Enter Home vs Away on separate lines or select scheduled fixtures.')
        a,b=_normalize(hit.group(1)),_normalize(hit.group(2))
        results=[r for r in available if _normalize(r['home'])==a and _normalize(r['away'])==b]
        if len(results)!=1:
            raise WindowReject('Fixture not found or ambiguous in next 7 days: '+line[:140])
        chosen.append(results[0])
    if len({r['key'] for r in chosen})!=len(chosen):
        raise WindowReject('Duplicate fixture requests rejected')
    return chosen

def predict(keys, raw, profile, day, league, tz, token, markets,
            *, source, execute, identify, remaining, quota, disclaimer, now=None):
    """Validate BEFORE any quota reservation; route by actual local kickoff date."""
    def deny(reason):
        uid,signed=identify(token)
        return (reason,[], 'No analysis generated or charged. '+disclaimer,
                signed,quota(uid))
    # Early bound before provider calls and before quota reservations.
    if not isinstance(keys,(list,tuple)) or len(keys)>MAXIMUM:
        return deny('Maximum 16 matches per request.')
    if len(str(raw or ''))>2500:
        return deny('Request is too long.')
    try:
        rows,start,end,errors=window(league,tz,source,now=now,force=True)
        if errors:
            raise WindowReject('Fixture provider coverage incomplete. Refresh and retry.')
        chosen=_resolve(list(keys or []),str(raw or '').strip(),rows)
        if len(chosen)>MAXIMUM:
            raise WindowReject('Maximum 16 matches per request')
        if not chosen:
            raise WindowReject('No future verified fixtures within the next 7 days.')
        # Check again after network calls to prevent rollover mid-request.
        current=_now()
        if now is not None:current=_now(now)  # deterministic test injection only
        if any(not current<_utc(r['kickoff_utc'])<current+timedelta(days=DAYS) for r in chosen):
            raise WindowReject('A match has started or moved outside the 7-day window.')
        uid,signed=identify(token)
        if len(chosen)>remaining(uid):
            raise WindowReject('Insufficient free analyses remaining for this request.')
    except (WindowReject,ValueError,TypeError) as exc:
        return deny(str(exc))

    by_day=defaultdict(list)
    zone=ZoneInfo(tz)
    for r in chosen:
        local_day=_utc(r['kickoff_utc']).astimezone(zone).date().isoformat()
        by_day[local_day].append(r['key'])
    # Each original bridge call remains authoritative for model eligibility.
    # Quotas are reserved/settled by the existing original per-day handler.
    # Cross-day reservations are NOT atomic; no claim of authenticated billing E2E.
    result_rows=[]; notes=[]; messages=[]
    session=signed; last_quota=quota(uid)
    for local_day in sorted(by_day):
        try:
            reply=execute(by_day[local_day],'',profile,local_day,league,tz,session,markets)
            if not isinstance(reply,(tuple,list)) or len(reply)!=5:
                raise RuntimeError('Original prediction executor returned invalid response')
            msg,values,note,session,last_quota=reply
            if not isinstance(values,list) or len(values)>len(by_day[local_day]):
                raise RuntimeError('Original executor returned invalid match quantity')
            result_rows.extend(values)
            messages.append(local_day+': '+str(msg))
            notes.append(local_day+': '+str(note))
        except Exception:
            messages.append(local_day+': Prediction service unavailable; no estimates added.')
    return (f'**{len(result_rows)} analysis row(s) returned** for {len(chosen)} '
            f'verified upcoming fixtures across {len(by_day)} kickoff dates.\n\n'
            +'\n\n'.join(messages),result_rows,'\n\n'.join(notes)+
            '\n\nOriginal production prediction eligibility remains enforced.',session,last_quota)
