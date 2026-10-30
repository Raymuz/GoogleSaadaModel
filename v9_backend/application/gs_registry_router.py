import os
import json
import hmac
import hashlib
from pathlib import Path
from collections import Counter
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
router = APIRouter(prefix='/v1', tags=['GoogleSaada Model Registry'])

def require_auth(authorization: str=Header(default='')):
    expected = os.environ.get('GOOGLESAADA_REGISTRY_API_TOKEN', '')
    if len(expected) < 32:
        raise HTTPException(status_code=503, detail='Backend authentication not configured')
    prefix = 'Bearer '
    if not authorization.startswith(prefix):
        raise HTTPException(status_code=401, detail='Unauthorized')
    supplied = authorization[len(prefix):].strip()
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail='Unauthorized')

def read_registry():
    location = os.environ.get('GOOGLESAADA_MODEL_REGISTRY_PATH', '')
    if not location:
        raise HTTPException(status_code=503, detail='Model registry not configured')
    try:
        with Path(location).open('r', encoding='utf-8') as file:
            data = json.load(file)
    except (OSError, ValueError):
        raise HTTPException(status_code=503, detail='Model registry unavailable')
    for key in ('contract_entries', 'objects', 'file_instances'):
        if not isinstance(data.get(key), list):
            raise HTTPException(status_code=503, detail='Invalid registry schema')
    return data

def contract_records(registry):
    records = []
    for index, entry in enumerate(registry['contract_entries'], start=1):
        if not isinstance(entry, dict):
            raise HTTPException(status_code=503, detail='Invalid model contract')
        canonical = json.dumps(entry, sort_keys=True, separators=(',', ':'), default=str)
        digest = hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:12]
        reference = f'contract-{index:03d}-{digest}'
        records.append({'contract_ref': reference, 'model_id': entry.get('model_id'), 'model_family': entry.get('model_family'), 'artifact_sha256': entry.get('sha256'), 'activation': 'DISABLED', 'native_inference_verified': False, 'public_inference_enabled': False})
    return records

@router.get('/registry/summary', dependencies=[Depends(require_auth)])
def registry_summary():
    data = read_registry()
    records = contract_records(data)
    original_ids = [str(item['model_id']) for item in records if item['model_id'] is not None]
    counts = Counter(original_ids)
    return {'status': 'REGISTRY_AVAILABLE', 'contract_count': len(records), 'artifact_instance_count': len(data['file_instances']), 'content_object_count': len(data['objects']), 'duplicate_model_id_count': sum((count - 1 for count in counts.values() if count > 1)), 'model_activation': 'DISABLED', 'production_prediction_enabled': False}

@router.get('/models', dependencies=[Depends(require_auth)])
def list_models():
    data = read_registry()
    return {'models': contract_records(data)}

@router.get('/models/{identifier}', dependencies=[Depends(require_auth)])
def get_model(identifier: str):
    data = read_registry()
    records = contract_records(data)
    matches = [item for item in records if item['contract_ref'] == identifier]
    if not matches:
        matches = [item for item in records if item['model_id'] is not None and str(item['model_id']) == identifier]
    if not matches:
        raise HTTPException(status_code=404, detail='Model contract not found')
    if len(matches) > 1:
        raise HTTPException(status_code=409, detail='Ambiguous model ID; use its unique contract_ref')
    return matches[0]

class PredictionRequest(BaseModel):
    model_id: str = Field(min_length=1, max_length=200)
    features: dict = Field(default_factory=dict)
