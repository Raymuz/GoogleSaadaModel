"""Expiring and single-use verification and recovery operations."""
from __future__ import annotations
import os, hashlib, secrets, smtplib, ssl
from datetime import datetime, timezone, timedelta
from email.message import EmailMessage
from sqlalchemy import Column, String, Integer, DateTime, select, update
from sqlalchemy.orm import declarative_base

TokenBase=declarative_base()
class GSOneTimeToken(TokenBase):
    __tablename__='gs_one_time_tokens'
    token_sha256=Column(String(64),primary_key=True)
    user_id=Column(Integer,nullable=False,index=True)
    purpose=Column(String(24),nullable=False,index=True)
    expires_at=Column(DateTime(timezone=True),nullable=False)
    consumed_at=Column(DateTime(timezone=True))

def digest(raw):return hashlib.sha256(raw.encode('utf-8')).hexdigest()
def utcnow():return datetime.now(timezone.utc)
def normalize_dt(value):return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
def issue(db,user_id,purpose,minutes=30):
    if purpose not in {'verify_email','reset_password'}:raise ValueError('Invalid purpose')
    if not (1<=minutes<=60):raise ValueError('Invalid token TTL')
    raw=secrets.token_urlsafe(48)
    row=GSOneTimeToken(token_sha256=digest(raw),user_id=user_id,purpose=purpose,
        expires_at=utcnow()+timedelta(minutes=minutes))
    db.add(row);db.commit()
    return raw

def consume(db,raw,purpose):
    # Conditional UPDATE makes consumption atomic across workers and processes.
    now=utcnow()
    result=db.execute(update(GSOneTimeToken).where(
      GSOneTimeToken.token_sha256==digest(raw or ''),
      GSOneTimeToken.purpose==purpose,
      GSOneTimeToken.consumed_at.is_(None),
      GSOneTimeToken.expires_at>now
    ).values(consumed_at=now).returning(GSOneTimeToken.user_id))
    user_id=result.scalar_one_or_none()
    db.commit()
    return user_id

def send_mail(to,subject,body):
    from googlesaada_hostinger_mail import send_google_saada_mail
    return send_google_saada_mail(to,subject,body)

def public_url(path):
    base=os.getenv('GOOGLESAADA_PUBLIC_BASE_URL','').rstrip('/')
    if not base.startswith('https://'):raise RuntimeError('HTTPS public base URL required')
    return base+path
