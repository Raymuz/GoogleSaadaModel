
from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.orm import declarative_base
from datetime import datetime,timezone
from secrets import token_urlsafe
import hashlib
from fastapi import HTTPException
SessionBase=declarative_base()
class GSAuthSession(SessionBase):
    __tablename__='gs_auth_sessions'
    id=Column(String(64),primary_key=True)
    user_id=Column(Integer,nullable=False,index=True)
    expires_utc=Column(DateTime(timezone=True),nullable=False)
    revoked=Column(Integer,default=0,nullable=False)

def hash_session(raw): return hashlib.sha256(raw.encode()).hexdigest()
def create_session(db,user_id):
    from datetime import timedelta
    raw=token_urlsafe(48)
    db.add(GSAuthSession(id=hash_session(raw),user_id=int(user_id),expires_utc=datetime.now(timezone.utc)+timedelta(days=7),revoked=0))
    db.commit()
    return raw
def get_session_user(db,raw):
    if not raw:return None
    record=db.get(GSAuthSession,hash_session(raw))
    if not record or record.revoked or record.expires_utc.replace(tzinfo=timezone.utc)<=datetime.now(timezone.utc):return None
    return record.user_id
def revoke_session(db,raw):
    if raw:
        record=db.get(GSAuthSession,hash_session(raw))
        if record:record.revoked=1;db.commit()
