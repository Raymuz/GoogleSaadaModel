"""Cross-worker PostgreSQL auth abuse rate-limiting with atomic UPSERT."""
from __future__ import annotations
from sqlalchemy import Column,String,Integer,DateTime,text
from sqlalchemy.orm import declarative_base
from datetime import datetime,timezone
import hashlib,hmac,os
LimitBase=declarative_base()
class GSLoginLimit(LimitBase):
    __tablename__='gs_login_limits'
    key_hash=Column(String(64),primary_key=True)
    window_utc=Column(String(16),nullable=False)
    attempts=Column(Integer,nullable=False,default=0)

def check_and_count(db,key,limit=10):
    """Count each attempt per minute in durable Postgres. Fail closed without database."""
    if db.bind.dialect.name!='postgresql':raise RuntimeError('PostgreSQL required for live rate limits')
    salt=os.getenv('GS_RATE_LIMIT_SECRET','')
    if len(salt)<32:raise RuntimeError('GS_RATE_LIMIT_SECRET missing')
    hashed=hmac.new(salt.encode(),key.encode(),hashlib.sha256).hexdigest()
    window=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M')
    row=db.execute(text("""INSERT INTO gs_login_limits(key_hash,window_utc,attempts)
      VALUES (:k,:w,1) ON CONFLICT(key_hash) DO UPDATE SET
      window_utc=excluded.window_utc,
      attempts=CASE WHEN gs_login_limits.window_utc=excluded.window_utc
        THEN gs_login_limits.attempts+1 ELSE 1 END
      RETURNING attempts"""),{'k':hashed,'w':window}).scalar_one()
    db.commit()
    return row<=limit
