import json, os
from pathlib import Path
from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from gs_registry_router import require_auth

router=APIRouter(prefix='/v1',tags=['GoogleSaada Predictions'])
class SingleRequest(BaseModel):
 model_id:str
 features:dict[str,Any]
class BatchRequest(BaseModel):
 requests:list[SingleRequest]=Field(min_length=1,max_length=100)

def runtime():
 reg=os.getenv('GOOGLESAADA_PHASE2_MODEL_REGISTRY')
 approvals=os.getenv('GOOGLESAADA_PHASE2_APPROVALS')
 if not reg or not approvals:raise HTTPException(503,'MODEL_RELEASE_NOT_CONFIGURED')
 try:
  data=json.loads(Path(reg).read_text(encoding='utf-8'))
  approved=json.loads(Path(approvals).read_text(encoding='utf-8'))
 except (OSError,ValueError):raise HTTPException(503,'MODEL_RELEASE_MANIFEST_INVALID')
 if approved.get('production_approved') is not True:
  raise HTTPException(403,'MODEL_RELEASE_NOT_APPROVED')
 if approved.get('registry_sha256')!=data.get('origin_registry_sha256'):
  raise HTTPException(503,'MODEL_REGISTRY_APPROVAL_MISMATCH')
 ids=approved.get('approved_model_ids',[])
 if not isinstance(ids,list) or not ids or len(ids)!=len(set(ids)):
  raise HTTPException(403,'NO_APPROVED_MODELS')
 valid={r['model_id'] for r in data.get('models',[])}
 if not set(ids).issubset(valid):raise HTTPException(503,'UNKNOWN_APPROVED_MODEL')
 from gs_verified_model_runtime import ModelRuntime
 return ModelRuntime(data['models'],ids)

def run(model,req):
 try:return model.predict(req.model_id,req.features)
 except KeyError:raise HTTPException(404,'MODEL_NOT_REGISTERED')
 except PermissionError:raise HTTPException(403,'MODEL_NOT_ACTIVATED')
 except ValueError as e:raise HTTPException(422,str(e)[:300])
 except Exception as e:raise HTTPException(503,'NATIVE_INFERENCE_FAILED:'+type(e).__name__)

@router.post('/predict',dependencies=[Depends(require_auth)])
def predict(req:SingleRequest):
 return run(runtime(),req)

@router.post('/predictbatch',dependencies=[Depends(require_auth)])
def predictbatch(req:BatchRequest):
 engine=runtime()
 return {'predictions':[run(engine,x) for x in req.requests]}
