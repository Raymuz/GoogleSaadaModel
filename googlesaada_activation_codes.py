"""Single-use ten-digit email challenge backed by transactional PostgreSQL."""
from __future__ import annotations
import os, secrets, hmac, hashlib
from datetime import datetime,timezone,timedelta
from sqlalchemy import Column,Integer,String,DateTime,update,select
from sqlalchemy.orm import declarative_base

ActivationBase=declarative_base()
class GSActivation(ActivationBase):
    __tablename__='gs_email_activation_codes'
    user_id=Column(Integer,primary_key=True)
    digest=Column(String(64),nullable=False)
    expires_at=Column(DateTime(timezone=True),nullable=False)
    attempts=Column(Integer,nullable=False,default=0)
    consumed_at=Column(DateTime(timezone=True))

def _pepper():
    pepper=os.getenv('GS_ACTIVATION_PEPPER','')
    if len(pepper)<32:raise RuntimeError('GS_ACTIVATION_PEPPER must be at least 32 characters')
    return pepper

def _digest(user_id,code):
    return hmac.new(_pepper().encode(),f'activation:{user_id}:{code}'.encode(),hashlib.sha256).hexdigest()

def issue_code(db,user_id):
    """Issue a new challenge; call only after a native account is persisted."""
    code=f'{secrets.randbelow(10**10):010d}'
    now=datetime.now(timezone.utc)
    challenge=db.get(GSActivation,user_id)
    if challenge is None:
        challenge=GSActivation(user_id=user_id)
        db.add(challenge)
    challenge.digest=_digest(user_id,code)
    challenge.expires_at=now+timedelta(minutes=10)
    challenge.attempts=0
    challenge.consumed_at=None
    db.commit()
    return code

def verify_code(db,user_id,code):
    """Each failed guess consumes one of five total attempts, atomically in PostgreSQL."""
    if not (isinstance(code,str) and len(code)==10 and code.isascii() and code.isdecimal()):
        return False
    now=datetime.now(timezone.utc)
    # Row lock serializes competing attempts; PostgreSQL required at deployment.
    row=db.execute(select(GSActivation).where(GSActivation.user_id==user_id).with_for_update()).scalar_one_or_none()
    if row is None or row.consumed_at is not None or row.attempts>=5 or row.expires_at.replace(tzinfo=row.expires_at.tzinfo or timezone.utc)<=now:
        db.rollback()
        return False
    row.attempts+=1
    ok=hmac.compare_digest(row.digest,_digest(user_id,code))
    if ok:row.consumed_at=now
    db.commit()
    return ok
