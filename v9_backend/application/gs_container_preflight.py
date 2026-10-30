import json,hashlib
from pathlib import Path
root=Path('/app')
registry=json.loads((root/'ORIGINAL_MODEL_RELEASE_REGISTRY.json').read_text())
approval=json.loads((root/'MODEL_ACTIVATION_APPROVALS.json').read_text())
ids=set(approval['approved_model_ids']); models=registry['models']
if len(ids)!=10 or len(models)!=19:raise RuntimeError('ACTIVATION_COUNTS_INVALID')
for model in models:
 active=model['model_id'] in ids
 if active:
  path=Path(model['artifact'])
  if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=model['sha256']:
   raise RuntimeError('MODEL_HASH_INVALID: '+model['model_id'])
 else:
  if model['artifact'] is not None or model.get('activation')!='DISABLED':
   raise RuntimeError('INACTIVE_MODEL_ARTIFACT_EXPOSED')
print('GOOGLESAADA_BACKEND_ARTIFACTS_VERIFIED: 10 ACTIVE, 9 DISABLED')
