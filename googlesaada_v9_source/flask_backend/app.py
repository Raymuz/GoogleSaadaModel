"""GoogleSaada V9 contract-driven Flask backend. Deploy only trusted model packages."""
from __future__ import annotations
import hashlib, hmac, io, json, logging, math, os, threading
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
from flask import Flask, jsonify, request
from reasoning import analyze_match

BASE = Path(__file__).resolve().parent if '__file__' in globals() else Path.cwd()
RELEASE = Path(os.environ.get('GS_RELEASE_ROOT', str(BASE / 'release'))).resolve()
API_KEY = os.getenv('GS_API_KEY', '')
MAX_BATCH = int(os.getenv('GS_MAX_BATCH', '500'))
app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = int(os.getenv('GS_MAX_REQUEST_BYTES', str(5 * 1024 * 1024)))
LOG = logging.getLogger('googlesaada')
logging.basicConfig(level=logging.INFO)


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def safe_path(name):
    if not isinstance(name, str) or not name or Path(name).is_absolute():
        raise ValueError('Invalid artifact path')
    p = (RELEASE / name).resolve()
    if not p.is_relative_to(RELEASE):
        raise ValueError('Artifact path escapes release')
    return p


class Registry:
    def __init__(self):
        self.contracts, self.errors, self.loaded = {}, {}, {}
        self.lock = threading.RLock()
        self.path = RELEASE / 'REGISTRY.json'
        self.production_approved = False
        if not self.path.is_file():
            self.errors['registry'] = 'REGISTRY.json missing; export model release first'
            return
        try:
            reg = json.loads(self.path.read_text(encoding='utf-8'))
            self.production_approved = bool(reg.get('production_approved', False))
            if not isinstance(reg.get('models'), list):
                raise ValueError('registry.models must be a list')
            for item in reg['models']:
                if not item.get('active', False):
                    continue
                mid = str(item['id'])
                try:
                    contract = json.loads(safe_path(item['contract']).read_text())
                    if contract.get('id') != mid or mid in self.contracts:
                        raise ValueError('ID mismatch or duplicate')
                    columns = contract.get('input', {}).get('columns')
                    if not isinstance(columns, list) or not columns or len(columns) != len(set(columns)):
                        raise ValueError('Missing/duplicate feature columns')
                    if contract.get('prediction', {}).get('method') not in ('predict', 'predict_proba'):
                        raise ValueError('Unsupported prediction method')
                    path = safe_path(contract['artifact'])
                    if not path.is_file() or sha256(path) != contract['artifact_sha256']:
                        raise ValueError('Model artifact missing or checksum mismatch')
                    self.contracts[mid] = contract
                except Exception as exc:
                    self.errors[mid] = str(exc)
        except Exception as exc:
            self.errors['registry'] = str(exc)

    def _model(self, mid):
        if mid not in self.loaded:
            c = self.contracts[mid]
            path = safe_path(c['artifact'])
            if sha256(path) != c['artifact_sha256']:
                raise RuntimeError('Artifact checksum changed')
            obj = joblib.load(path)  # Only checksum-verified, trusted packaged artifacts.
            if isinstance(obj, dict) and isinstance(obj.get('fitted'), dict):
                fit = obj['fitted']
                if 'model' not in fit or 'imputer' not in fit or 'indices' not in fit:
                    raise RuntimeError('Incomplete hybrid model bundle')
                model, indices, imputer = fit['model'], np.asarray(fit['indices'], dtype=int), fit['imputer']
            else:
                model = obj.get('estimator', obj.get('model')) if isinstance(obj, dict) else obj
                indices = imputer = None
            if model is None:
                raise RuntimeError('No fitted estimator found')
            if isinstance(obj, dict) and obj.get('feature_columns') is not None:
                if list(obj['feature_columns']) != c['input']['columns']:
                    raise RuntimeError('Stored features differ from model contract')
            self.loaded[mid] = (model, indices, imputer)
        return self.loaded[mid]

    def predict(self, mid, rows):
        if mid not in self.contracts:
            raise KeyError('Unknown or inactive model: ' + mid)
        if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_BATCH:
            raise ValueError('Expected a nonempty list of up to %d rows' % MAX_BATCH)
        c = self.contracts[mid]
        columns = c['input']['columns']
        values = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError('Each features entry must be an object')
            missing, extra = sorted(set(columns) - set(row)), sorted(set(row) - set(columns))
            if missing or extra:
                raise ValueError(json.dumps({'missing_features': missing, 'unexpected_features': extra}))
            vals = []
            for col in columns:
                v = row[col]
                if v is None:
                    vals.append(np.nan)
                elif isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                    raise ValueError('Invalid numeric feature: ' + col)
                else:
                    vals.append(float(v))
            values.append(vals)
        frame = pd.DataFrame(values, columns=columns, dtype='float32')
        with self.lock:
            model, indices, imputer = self._model(mid)
            matrix = frame
            if indices is not None:
                if indices.ndim != 1 or not len(indices) or indices.min() < 0 or indices.max() >= len(columns):
                    raise RuntimeError('Invalid native feature indices')
                matrix = imputer.transform(frame.iloc[:, indices].to_numpy())
            method = c['prediction']['method']
            raw = getattr(model, method)(matrix)
        answers = []
        if method == 'predict_proba':
            classes = list(getattr(model, 'classes_', []))
            probs = np.asarray(raw, dtype=float)
            if not classes or probs.shape != (len(rows), len(classes)):
                raise RuntimeError('Class contract mismatch')
            labels = c['prediction'].get('classes')
            if labels is not None:
                if len(labels) != len(classes) or set(classes) != set(range(len(labels))):
                    raise RuntimeError('Class ordering not established')
                labels = [labels[int(i)] for i in classes]
            else:
                labels = classes
            for probability in probs:
                if not np.isfinite(probability).all() or (probability < 0).any() or abs(probability.sum() - 1) > 1e-5:
                    raise RuntimeError('Invalid probability output')
                answers.append({'probabilities': {str(label): float(v) for label, v in zip(labels, probability)}})
        else:
            for v in np.asarray(raw).reshape(-1):
                if not math.isfinite(float(v)):
                    raise RuntimeError('Invalid numeric model output')
                answers.append({'value': float(v), 'unit': c['prediction'].get('value', 'value')})
        return answers


store = Registry()


@app.before_request
def authenticate():
    if request.path.startswith('/v1/'):
        if not API_KEY:
            return jsonify(error='GS_API_KEY not configured'), 503
        if not hmac.compare_digest(request.headers.get('X-API-Key', ''), API_KEY):
            return jsonify(error='unauthorized'), 401


@app.after_request
def secure_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.get('/')
def home():
    return jsonify(service='GoogleSaada V9 Flask Prediction Backend', docs='GET /v1/models', version='2.0')


@app.get('/health')
def health():
    return jsonify(status='running')


@app.get('/ready')
def ready():
    ok = bool(store.contracts) and not store.errors and bool(API_KEY)
    return jsonify(status='ready' if ok else 'not_ready', active_models=len(store.contracts), errors=store.errors), (200 if ok else 503)


@app.get('/v1/models')
def models():
    return jsonify(models=[{'id': k, 'task': c.get('task'), 'production_approved': bool(c.get('production_approved') and store.production_approved)} for k, c in store.contracts.items()], errors=store.errors)


@app.get('/v1/models/<mid>/contract')
def contract(mid):
    if mid not in store.contracts:
        return jsonify(error='unknown_or_inactive_model'), 404
    return jsonify(store.contracts[mid])


def attempt(mid, rows):
    try:
        return {'model_id': mid, 'task': store.contracts[mid].get('task', mid), 'predictions': store.predict(mid, rows)}, 200
    except KeyError:
        return {'error': 'unknown_or_inactive_model', 'model_id': mid}, 404
    except ValueError as exc:
        return {'error': 'invalid_input', 'detail': str(exc)}, 422
    except Exception:
        LOG.exception('Prediction failed: %s', mid)
        return {'error': 'model_inference_unavailable', 'model_id': mid}, 503


@app.post('/v1/models/<mid>/predict')
def predict_one(mid):
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error='JSON object required'), 400
    result, status = attempt(mid, [body.get('features')])
    if status == 200:
        result['prediction'] = result.pop('predictions')[0]
    return jsonify(result), status


@app.post('/v1/models/<mid>/predictbatch')
def predict_batch(mid):
    if 'file' in request.files:
        try:
            df = pd.read_csv(io.BytesIO(request.files['file'].read()), nrows=MAX_BATCH + 1)
            ids = df['id'].tolist() if 'id' in df else list(range(len(df)))
            rows = df.drop(columns=['id'], errors='ignore').replace({np.nan: None}).to_dict('records')
        except Exception:
            return jsonify(error='invalid_csv'), 400
    else:
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or not isinstance(body.get('rows'), list):
            return jsonify(error='Supply JSON rows or multipart CSV file'), 400
        rows = body['rows']
        ids = list(range(len(rows)))
    result, status = attempt(mid, rows)
    if status == 200:
        result['results'] = [{'id': str(i), **p} for i, p in zip(ids, result.pop('predictions'))]
    return jsonify(result), status


@app.post('/v1/analyze')
def analyze():
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get('models'), dict) or not body['models']:
        return jsonify(error='Provide models map of model_id -> features'), 400
    if len(body['models']) > 25:
        return jsonify(error='too_many_models'), 422
    ok, failures = [], []
    for mid, features in body['models'].items():
        result, status = attempt(mid, [features])
        if status == 200:
            ok.append({'model_id': mid, 'task': result['task'], **result['predictions'][0]})
        else:
            failures.append({'model_id': mid, 'http_status': status, **result})
    if not ok:
        return jsonify(error='no_valid_predictions', failures=failures), 422
    explanation = analyze_match(ok, body.get('context'), contract_lookup=store.contracts)
    return jsonify(status='partial' if failures else 'complete', predictions=ok,
                   failures=failures, reasoning=explanation,
                   collaboration={'policy':'task_aware_peer_review',
                                  'automatic_ensemble':False,
                                  'trained_weight_transfer':False})


@app.post('/api/data')
def old_endpoint():
    return jsonify(error='deprecated', message='Use /v1/models/<model_id>/predict'), 410


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', '7860')), debug=False)
