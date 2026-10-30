import os,json
from pathlib import Path
from fastapi import APIRouter,Depends,HTTPException
from gs_registry_router import require_auth
router=APIRouter(prefix='/v1/activation',tags=['GoogleSaada Model Activation'])
def state():
 try:
  registry=json.loads(Path(os.environ['GOOGLESAADA_PHASE2_MODEL_REGISTRY']).read_text())
  approval=json.loads(Path(os.environ['GOOGLESAADA_PHASE2_APPROVALS']).read_text())
 except (OSError,ValueError,KeyError):raise HTTPException(503,'ACTIVATION_CONFIG_UNAVAILABLE')
 models=registry.get('models',[])
 active=set(approval.get('approved_model_ids',[])) if approval.get('production_approved') is True else set()
 if len(models)!=19 or len(set(x.get('model_id') for x in models))!=19 or len(set(x.get('contract_ref') for x in models))!=19 or approval.get('registry_sha256')!=registry.get('origin_registry_sha256'):
  raise HTTPException(503,'ACTIVATION_REGISTRY_MISMATCH')
 actual={x['model_id'] for x in models if x.get('activation')=='ACTIVE'}
 if active!=actual or len(active)!=10 or any(x['model_id'] in active and not x.get('artifact') for x in models):
  raise HTTPException(503,'ACTIVATION_POLICY_MISMATCH')
 if any(x.get('visible_in_registry') is not True for x in models):
  raise HTTPException(503,'REGISTRY_VISIBILITY_INCOMPLETE')
 if any(x['model_id'] not in active and (x.get('artifact') is not None or x.get('activation')!='DISABLED') for x in models):
  raise HTTPException(503,'DISABLED_MODEL_EXECUTION_POLICY_MISMATCH')
 return models,active
def visible(m,active):
 return {'contract_ref':m['contract_ref'],'model_id':m['model_id'],
         'model_family':m.get('model_family') or m.get('family'),
         'qualification_status':m['qualification_status'],
         'visible_in_registry':True,
         'activation':'ACTIVE' if m['model_id'] in active else 'DISABLED',
         'prediction_enabled':m['model_id'] in active,
         'native_execution_verified':m['model_id'] in active}
@router.get('/readiness',dependencies=[Depends(require_auth)])
def readiness():
 models,active=state()
 return {'status':'ACTIVATION_MANIFEST_READY','registered_models':len(models),'visible_model_count':len(models),
         'qualified_model_count':len(active),'activated_model_count':len(active),
         'disabled_model_count':len(models)-len(active),'public_predictions_enabled':False,
         'authenticated_predictions_enabled':True,
         'production_approved':True,'deployment_verified':False}
@router.get('/models',dependencies=[Depends(require_auth)])
def all_models():
 models,active=state()
 return {'model_count':len(models),'active_count':len(active),'disabled_count':len(models)-len(active),
         'models':[visible(m,active) for m in models]}
@router.get('/models/{identifier}',dependencies=[Depends(require_auth)])
def model_readiness(identifier:str):
 models,active=state()
 found=[x for x in models if x['contract_ref']==identifier or x['model_id'].replace('/','_')==identifier]
 if len(found)!=1:raise HTTPException(404,'MODEL_NOT_FOUND')
 return visible(found[0],active)
