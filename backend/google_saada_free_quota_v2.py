"""Free GoogleSaada anonymous request ledger. Best effort without login.

Uses atomic SQLite transactions and signed BrowserState identifiers. A user
may evade best-effort limits by resetting browser storage or changing devices.
Ephemeral Space storage also resets on rebuild; /data persistent is preferred.
"""
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
import hashlib,hmac,os,secrets,sqlite3,time

DAY_MAX=20
_LOCK=RLock()
_RUNTIME_SECRET=secrets.token_bytes(32)

def _path():
    preferred=os.environ.get('GS_LEDGER_PATH','').strip()
    if preferred:return Path(preferred)
    if Path('/data').is_dir():return Path('/data/googlesaada_quota.sqlite3')
    return Path('/tmp/googlesaada_quota.sqlite3')

def persistent():return str(_path()).startswith('/data/') and Path('/data').is_dir()

def _secret():
    configured=os.environ.get('GS_ANON_SECRET','')
    return configured.encode() if len(configured)>=32 else _RUNTIME_SECRET

def identity(token):
    if isinstance(token,str) and len(token)<200 and '.' in token:
        uid,mac=token.split('.',1)
        if len(uid)==32 and all(ch in '0123456789abcdef' for ch in uid):
            sig=hmac.new(_secret(),uid.encode(),'sha256').hexdigest()
            if hmac.compare_digest(mac,sig):return uid,token
    uid=secrets.token_hex(16)
    mac=hmac.new(_secret(),uid.encode(),'sha256').hexdigest()
    return uid,uid+'.'+mac

def _db():
    p=_path();p.parent.mkdir(parents=True,exist_ok=True)
    conn=sqlite3.connect(str(p),timeout=15,isolation_level=None)
    conn.execute('PRAGMA busy_timeout=15000')
    conn.execute('CREATE TABLE IF NOT EXISTS gs_usage(day TEXT NOT NULL, uid TEXT NOT NULL, used INTEGER NOT NULL, PRIMARY KEY(day,uid))')
    conn.execute('CREATE TABLE IF NOT EXISTS gs_reservations(id TEXT PRIMARY KEY, day TEXT, uid TEXT, reserved INTEGER, created REAL)')
    conn.execute('CREATE TABLE IF NOT EXISTS gs_gpt_cost(day TEXT PRIMARY KEY, count INTEGER NOT NULL)')
    return conn

def _today():return datetime.now(timezone.utc).strftime('%Y-%m-%d')

def remaining(uid):
    with _LOCK:
        db=_db()
        try:
            row=db.execute('SELECT used FROM gs_usage WHERE day=? AND uid=?',(_today(),uid)).fetchone()
            return max(0,DAY_MAX-(row[0] if row else 0))
        finally:db.close()

def reserve(uid,amount):
    if isinstance(amount,bool) or not isinstance(amount,int) or not 1<=amount<=DAY_MAX:
        raise ValueError('Invalid analysis request count')
    db=_db();day=_today();rid=secrets.token_hex(16)
    try:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT used FROM gs_usage WHERE day=? AND uid=?',(day,uid)).fetchone()
        used=row[0] if row else 0
        if used+amount>DAY_MAX:
            db.rollback();return None
        db.execute('INSERT INTO gs_usage(day,uid,used) VALUES(?,?,?) ON CONFLICT(day,uid) DO UPDATE SET used=excluded.used',(day,uid,used+amount))
        db.execute('INSERT INTO gs_reservations VALUES(?,?,?,?,?)',(rid,day,uid,amount,time.time()))
        db.commit();return rid
    except Exception:
        db.rollback();raise
    finally:db.close()

def settle(rid,actual):
    db=_db()
    try:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT day,uid,reserved FROM gs_reservations WHERE id=?',(rid,)).fetchone()
        if not row:raise RuntimeError('Quota reservation missing')
        day,uid,reserved=row
        actual=max(0,min(reserved,int(actual)))
        db.execute('UPDATE gs_usage SET used=used-? WHERE day=? AND uid=?',(reserved-actual,day,uid))
        db.execute('DELETE FROM gs_reservations WHERE id=?',(rid,))
        db.commit()
    except Exception:db.rollback();raise
    finally:db.close()

def reserve_gpt_global():
    # A global (not per-agent-user) daily API cost-control cap.
    # No paid API traffic on ephemeral storage or without opted-in controls.
    if not persistent():return False
    if os.environ.get('GS_GPT_INTENT_ENABLED')!='true':return False
    if os.environ.get('GS_PROVIDER_RATES_VERIFIED')!='1':return False
    if not os.environ.get('OPENAI_API_KEY'):return False
    cap=int(os.environ.get('GS_MAX_GPT_CALLS_PER_DAY','50'))
    cap=max(1,min(cap,100))
    db=_db();day=_today()
    try:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT count FROM gs_gpt_cost WHERE day=?',(day,)).fetchone()
        count=row[0] if row else 0
        if count>=cap:db.rollback();return False
        db.execute('INSERT INTO gs_gpt_cost(day,count) VALUES(?,?) ON CONFLICT(day) DO UPDATE SET count=excluded.count',(day,count+1))
        db.commit();return True
    except Exception:db.rollback();raise
    finally:db.close()
