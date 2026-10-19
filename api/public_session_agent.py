"""Public research sessions; signed bearer tokens; SQLite quota & conversation history.
Anonymous session identity is not verified user authentication. No model inference authorization.
"""
from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime, timezone, date
from pathlib import Path
from contextlib import contextmanager
import sqlite3, os, secrets, hmac, hashlib, base64, json, re, time

router=APIRouter(prefix='/api/v1',tags=['public-research-sessions'])
MAX_DAILY=30
MAX_HISTORY=12
MAX_MESSAGE=1200
TTL=86400

def _secret():
 s=os.environ.get('GS_V8_SESSION_SECRET','')
 if len(s)<32: raise RuntimeError('GS_V8_SESSION_SECRET must be at least 32 chars')
 return s.encode()

def _dbpath():
 p=Path(os.environ.get('GS_V8_STATE_DB','/tmp/gs_v8_sessions.sqlite3'))
 p.parent.mkdir(parents=True,exist_ok=True)
 return p

@contextmanager
def _db():
 conn=sqlite3.connect(str(_dbpath()),timeout=20,isolation_level=None)
 try:
  conn.execute('PRAGMA busy_timeout=20000')
  conn.execute('PRAGMA journal_mode=WAL')
  conn.execute('CREATE TABLE IF NOT EXISTS sessions (sid TEXT PRIMARY KEY, created INTEGER NOT NULL, expires INTEGER NOT NULL)')
  conn.execute('CREATE TABLE IF NOT EXISTS usage (sid TEXT NOT NULL, day TEXT NOT NULL, count INTEGER NOT NULL, PRIMARY KEY(sid,day))')
  conn.execute('CREATE TABLE IF NOT EXISTS history (sid TEXT NOT NULL, seq INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT NOT NULL, content TEXT NOT NULL, created INTEGER NOT NULL)')
  yield conn
 finally: conn.close()

def _token(sid,expiry):
 value=f'{sid}.{expiry}'
 sig=hmac.new(_secret(),value.encode(),hashlib.sha256).hexdigest()
 return f'{value}.{sig}'

def _verify(auth):
 if not auth or not auth.startswith('Bearer '):raise HTTPException(status_code=401,detail='Public research session required')
 token=auth[7:]
 bits=token.split('.')
 if len(bits)!=3:raise HTTPException(status_code=401,detail='Invalid session')
 sid,expiry,sig=bits
 if not re.fullmatch('[a-f0-9]{32}',sid):raise HTTPException(status_code=401,detail='Invalid session')
 signed=f'{sid}.{expiry}'
 expected=hmac.new(_secret(),signed.encode(),hashlib.sha256).hexdigest()
 if not hmac.compare_digest(expected,sig):raise HTTPException(status_code=401,detail='Invalid session')
 try:expires=int(expiry)
 except ValueError:raise HTTPException(status_code=401,detail='Invalid session')
 if expires<=int(time.time()):raise HTTPException(status_code=401,detail='Session expired')
 with _db() as db:
  item=db.execute('SELECT expires FROM sessions WHERE sid=?',(sid,)).fetchone()
 if not item or item[0]!=expires:raise HTTPException(status_code=401,detail='Session revoked or expired')
 return sid

def _today():return datetime.now(timezone.utc).date().isoformat()

@router.post('/agent/public/session')
def begin_public_session():
 sid=secrets.token_hex(16);now=int(time.time());expiry=now+TTL
 with _db() as db:db.execute('INSERT INTO sessions(sid,created,expires) VALUES(?,?,?)',(sid,now,expiry))
 return {'access_token':_token(sid,expiry),'token_type':'bearer','expires_utc':datetime.fromtimestamp(expiry,timezone.utc).isoformat(),
         'verified_account':False,'daily_request_limit':MAX_DAILY,'research_only':True}

@router.get('/agent/public/quota')
def quota(authorization:str|None=Header(default=None)):
 sid=_verify(authorization)
 with _db() as db:
  row=db.execute('SELECT count FROM usage WHERE sid=? AND day=?',(sid,_today())).fetchone()
 used=row[0] if row else 0
 return {'used':used,'limit':MAX_DAILY,'remaining':max(0,MAX_DAILY-used),'reset_boundary':'UTC midnight','verified_account':False}

class ChatRequest(BaseModel):
 model_config=ConfigDict(extra='forbid')
 message:str=Field(min_length=2,max_length=MAX_MESSAGE)
 day:date|None=None
 league:str=Field(default='eng.1',max_length=20)
 time_zone:str=Field(default='Africa/Douala',max_length=64)

_INTENT_WORDS={
 'live_scores': ('live','score','playing now'),
 'results': ('result','finished','final','yesterday'),
 'fixtures': ('fixture','schedule','upcoming','tomorrow','today','matches'),
 'prediction_status': ('predict','probability','bet','odds','recommendation','pick'),
 'help': ('help','how to','basket','use')}

def _intent(msg,previous):
 text=msg.lower()
 for intent,words in _INTENT_WORDS.items():
  if any(w in text for w in words):return intent
 # Short followups stay within the previous explicit intent.
 return previous or 'help'

@router.post('/agent/public/conversation')
def converse(request:ChatRequest,authorization:str|None=Header(default=None)):
 sid=_verify(authorization)
 # Perform public football-data query BEFORE quota consumption; provider failures do not cost allowance.
 with _db() as db:
  rows=db.execute('SELECT role,content FROM history WHERE sid=? ORDER BY seq DESC LIMIT ?', (sid,MAX_HISTORY)).fetchall()
  previous=None
  for role,content in rows:
   if role=='intent':previous=content;break
 intent=_intent(request.message,previous)
 from api.public_agent_research import PublicAgentQuery,_answer
 if intent in ('fixtures','live_scores','results') and request.day is None:
  raise HTTPException(status_code=422,detail='Choose a date for fixtures, scores or results')
 q=PublicAgentQuery(intent=intent,day=request.day,league=request.league,time_zone=request.time_zone)
 answer=_answer(q)
 # Count requests atomically per day, with SQLite transaction.
 with _db() as db:
  db.execute('BEGIN IMMEDIATE')
  day=_today()
  row=db.execute('SELECT count FROM usage WHERE sid=? AND day=?',(sid,day)).fetchone()
  used=row[0] if row else 0
  if used>=MAX_DAILY:
   db.execute('ROLLBACK')
   raise HTTPException(status_code=429,detail='Public daily research quota reached')
  db.execute('INSERT INTO usage(sid,day,count) VALUES(?,?,1) ON CONFLICT(sid,day) DO UPDATE SET count=count+1',(sid,day))
  now=int(time.time())
  db.execute('INSERT INTO history(sid,role,content,created) VALUES(?,?,?,?)',(sid,'user',request.message,now))
  db.execute('INSERT INTO history(sid,role,content,created) VALUES(?,?,?,?)',(sid,'intent',intent,now))
  db.execute('INSERT INTO history(sid,role,content,created) VALUES(?,?,?,?)',(sid,'assistant',str(answer.get('message','')),now))
  # Bound history per session to last 36 entries
  db.execute('DELETE FROM history WHERE sid=? AND seq NOT IN (SELECT seq FROM history WHERE sid=? ORDER BY seq DESC LIMIT 36)',(sid,sid))
  db.execute('COMMIT')
 return {'intent':intent,'reply':answer.get('message',''),'matches':answer.get('matches',[]),'source':answer.get('evidence',{}),
         'turns_remembered':len(rows)//3,'remaining_today':MAX_DAILY-used-1,
         'research_only':True,'model_generated_text':False,'production_predictions_enabled':False,
         'identity_approved':False,'calibration_approved':False}
