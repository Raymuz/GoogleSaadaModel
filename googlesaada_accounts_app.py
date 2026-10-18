"""Standalone Stage8E FastAPI + Gradio account gateway; no WordPress."""
from __future__ import annotations
import os, re, secrets, html
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from starlette.middleware.sessions import SessionMiddleware
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
import gradio as gr
from googlesaada_account_service import db_engine,make_session,initialize,register,authenticate,oauth_signin,remaining,User,normalized_email
from googlesaada_auth_sessions import GSAuthSession,SessionBase,create_session,get_session_user,revoke_session
from googlesaada_account_security import GSAccountSecurityMiddleware
from googlesaada_unified_app import _create_blocks

SECRET=os.getenv('GOOGLESAADA_SESSION_SECRET','')
if len(SECRET)<40:raise RuntimeError('GOOGLESAADA_SESSION_SECRET must be configured (40+ characters)')
DB=db_engine()
# schema creation performed separately by controlled migration; not implicit on every public process startup.
Session=make_session(DB)
serializer=URLSafeTimedSerializer(SECRET,salt='gs-v8-session')
COOKIE='gs_v8_auth'
SECURE=os.getenv('GOOGLESAADA_COOKIE_SECURE','true').lower()=='true'

from googlesaada_auth_tokens import TokenBase,issue,send_mail,public_url
from googlesaada_persistent_limits import LimitBase,check_and_count
from fastapi import APIRouter

app=FastAPI(title='GoogleSaada V8 Accounts',docs_url=None,redoc_url=None,openapi_url=None)
app.add_middleware(SessionMiddleware,secret_key=SECRET,https_only=SECURE,same_site='lax')
app.add_middleware(GSAccountSecurityMiddleware)

def current_user(request:Request):
    value=request.cookies.get(COOKIE,'')
    with Session() as db:
        user_id=get_session_user(db,value)
        if user_id is None:return None
        u=db.get(User,user_id)
        if not u or not u.email_verified:return None
        return str(u.id)

def auth_dependency(request:Request):return current_user(request)
def logged_in(request:Request):
    u=current_user(request)
    if not u:raise HTTPException(status_code=401,detail='Login required')
    return int(u)

def apply_cookie(resp,user_id):
    with Session() as db: ticket=create_session(db,user_id)
    resp.set_cookie(COOKIE,ticket,max_age=604800,httponly=True,secure=SECURE,samesite='lax',path='/')

def form_page(message='',csrf_token=''):
    msg='<p role="alert">'+html.escape(message)+'</p>' if message else ''
    csrf_input='<input type="hidden" name="csrf_token" value="'+html.escape(csrf_token,quote=True)+'">'
    return HTMLResponse(("""<!doctype html><html><head><title>GoogleSaada Free Account</title><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{font:16px system-ui;max-width:520px;margin:4rem auto;padding:1rem;background:#f8fafc;color:#111827}section{background:white;padding:1.3rem;border-radius:15px;margin-bottom:1rem}input,button{padding:.7rem;margin:.4rem 0;box-sizing:border-box;width:100%}button{background:#1f3a60;color:white;border:0;border-radius:6px}</style></head><body><h1>GoogleSaada</h1><p>Free account · No credit card · 18+</p>"""+msg+"""<section><h2>Create account</h2><form method="post" action="/register">{CSRF_HIDDEN}<input name="email" type="email" autocomplete="email" placeholder="Email" required><input name="password" type="password" minlength="12" autocomplete="new-password" placeholder="Password (12+ characters)" required><input name="age" type="number" min="18" max="120" placeholder="Age" required><button>Create free account</button></form></section><section><h2>Sign in</h2><form method="post" action="/login">{CSRF_HIDDEN}<input name="email" type="email" autocomplete="email" placeholder="Email" required><input name="password" type="password" autocomplete="current-password" placeholder="Password" required><button>Sign in</button></form></section><section><h2>Hugging Face account</h2><a href="/oauth/huggingface">Sign in with Hugging Face</a><p>First-time OAuth users confirm their age before the account is created.</p></section></body></html>""").replace('{CSRF_HIDDEN}',csrf_input))

@app.get('/health')
def health():return {'status':'ok','service':'googlesaada-v8-accounts','wordpress':False,'predictions_enabled':False}
@app.get('/')
def home(request:Request):return RedirectResponse('/app' if current_user(request) else '/account',status_code=302)
@app.get('/account')
def account():
    token=secrets.token_urlsafe(32)
    page=form_page(csrf_token=token)
    page.set_cookie('gs_csrf',token,max_age=3600,httponly=True,secure=SECURE,samesite='lax',path='/')
    return page
@app.post('/register')
def signup(email:str=Form(...),password:str=Form(...),age:int=Form(...)):
    try:
        with Session() as db:
            if not os.getenv('GS_HOSTINGER_API_TOKEN') or not os.getenv('GS_MAIL_FROM') or not os.getenv('GS_ACTIVATION_PEPPER'):
                raise HTTPException(503,'Registration unavailable until secure mail delivery and activation are configured')
            if not check_and_count(db,'register|'+email.lower(),limit=6):
                raise HTTPException(429,'Too many registration attempts')
            u=register(db,email,password,age);uid=u.id
            code=issue_code(db,uid)
            try:
                send_google_saada_mail(u.email,'Your GoogleSaada activation code',
                    'Your GoogleSaada activation code is: '+code+'\nIt expires in 10 minutes. Do not share it.')
            except Exception:
                # Account remains inactive; no session issued. Avoid exposing credentials/provider messages.
                raise HTTPException(503,'Unable to send activation email. Please contact support.')
    except ValueError as ex:return form_page(str(ex))
    # Account email verification is pending; session for free non-prediction features.
    return RedirectResponse('/account/activate',status_code=303)
@app.post('/login')
def login(email:str=Form(...),password:str=Form(...)):
    with Session() as db:
        if not check_and_count(db,'login|'+email.lower(),limit=8):
            raise HTTPException(429,'Too many login attempts')
        u=authenticate(db,email,password)
    if not u:return form_page('Invalid email or password')
    resp=RedirectResponse('/app',status_code=303);apply_cookie(resp,u.id);return resp
@app.post('/logout')
def logout(request:Request):
    with Session() as db: revoke_session(db,request.cookies.get(COOKIE,''))
    resp=RedirectResponse('/account',status_code=303);resp.delete_cookie(COOKIE,path='/');return resp
@app.get('/api/v1/account')
def profile(request:Request):
    uid=logged_in(request)
    with Session() as db:
        u=db.get(User,uid)
        return {'email':u.email,'provider':u.provider,'email_verified':bool(u.email_verified),'age_eligible':u.age_at_signup>=18,'daily_free_limit':10,'predictions_remaining':remaining(db,uid),'prediction_api_enabled':False}
@app.get('/api/v1/predict')
def locked_predict():return JSONResponse(status_code=503,content={'error':'Predictions disabled until native Judge and calibration acceptance'})

# OAuth: Authlib validates state and provider token. HF profile email must be verified.
@app.get('/oauth/huggingface')
async def hf_start(request:Request):
    from authlib.integrations.starlette_client import OAuth
    client_id=os.getenv('HF_OAUTH_CLIENT_ID','');client_secret=os.getenv('HF_OAUTH_CLIENT_SECRET','')
    callback=os.getenv('GOOGLESAADA_OAUTH_REDIRECT_URI','')
    if not all((client_id,client_secret,callback)):raise HTTPException(503,'Hugging Face OAuth not configured')
    oauth=OAuth();oauth.register(name='hf',client_id=client_id,client_secret=client_secret,
      authorize_url='https://huggingface.co/oauth/authorize',access_token_url='https://huggingface.co/oauth/token',
      client_kwargs={'scope':'openid profile email'},server_metadata_url='https://huggingface.co/.well-known/openid-configuration')
    return await oauth.hf.authorize_redirect(request,callback)
@app.get('/oauth/huggingface/callback')
async def hf_callback(request:Request):
    from authlib.integrations.starlette_client import OAuth
    client_id=os.getenv('HF_OAUTH_CLIENT_ID','');client_secret=os.getenv('HF_OAUTH_CLIENT_SECRET','')
    if not client_id or not client_secret:raise HTTPException(503,'OAuth not configured')
    oauth=OAuth();oauth.register(name='hf',client_id=client_id,client_secret=client_secret,
      authorize_url='https://huggingface.co/oauth/authorize',access_token_url='https://huggingface.co/oauth/token',
      client_kwargs={'scope':'openid profile email'},server_metadata_url='https://huggingface.co/.well-known/openid-configuration')
    try:
        token=await oauth.hf.authorize_access_token(request)
        info=token.get('userinfo') or await oauth.hf.userinfo(token=token)
        subject=str(info.get('sub') or '')
        email=str(info.get('email') or '')
        verified=info.get('email_verified') is True
        if not subject or not verified:raise ValueError('Verified email and subject required from Hugging Face')
    except Exception:raise HTTPException(401,'OAuth validation failed')
    with Session() as db:
        existing=db.query(User).filter(User.hf_sub==subject).one_or_none()
        if existing:
            resp=RedirectResponse('/app',status_code=303);apply_cookie(resp,existing.id);return resp
    # Temporary age-confirmation state; signed and expires quickly. Never stores access tokens.
    request.session['oauth_pending']={'sub':subject,'email':email,'verified':True,'issued':datetime.now(timezone.utc).timestamp()}
    csrf=secrets.token_urlsafe(32)
    page=HTMLResponse('<h2>Confirm your age to create your free account</h2><form method="post" action="/oauth/finish"><input type="hidden" name="csrf_token" value="'+html.escape(csrf,quote=True)+'"><input type="number" name="age" min="18" max="120" required><button>Continue</button></form>')
    page.set_cookie('gs_csrf',csrf,max_age=600,httponly=True,secure=SECURE,samesite='lax',path='/')
    return page
@app.post('/oauth/finish')
def oauth_finish(request:Request,age:int=Form(...)):
    pending=request.session.pop('oauth_pending',None)
    if not pending or datetime.now(timezone.utc).timestamp()-pending.get('issued',0)>600:
        raise HTTPException(401,'OAuth confirmation expired')
    try:
        with Session() as db:u=oauth_signin(db,pending['sub'],pending['email'],pending['verified'],age);uid=u.id
    except ValueError as ex:return form_page(str(ex))
    resp=RedirectResponse('/app',status_code=303);apply_cookie(resp,uid);return resp

"""Stage 8G account routes, included in the existing FastAPI gateway."""
import os,secrets,html
from fastapi import APIRouter,Request,Form,HTTPException
from fastapi.responses import HTMLResponse,RedirectResponse
from sqlalchemy import update
from googlesaada_account_service import User,HASHER,normalized_email
from googlesaada_auth_tokens import issue,consume,send_mail,public_url
from googlesaada_persistent_limits import check_and_count

router=APIRouter()

def _safe_key(request,email,purpose):
    peer=request.client.host if request.client else 'unknown'
    return purpose+'|'+peer+'|'+str(email or '').strip().lower()

def _gate(request,email,purpose):
    with Session() as db:
        if not check_and_count(db,_safe_key(request,email,purpose),limit=6):
            raise HTTPException(429,'Too many attempts. Try again later.')

def _csrf(request,submitted):
    from urllib.parse import urlsplit
    origin=request.headers.get('origin')
    if origin:
        o=urlsplit(origin); target=urlsplit(str(request.base_url))
        if (o.scheme,o.netloc)!=(target.scheme,target.netloc):
            raise HTTPException(403,'Untrusted origin')
    from secrets import compare_digest
    expected=request.cookies.get('gs_csrf','')
    if not expected or not submitted or not compare_digest(expected,submitted):
        raise HTTPException(403,'Invalid CSRF token')

def _token_form(action,heading,fields):
    token=secrets.token_urlsafe(32)
    body='<h2>'+html.escape(heading)+'</h2><form method="post" action="'+action+'">'
    body+='<input type="hidden" name="csrf_token" value="'+html.escape(token,quote=True)+'">'
    body+=fields+'<button type="submit">Continue</button></form>'
    response=HTMLResponse(body)
    response.set_cookie('gs_csrf',token,max_age=900,httponly=True,secure=SECURE,samesite='lax',path='/')
    return response

@router.get('/account/forgot')
def forgot_form():
    return _token_form('/account/forgot','Reset your password','<input type="email" name="email" required>')

@router.post('/account/forgot')
def forgot(request:Request,email:str=Form(...),csrf_token:str=Form(...)):
    _csrf(request,csrf_token);_gate(request,email,'reset')
    # Never disclose whether an account exists. Tokens do not appear in logs.
    with Session() as db:
        try:clean=normalized_email(email)
        except ValueError:clean=''
        u=db.query(User).filter(User.email==clean,User.pwd_hash.isnot(None)).first() if clean else None
        if u:
            raw=issue(db,u.id,'reset_password',minutes=20)
            send_mail(u.email,'GoogleSaada password reset',
                'To reset your password, open '+public_url('/account/reset?token='+raw))
    return HTMLResponse('If an eligible account exists, a recovery message has been sent.')

@router.get('/account/reset')
def reset_form(token:str=''):
    if not token or len(token)>300:raise HTTPException(400,'Invalid recovery link')
    return _token_form('/account/reset','Choose new password',
        '<input type="hidden" name="token" value="'+html.escape(token,quote=True)+'">'+
        '<input type="password" name="password" minlength="12" required>')

@router.post('/account/reset')
def reset(request:Request,token:str=Form(...),password:str=Form(...),csrf_token:str=Form(...)):
    _csrf(request,csrf_token);_gate(request,'reset_token','reset_submit')
    if not 12<=len(password)<=256:raise HTTPException(400,'Invalid password length')
    with Session() as db:
        user_id=consume(db,token,'reset_password')
        if user_id is None:raise HTTPException(400,'Recovery link invalid or expired')
        user=db.get(User,user_id)
        if user is None:raise HTTPException(400,'Account not found')
        user.pwd_hash=HASHER.hash(password);db.commit()
        # Revoke active sessions after a password change.
        from googlesaada_auth_sessions import GSAuthSession
        db.query(GSAuthSession).filter(GSAuthSession.user_id==user_id).update({'revoked':1})
        db.commit()
    return RedirectResponse('/account',status_code=303)

@router.get('/account/verify')
def verify(token:str=''):
    if not token or len(token)>300:raise HTTPException(400,'Invalid verification link')
    with Session() as db:
        uid=consume(db,token,'verify_email')
        if uid is None:raise HTTPException(400,'Verification link invalid or expired')
        user=db.get(User,uid)
        if not user:raise HTTPException(400,'Account not found')
        user.email_verified=1;db.commit()
    return RedirectResponse('/account',status_code=303)

@router.get('/account/resend')
def resend_form():
    return _token_form('/account/resend','Resend verification','<input type="email" name="email" required>')

@router.post('/account/resend')
def resend(request:Request,email:str=Form(...),csrf_token:str=Form(...)):
    _csrf(request,csrf_token);_gate(request,email,'verify')
    with Session() as db:
        try:clean=normalized_email(email)
        except ValueError:clean=''
        user=db.query(User).filter(User.email==clean,User.email_verified==0).first() if clean else None
        if user:
            raw=issue(db,user.id,'verify_email',minutes=30)
            send_mail(user.email,'Verify GoogleSaada email',
                'Verify your free account: '+public_url('/account/verify?token='+raw))
    return HTMLResponse('If verification is needed, a message has been sent.')

app.include_router(router)


@app.get('/api/v1/admin/me')
def admin_me(request:Request):
    uid=logged_in(request)
    with Session() as db:
        user=db.get(User,uid)
        owner=os.getenv('GS_HF_OWNER_SUBJECT','')
        if not owner or user.provider!='huggingface' or not user.hf_sub or user.hf_sub!=owner:
            raise HTTPException(403,'Verified owner identity required')
        return {'role':'admin','authenticated':True,'provider':'huggingface'}


from googlesaada_activation_codes import GSActivation,ActivationBase,issue_code,verify_code
from googlesaada_hostinger_mail import send_google_saada_mail
from googlesaada_persistent_limits import check_and_count

def _activation_form(message=''):
    csrf=secrets.token_urlsafe(32)
    body='<h2>Activate your GoogleSaada account</h2><p>Enter the 10-digit code sent to your email. It expires in 10 minutes.</p>'
    if message:body+='<p role="alert">'+html.escape(message)+'</p>'
    body+='<form method="post" action="/account/activate"><input type="hidden" name="csrf_token" value="'+html.escape(csrf,quote=True)+'"><input type="email" name="email" autocomplete="email" required placeholder="Email"><input type="text" name="code" inputmode="numeric" pattern="[0-9]{10}" maxlength="10" required placeholder="10-digit code"><button type="submit">Activate</button></form>'
    response=HTMLResponse(body)
    response.set_cookie('gs_csrf',csrf,max_age=900,httponly=True,secure=SECURE,samesite='lax',path='/')
    return response

@app.get('/account/activate')
def activation_page():
    return _activation_form()

@app.post('/account/activate')
def activation_submit(request:Request,email:str=Form(...),code:str=Form(...),csrf_token:str=Form(...)):
    _csrf(request,csrf_token)
    # Shared-database limiter; same generic error whether email is unknown or incorrect.
    with Session() as db:
        if not check_and_count(db,'activation|'+email.strip().casefold(),limit=8):
            raise HTTPException(429,'Too many attempts; try again later')
        try: normalized=normalized_email(email)
        except ValueError:normalized=''
        user=db.query(User).filter(User.email==normalized,User.provider=='native').one_or_none() if normalized else None
        if not user or bool(user.email_verified) or not verify_code(db,user.id,code):
            return _activation_form('Invalid or expired activation code')
        user.email_verified=1
        db.commit()
        uid=user.id
    response=RedirectResponse('/app',status_code=303)
    apply_cookie(response,uid)
    return response

# Gradio is mounted at /app; its own API and static paths are covered by its auth_dependency.
app=gr.mount_gradio_app(app,_create_blocks(),path='/app',auth_dependency=auth_dependency)
