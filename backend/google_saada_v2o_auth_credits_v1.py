"""GoogleSaada V2O — signed prediction identity and one-request free-credit ledger.

A signed assertion must be ISSUED BY AN EXTERNAL TRUSTED IDP; this module
NEVER issues a production identity. Requires HTTPS and explicit trusted origin.
No paid billing, no credit-card processing; free match-analysis allowance only.
"""
from __future__ import annotations
import base64, hashlib, hmac, json, os, re, sqlite3, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

class AccessDenied(ValueError): pass
class QuotaDenied(ValueError): pass
class InFlight(RuntimeError): pass

DAY_MAX = 20
MAX_BATCH = 16

def _unb64(s):
    try:
        if not isinstance(s,str) or len(s)>16384:raise ValueError()
        return base64.urlsafe_b64decode(s+'='*(-len(s)%4))
    except Exception:raise AccessDenied('Invalid signed session') from None

def _key():
    value=os.environ.get('GS_PREDICT_SIGNING_KEY_HEX','')
    try:key=bytes.fromhex(value)
    except ValueError:key=b''
    if len(key)<32:raise AccessDenied('Signed-in prediction service not configured')
    return key

def principal_from_assertion(assertion, *, now=None):
    key=_key()
    if not isinstance(assertion,str) or len(assertion)>8192 or assertion.count('.')!=2:
        raise AccessDenied('Please sign in to analyze matches')
    h,p,s=assertion.split('.')
    try:
        supplied=_unb64(s)
        expected=hmac.new(key,(h+'.'+p).encode('ascii'),hashlib.sha256).digest()
        if not hmac.compare_digest(expected,supplied):raise AccessDenied('Invalid session signature')
        header=json.loads(_unb64(h));claims=json.loads(_unb64(p))
    except (UnicodeError,ValueError,TypeError):
        raise AccessDenied('Invalid signed session') from None
    if not isinstance(header,dict) or header.get('alg')!='HS256' or header.get('typ')!='JWT' or not isinstance(claims,dict):
        raise AccessDenied('Unapproved session format')
    current=int(time.time()) if now is None else int(now)
    issued,expiry=claims.get('iat'),claims.get('exp')
    if type(issued) is not int or type(expiry) is not int or issued>current+30 or issued<current-300 or expiry<=current or expiry<=issued or expiry-issued>300:
        raise AccessDenied('Expired or invalid session')
    if claims.get('iss')!=os.environ.get('GS_PREDICT_ISSUER') or not os.environ.get('GS_PREDICT_ISSUER'):
        raise AccessDenied('Untrusted identity issuer')
    if claims.get('aud')!=os.environ.get('GS_PREDICT_AUDIENCE') or not os.environ.get('GS_PREDICT_AUDIENCE'):
        raise AccessDenied('Incorrect identity audience')
    if not isinstance(claims.get('scope'),str) or 'googlesaada:predict' not in claims['scope'].split():
        raise AccessDenied('Prediction permission not granted')
    subject=claims.get('sub')
    if not isinstance(subject,str) or not (1<=len(subject)<=128) or not subject.strip():
        raise AccessDenied('Invalid authenticated customer')
    return hmac.new(key,('googlesaada-predict:'+subject).encode(),hashlib.sha256).hexdigest()

def authenticated_principal(request, *, now=None):
    if os.environ.get('GS_PREDICT_AUTH_ENABLED')!='1':
        raise AccessDenied('Authenticated predictions are not enabled')
    underlying=getattr(request,'request',None)
    if underlying is None:raise AccessDenied('Authenticated HTTPS request required')
    url=getattr(underlying,'url',None)
    if getattr(url,'scheme',None)!='https':raise AccessDenied('HTTPS is required')
    headers=getattr(underlying,'headers',None)
    origin=headers.get('origin') if hasattr(headers,'get') else None
    allowed={x.strip().rstrip('/') for x in os.environ.get('GS_PREDICT_TRUSTED_ORIGINS','').split(',') if x.strip()}
    if not allowed or not origin or origin.rstrip('/') not in allowed:
        raise AccessDenied('Untrusted request origin')
    if urlparse(origin).scheme!='https':raise AccessDenied('Untrusted request origin scheme')
    cookies=getattr(underlying,'cookies',None)
    token=cookies.get('gs_prediction_identity') if hasattr(cookies,'get') else None
    return principal_from_assertion(token,now=now)

def _open(path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    conn=sqlite3.connect(str(path),timeout=15,isolation_level=None)
    conn.execute('PRAGMA busy_timeout=15000')
    conn.execute('CREATE TABLE IF NOT EXISTS v2o_usage(day TEXT NOT NULL, principal TEXT NOT NULL, used INTEGER NOT NULL, PRIMARY KEY(day,principal))')
    conn.execute('''CREATE TABLE IF NOT EXISTS v2o_request(
        id TEXT PRIMARY KEY, day TEXT NOT NULL, principal TEXT NOT NULL,
        digest TEXT NOT NULL, reserved INTEGER NOT NULL,
        actual INTEGER, state TEXT NOT NULL, created INTEGER NOT NULL, response TEXT)''')
    return conn

class CreditLedger:
    def __init__(self, path):
        self.path=str(path)
        with _open(self.path):pass
    @staticmethod
    def day(now=None):
        if now is None:now=datetime.now(timezone.utc)
        if now.tzinfo is None:raise ValueError('UTC-aware time required')
        return now.astimezone(timezone.utc).strftime('%Y-%m-%d')
    @staticmethod
    def request_digest(principal, keys, markets, profile, day):
        if not isinstance(keys,(list,tuple)) or not 1<=len(keys)<=MAX_BATCH or len(keys)!=len(set(keys)) or any(not isinstance(x,str) or not x for x in keys):
            raise ValueError('Select 1 to 16 distinct verified fixtures')
        payload=json.dumps({'p':principal,'keys':sorted(keys),'markets':sorted(markets),'profile':str(profile),'day':day},sort_keys=True,separators=(',',':'))
        return hmac.new(_key(),payload.encode(),hashlib.sha256).hexdigest()
    def reserve(self, principal, keys, markets, profile, *, now=None):
        day=self.day(now);rid=self.request_digest(principal,keys,markets,profile,day)
        db=_open(self.path)
        try:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT principal,digest,state,response FROM v2o_request WHERE id=?',(rid,)).fetchone()
            if row:
                if row[0]!=principal or row[1]!=rid:raise QuotaDenied('Conflicting request identity')
                db.commit()
                if row[2]=='SETTLED':return ('REPLAY',rid,json.loads(row[3]))
                if row[2]=='ACTIVE':raise InFlight('Matching request already running; please wait')
                raise QuotaDenied('Previous request outcome requires review')
            used=db.execute('SELECT used FROM v2o_usage WHERE day=? AND principal=?',(day,principal)).fetchone()
            used=used[0] if used else 0
            count=len(keys)
            if used+count>DAY_MAX:raise QuotaDenied('Insufficient authenticated free match credits')
            db.execute('INSERT INTO v2o_usage(day,principal,used) VALUES(?,?,?) ON CONFLICT(day,principal) DO UPDATE SET used=excluded.used',(day,principal,used+count))
            db.execute('INSERT INTO v2o_request VALUES(?,?,?,?,?,?,?,?,?)',(rid,day,principal,rid,count,None,'ACTIVE',int(time.time()),None))
            db.commit();return ('NEW',rid,None)
        except Exception:
            db.rollback();raise
        finally:db.close()
    def settle(self,rid,principal,actual,response):
        if type(actual) is not int or not 0<=actual<=MAX_BATCH:raise ValueError('Invalid settlement count')
        encoded=json.dumps(response,separators=(',',':'),allow_nan=False)
        db=_open(self.path)
        try:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT day,principal,reserved,state FROM v2o_request WHERE id=?',(rid,)).fetchone()
            if not row or row[1]!=principal:raise RuntimeError('Reservation ownership mismatch')
            day,_,reserved,state=row
            if state!='ACTIVE':raise RuntimeError('Request already finalized')
            if actual>reserved:raise RuntimeError('Settlement exceeds reserved credits')
            db.execute('UPDATE v2o_usage SET used=used-? WHERE day=? AND principal=?',(reserved-actual,day,principal))
            db.execute('UPDATE v2o_request SET actual=?,state=?,response=? WHERE id=?',(actual,'SETTLED',encoded,rid))
            db.commit()
        except Exception:
            db.rollback();raise
        finally:db.close()
    def remaining(self,principal,*,now=None):
        db=_open(self.path)
        try:
            row=db.execute('SELECT used FROM v2o_usage WHERE day=? AND principal=?',(self.day(now),principal)).fetchone()
            return max(0,DAY_MAX-(row[0] if row else 0))
        finally:db.close()


# GOOGLESAADA_V2P_HF_OAUTH_ACCESS_V1
def principal_from_hf_profile(profile):
    """Derive a private, stable credit-account ID from Gradio's SERVER-injected
    Hugging Face OAuthProfile, never from a text input or BrowserState.
    """
    import gradio as gr
    if profile is None or not isinstance(profile,gr.OAuthProfile):
        raise AccessDenied('Sign in with Hugging Face before using GoogleSaada')
    if not os.environ.get('OAUTH_CLIENT_ID'):
        raise AccessDenied('Hugging Face OAuth is not enabled for this Space')
    sub=profile.get('sub')
    if not isinstance(sub,str) or not (3<=len(sub)<=256) or not sub.strip():
        raise AccessDenied('Hugging Face OAuth subject is missing')
    # Avoid usernames/emails as ledger keys. Keep this secret only in Space Secrets.
    return hmac.new(_key(),('googlesaada:hf-oauth:'+sub).encode('utf-8'),hashlib.sha256).hexdigest()
