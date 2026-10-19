
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from collections import defaultdict,deque
from time import monotonic
from secrets import token_urlsafe,compare_digest
from itsdangerous import URLSafeTimedSerializer, BadSignature
import os
AUTH_WRITE={'/register','/login','/logout','/oauth/finish'}
_LIMITS=defaultdict(deque)
class GSAccountSecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self,request:Request,call_next):
        path=request.url.path
        if path in AUTH_WRITE or path.startswith('/oauth/huggingface'):
            # Peer IP is not identity. Never trust forwarded headers by default.
            key=(request.client.host if request.client else 'unknown',path)
            now=monotonic()
            q=_LIMITS[key]
            while q and q[0]<now-60:q.popleft()
            limit=10 if path in {'/login','/register'} else 20
            if len(q)>=limit:return JSONResponse({'error':'Too many requests'},status_code=429)
            q.append(now)
        if request.method in {'POST','PUT','PATCH','DELETE'} and path in AUTH_WRITE:
            from urllib.parse import parse_qs
            raw=await request.body()
            if len(raw)>8192:return JSONResponse({'error':'Payload too large'},status_code=413)
            parsed=parse_qs(raw.decode('utf-8','replace'),max_num_fields=12)
            submitted=(parsed.get('csrf_token') or [''])[0]
            cookie=request.cookies.get('gs_csrf','')
            if not cookie or not submitted or not compare_digest(cookie,submitted):
                return JSONResponse({'error':'Invalid CSRF token'},status_code=403)
            origin=request.headers.get('origin')
            if origin:
                from urllib.parse import urlparse
                from urllib.parse import urlparse as parse
                requested=parse(str(request.base_url))
                supplied=parse(origin)
                if supplied.scheme!=requested.scheme or supplied.netloc!=requested.netloc:
                    return JSONResponse({'error':'Untrusted Origin'},status_code=403)
        response=await call_next(request)
        if request.method=='GET' and path=='/account':
            # Set token on public account GET. Frontend receives token through placeholder.
            pass
        return response
