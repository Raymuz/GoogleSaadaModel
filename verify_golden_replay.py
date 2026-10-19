
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from model_loader import load_soccer_models

DEPLOY = ROOT / 'deployment_files'

capture = json.loads(
    (DEPLOY / 'step11b2_capture_manifest.json')
    .read_text(encoding='utf-8')
)

contracts = json.loads(
    (DEPLOY / 'prematch_model_contracts.json')
    .read_text(encoding='utf-8')
)


def hash_file(path):
    h = hashlib.sha256()

    with path.open('rb') as stream:
        for chunk in iter(
            lambda: stream.read(1024 * 1024),
            b''
        ):
            h.update(chunk)

    return h.hexdigest()


def safe_reference(relative):
    p = (ROOT / relative).resolve()

    if not p.is_relative_to(ROOT):
        raise ValueError(
            'Replay reference escapes deployment folder.'
        )

    if not p.is_file():
        raise FileNotFoundError(p)

    return p


models = load_soccer_models()

if (
    set(models) != set(contracts)
    or set(models) != set(capture['replay_index'])
):
    raise RuntimeError(
        'Saved models, contracts and replay roles differ.'
    )

results = {}
failed = []

for role in sorted(models):

    try:
        model = models[role]
        contract = contracts[role]
        entry = capture['replay_index'][role]

        inp = safe_reference(entry['input_file'])
        out = safe_reference(entry['output_file'])

        if (
            hash_file(inp) != entry['input_sha256']
            or hash_file(out) != entry['output_sha256']
        ):
            raise RuntimeError(
                'Replay reference checksum mismatch.'
            )

        X = pd.read_parquet(inp)

        expected_cols = contract['features_in_order']

        if list(X.columns) != expected_cols:
            raise RuntimeError(
                'Input columns differ from the training contract.'
            )

        if len(X) != entry['rows']:
            raise RuntimeError(
                'Replay input row count changed.'
            )

        expected = json.loads(
            out.read_text(encoding='utf-8')
        )

        if (
            expected['role'] != role
            or entry['method'] != expected['method']
        ):
            raise RuntimeError(
                'Replay role or method mismatch.'
            )

        model_classes = [
            str(v)
            for v in getattr(model, 'classes_', [])
        ]

        if model_classes != expected['model_classes']:
            raise RuntimeError(
                'Classifier class order changed.'
            )

        if expected['method'] == 'predict_proba':
            pred = model.predict_proba(X)

        elif expected['method'] == 'predict':
            pred = model.predict(X)

        else:
            raise ValueError(
                'Unknown prediction method.'
            )

        actual = np.asarray(pred, dtype=float)
        reference = np.asarray(
            expected['output'],
            dtype=float
        )

        if actual.shape != reference.shape:
            raise RuntimeError(
                f'Output shapes differ: '
                f'{actual.shape} vs {reference.shape}'
            )

        if (
            not np.isfinite(actual).all()
            or not np.isfinite(reference).all()
        ):
            raise RuntimeError(
                'Nonfinite saved model output.'
            )

        maximum_error = float(
            np.max(np.abs(actual - reference))
        )

        passed = bool(
            np.allclose(
                actual,
                reference,
                rtol=1e-8,
                atol=1e-10
            )
        )

        if not passed:
            raise RuntimeError(
                'Predictions changed; maximum error: '
                f'{maximum_error:.12g}'
            )

        results[role] = {
            'status': 'PASS',
            'rows': len(X),
            'method': expected['method'],
            'max_absolute_difference': maximum_error
        }

        print(
            f'[PASS] {role}: {len(X)} reference rows; '
            f'max diff {maximum_error:.3g}'
        )

    except Exception as exc:

        failed.append(role)

        results[role] = {
            'status': 'FAIL',
            'error': (
                f'{type(exc).__name__}: {exc}'
            )
        }

        print(
            f'[FAIL] {role}: '
            f'{type(exc).__name__}: {exc}'
        )

report = {
    'step': '11B.3A',
    'models_tested': len(results),

    'models_passed': sum(
        v['status'] == 'PASS'
        for v in results.values()
    ),

    'failed_roles': failed,

    'independent_process_prediction_replay_verified':
        len(failed) == 0 and len(results) == 10,

    'future_fixture_feature_generation_verified': False,
    'production_predictions_enabled': False,
    'results': results
}

report_path = (
    DEPLOY
    / 'preprocessing_audit'
    / 'step11b3a_replay_report.json'
)

report_path.parent.mkdir(
    parents=True,
    exist_ok=True
)

report_path.write_text(
    json.dumps(report, indent=2),
    encoding='utf-8'
)

print('REPLAY_REPORT:', report_path)

if failed:
    sys.exit(1)

print('INDEPENDENT REPLAY PASS: 10/10')
