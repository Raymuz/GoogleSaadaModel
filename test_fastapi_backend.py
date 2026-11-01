from fastapi.testclient import TestClient
import importlib.util, os
from pathlib import Path
p=Path(__file__).parent/'googlesaada_fastapi_backend.py'
spec=importlib.util.spec_from_file_location('gs_stage_api',p);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
c=TestClient(mod.app)
r=c.get('/health');assert r.status_code==200 and r.json()['status']=='ok'
assert c.get('/docs').status_code==404
assert c.get('/api/v1/admin/status').status_code in (401,503)
assert c.get('/api/v1/predict').status_code==503
os.environ['GOOGLESAADA_ADMIN_API_TOKEN']='A'*64
assert c.get('/api/v1/admin/status',headers={'Authorization':'Bearer invalid'}).status_code==401
assert c.get('/api/v1/admin/status',headers={'Authorization':'Bearer '+'A'*64}).status_code==200
print('[PASS] FastAPI 6 local security/HTTP assertions')
