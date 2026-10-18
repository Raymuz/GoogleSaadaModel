"""GoogleSaada free account service. Postgres required outside isolated tests."""
from __future__ import annotations
import os, re, secrets
from datetime import datetime, timezone
from sqlalchemy import create_engine, Column, Integer, String, DateTime, UniqueConstraint, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.exc import IntegrityError
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError

class Base(DeclarativeBase): pass
class User(Base):
    __tablename__='gs_users'
    id=Column(Integer,primary_key=True)
    email=Column(String(320),unique=True,nullable=False)
    pwd_hash=Column(String(512),nullable=True)
    age_at_signup=Column(Integer,nullable=False)
    provider=Column(String(32),nullable=False,default='native')
    hf_sub=Column(String(256),unique=True,nullable=True)
    email_verified=Column(Integer,nullable=False,default=0)
    created_at=Column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))
class DailyQuota(Base):
    __tablename__='gs_daily_quota'
    user_id=Column(Integer,primary_key=True)
    day_utc=Column(String(10),primary_key=True)
    used=Column(Integer,nullable=False,default=0)

HASHER=PasswordHasher(time_cost=3,memory_cost=65536,parallelism=2)
def db_engine(url=None, testing=False):
    url=url or os.getenv('DATABASE_URL','')
    if not url:raise RuntimeError('DATABASE_URL required: use private staging PostgreSQL')
    if not testing and not url.startswith(('postgresql+psycopg://','postgresql://')):
        raise RuntimeError('Production account persistence requires PostgreSQL')
    return create_engine(url,pool_pre_ping=True)
def make_session(engine):return sessionmaker(bind=engine,expire_on_commit=False)
def initialize(engine):Base.metadata.create_all(engine)
def normalized_email(email):
    s=(email or '').strip().lower()
    if len(s)>320 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',s):raise ValueError('Invalid email')
    return s
def register(db,email,password,age):
    email=normalized_email(email)
    if len(password or '')<12 or len(password)>256:raise ValueError('Password must contain 12–256 characters')
    if isinstance(age,bool) or not isinstance(age,int) or age<18 or age>120:raise ValueError('Registration requires age 18+')
    # Account is not email-verified until user completes a verified email flow.
    u=User(email=email,pwd_hash=HASHER.hash(password),age_at_signup=age,provider='native',email_verified=0)
    db.add(u)
    try:db.commit()
    except IntegrityError:db.rollback();raise ValueError('Account cannot be created for this email')
    return u
def authenticate(db,email,password):
    try:email=normalized_email(email)
    except ValueError:return None
    u=db.query(User).filter(User.email==email,User.pwd_hash.isnot(None)).one_or_none()
    if not u:return None
    try:
        if HASHER.verify(u.pwd_hash,password or ''):return u
    except (VerifyMismatchError,VerificationError):pass
    return None

def oauth_signin(db,subject,email,verified,age):
    # Never link a Hugging Face identity to an existing email-only account automatically.
    if not subject or len(str(subject))>256 or not verified:raise ValueError('Verified OAuth identity required')
    email=normalized_email(email)
    u=db.query(User).filter(User.hf_sub==str(subject)).one_or_none()
    if u:return u
    if db.query(User).filter(User.email==email).first():
        raise ValueError('Email already registered. Log in through its original method; linking requires a separate verified flow.')
    if isinstance(age,bool) or not isinstance(age,int) or not (18<=age<=120):
        raise ValueError('Age confirmation 18+ is required on first OAuth sign-in')
    u=User(email=email,pwd_hash=None,age_at_signup=age,provider='huggingface',hf_sub=str(subject),email_verified=1)
    db.add(u)
    try:db.commit()
    except IntegrityError:db.rollback();raise ValueError('Identity already registered')
    return u

def remaining(db,user_id,day=None):
    day=day or datetime.now(timezone.utc).date().isoformat()
    q=db.get(DailyQuota,(user_id,day))
    return max(0,10-(q.used if q else 0))

def reserve_one(engine,user_id,day=None,release_approved=False):
    # Production gate is intentionally fail-closed. Call ONLY once per authorized prediction.
    if not release_approved or os.getenv('GOOGLESAADA_PUBLIC_PREDICTIONS_ENABLED','false').lower()!='true':
        raise PermissionError('Predictions remain locked until Judge and OOS acceptance')
    day=day or datetime.now(timezone.utc).date().isoformat()
    with engine.begin() as conn:
        if engine.dialect.name!='postgresql':raise RuntimeError('Atomic credit reservations require PostgreSQL')
        row=conn.execute(text("""INSERT INTO gs_daily_quota (user_id,day_utc,used)
            VALUES (:uid,:day,1) ON CONFLICT (user_id,day_utc)
            DO UPDATE SET used=gs_daily_quota.used+1 WHERE gs_daily_quota.used<10
            RETURNING used"""),{'uid':user_id,'day':day}).first()
        if row is None:raise PermissionError('Daily allowance exhausted')
        return 10-row[0]
