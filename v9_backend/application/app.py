"""GoogleSaada V9 approved-layout frontend. Explicit feature flags protect inactive contracts."""
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse
import json, os, importlib.util, re, sqlite3, hashlib, threading
from datetime import datetime, timezone
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent
app = FastAPI(title="GoogleSaada V9 Frontend", docs_url=None, redoc_url=None)


# GOOGLESAADA V9 — ADDITIVE MODEL REGISTRY INTEGRATION
# Prediction execution remains disabled until qualification.
from gs_registry_router import router as gs_v9_registry_router
app.include_router(gs_v9_registry_router)
# END GOOGLESAADA V9 REGISTRY INTEGRATION

app.mount('/static', StaticFiles(directory=str(ROOT / 'ui')), name='static')
CONFIG = json.loads((ROOT / 'config' / 'contracts.json').read_text())

def agent_ready():
    return bool(os.getenv('OPENAI_API_KEY') and (ROOT / 'components/agent/v1/gs_agent.py').is_file())
ALLOWED = set(CONFIG['capabilities'])


# Anonymous limits are an interim safeguard, not a strong per-person identity.
# Use persistent /data mount or external Redis/DB before promising strict daily limits.
_QUOTA_LOCK = threading.Lock()
_QUOTA_DB = os.getenv('GS_QUOTA_DB', '/tmp/googlesaada_agent_quota.sqlite3')
_ANALYSIS_MARKERS = re.compile(r'\b(analy[sz]e|predict|prediction|match analysis|who (?:will|wins)|odds|bet|h2h|head.to.head)\b', re.I)
_MATCH_PATTERN = re.compile(r'\b(vs\.?|versus|against)\b', re.I)

def is_match_analysis(message, mode=None):
    # Never let a client claim 'chat' to exempt an unmistakable analysis request.
    return mode == 'analysis' or bool(_ANALYSIS_MARKERS.search(message) and _MATCH_PATTERN.search(message))

def _identity(request):
    # IP-based anonymous approximation; does not guarantee one person = one ID.
    # Avoid trusting caller-controlled X-Forwarded-For headers.
    host = request.client.host if request.client else 'unknown'
    salt = os.getenv('GS_QUOTA_SALT', 'googlesaada-local-testing')
    return hashlib.sha256((salt + ':' + host).encode()).hexdigest()

def _quota_op(request, bucket, limit, reserve=False):
    today = datetime.now(timezone.utc).date().isoformat()
    identity = _identity(request)
    with _QUOTA_LOCK:
        with sqlite3.connect(_QUOTA_DB, timeout=20) as db:
            db.execute('CREATE TABLE IF NOT EXISTS quota (day TEXT, identity TEXT, bucket TEXT, used INTEGER NOT NULL, PRIMARY KEY(day,identity,bucket))')
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT used FROM quota WHERE day=? AND identity=? AND bucket=?', (today,identity,bucket)).fetchone()
            used = int(row[0]) if row else 0
            if reserve and used < limit:
                used += 1
                db.execute('INSERT INTO quota(day,identity,bucket,used) VALUES (?,?,?,?) ON CONFLICT(day,identity,bucket) DO UPDATE SET used=excluded.used',(today,identity,bucket,used))
            db.commit()
    return used, max(0,limit-used), bool(reserve and row and int(row[0]) >= limit)

def remaining_quota(request, bucket, limit):
    return _quota_op(request,bucket,limit)[1]

def reserve_quota(request, analysis):
    bucket,limit=('analysis',5) if analysis else ('general_chat',20)
    used,left,blocked = _quota_op(request,bucket,limit,reserve=True)
    if blocked:
        raise HTTPException(429, 'Daily limit reached: 5 match analyses or 20 general chat messages. Try again after 00:00 UTC.')
    return used,left

@app.get('/', response_class=HTMLResponse)
def index():
    return (ROOT / 'ui' / 'index.html').read_text(encoding='utf-8')

@app.get('/api/ui/status')
def status():
    return {'frontend':'ready', 'backend':'not_verified',
            'capabilities':{k:{'enabled':(agent_ready() if k=='agent' else bool(v.get('enabled'))), 'method':v.get('method'), 'route':v.get('route')} for k,v in CONFIG['capabilities'].items()}}

@app.get('/api/ui/agent/quota')
def agent_quota(request: Request):
    return {'match_analysis_daily_limit': 5, 'remaining': remaining_quota(request,'analysis',5), 'reset_timezone':'UTC', 'identity':'anonymous-approximate'}

@app.post('/api/ui/agent')
async def agent_chat(request: Request):
    # Public Agent; API key remains server-side. Enforce server-side usage budgets.
    if not agent_ready():
        raise HTTPException(503, 'Agent not configured: OPENAI_API_KEY must be set as a Hugging Face Space secret')
    if request.headers.get('content-type', '').split(';')[0].strip() != 'application/json':
        raise HTTPException(415, 'JSON required')
    data = await request.body()
    if len(data)>20000: raise HTTPException(413, 'Agent request too large')
    try: payload=json.loads(data)
    except (ValueError,UnicodeDecodeError): raise HTTPException(400, 'Invalid JSON')
    if not isinstance(payload, dict): raise HTTPException(400, 'JSON object required')
    # Validate before reaching OpenAI.
    message=payload.get('message')
    history=payload.get('history', [])
    if not isinstance(message,str) or not message.strip() or len(message)>3000:
        raise HTTPException(400, 'Invalid message')
    if not isinstance(history,list) or len(history)>12:
        raise HTTPException(400, 'Invalid chat history')
    for item in history:
        if not isinstance(item,dict) or item.get('role') not in ('user','assistant') or not isinstance(item.get('content'),str) or len(item['content'])>3000:
            raise HTTPException(400, 'Invalid history item')
    analysis = is_match_analysis(message, payload.get('mode'))
    # Reserve quota before incurring provider cost; never trust client-provided counters.
    used, left = reserve_quota(request, analysis)
    def invoke():
        # Use the original verified gs_agent.py; no substitute GPT engine.
        from flask import Flask
        path=ROOT/'components/agent/v1/gs_agent.py'
        spec=importlib.util.spec_from_file_location('gs_original_agent',str(path))
        mod=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        flask_app=Flask('googlesaada_agent_private')
        mod.install_agent(flask_app)
        with flask_app.test_client() as client:
            resp=client.post('/v1/agent',json={'message':message,'history':history},headers={})
            return resp.status_code,resp.get_json(silent=True)
    try:
        status_code, result=await run_in_threadpool(invoke)
    except Exception:
        raise HTTPException(502, 'Agent service failed')
    if not isinstance(result,dict): raise HTTPException(502, 'Agent returned invalid JSON')
    result['daily_analysis_used'] = used if analysis else None
    result['daily_analysis_remaining'] = left if analysis else remaining_quota(request, 'analysis', 5)
    return JSONResponse(result,status_code=status_code)

@app.api_route('/api/ui/{capability}', methods=['GET','POST'])
async def proxy(capability: str, request: Request):
    if capability not in ALLOWED:
        raise HTTPException(404, 'Unknown capability')
    c = CONFIG['capabilities'][capability]
    if not c.get('enabled'):
        raise HTTPException(503, f'{capability} not activated: backend contract not verified')
    method = c['method'].upper()
    if request.method != method:
        raise HTTPException(405, 'Wrong method for capability')
    base = os.environ.get('GOOGLESAADA_API_BASE_URL', '').rstrip('/')
    parsed = urlparse(base)
    if parsed.scheme not in ('http','https') or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise HTTPException(503, 'Valid backend URL not configured')
    route = c.get('route','')
    if not route.startswith('/') or route.startswith('//') or '://' in route or '..' in route:
        raise HTTPException(500, 'Invalid configured route')
    url = base + route
    headers={'Accept':'application/json'}
    token=os.environ.get('GOOGLESAADA_BACKEND_TOKEN','')
    if token: headers['Authorization']='Bearer '+token
    opts={'headers': headers, 'follow_redirects':False, 'timeout':12.0}
    if method == 'POST':
        if request.headers.get('content-type','').split(';')[0].strip() != 'application/json':
            raise HTTPException(415,'JSON required')
        data=await request.body()
        if len(data)>16000: raise HTTPException(413,'Request too large')
        try: payload=json.loads(data)
        except (ValueError,UnicodeDecodeError): raise HTTPException(400,'Invalid JSON')
        if not isinstance(payload,dict):raise HTTPException(400,'JSON object required')
        opts['json']=payload
    else:
        params=dict(request.query_params)
        if len(params)>12 or sum(len(k)+len(v) for k,v in params.items())>2048:
            raise HTTPException(400,'Too many query parameters')
        opts['params']=params
    try:
        async with httpx.AsyncClient() as client:
            result=await client.request(method,url,**opts)
    except httpx.HTTPError:
        raise HTTPException(502,'Configured backend is unreachable')
    if result.status_code>=400:
        raise HTTPException(502,f'Backend returned HTTP {result.status_code}')
    if 'application/json' not in result.headers.get('content-type',''):
        raise HTTPException(502,'Backend returned a non-JSON response')
    if len(result.content)>1_000_000:
        raise HTTPException(502,'Backend response exceeds size limit')
    try: body=result.json()
    except ValueError: raise HTTPException(502,'Backend returned invalid JSON')
    return JSONResponse(body)

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0',port=int(os.getenv('PORT','7860')))


# GOOGLESAADA V9 — GUARDED PREDICTION ROUTER
from gs_prediction_router import (
    router as gs_prediction_router
)

app.include_router(gs_prediction_router)


# GOOGLESAADA V9 — MODEL QUALIFICATION GATE
from gs_activation_router import (
    router as gs_activation_router
)
app.include_router(gs_activation_router)
