"""GoogleSaada V2: actual joblib specialist inference behind the staging orchestrator.

Does not construct evidence, acquire fixtures, authorize requests, settle credits,
learn weights, or activate any public callback. The trusted caller supplies all
pre-kickoff features and provenance, matching the pinned training schemas.

Only load joblib files from trusted project storage: deserializing untrusted files
can run arbitrary Python code. Release approval is intentionally absent here.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
import hashlib
import json
import math

ORIGINAL_ROLE_MAP = {
    'xgboost': 'full_time_xgboost',
    'logistic_regression': 'full_time_logistic',
    'random_forest': 'full_time_random_forest',
    'elo_only': 'full_time_elo',
    'ft_home_poisson': 'full_time_home_goals',
    'ft_away_poisson': 'full_time_away_goals',
    'ht_home_poisson': 'first_half_home_goals',
    'ht_away_poisson': 'first_half_away_goals',
    'sh_home_poisson': 'second_half_home_goals',
    'sh_away_poisson': 'second_half_away_goals',
}
WORLDWIDE_ROLE_MAP = {
    'worldwide_match_result': 'match_result',
    'worldwide_over_2_5': 'over_2_5',
    'worldwide_btts': 'btts',
}
ALL_ROLES = tuple(ORIGINAL_ROLE_MAP) + tuple(WORLDWIDE_ROLE_MAP)
CATEGORICAL_ORIGINAL = frozenset(('league_name', 'home_team_name', 'away_team_name'))
GOAL_ROLES = frozenset(role for role in ORIGINAL_ROLE_MAP if 'poisson' in role)
ORIGINAL_RESULT_ROLES = frozenset(set(ORIGINAL_ROLE_MAP) - GOAL_ROLES)

# Preserved from the original backend's fitted training-label evidence contract:
# dynamic_research_inference_v3_markets_recent.py uses 0=AWAY, 1=DRAW, 2=HOME.
ORIGINAL_CLASS_MAP = {'0': 'A', '1': 'D', '2': 'H'}
EXPECTED_PROB_CLASSES = {
    'match_result': frozenset(('H', 'D', 'A')),
    'over_2_5': frozenset(('0', '1')),
    'btts': frozenset(('0', '1')),
}


class AdapterEvidenceError(ValueError):
    pass


class AdapterArtifactError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def class_token(value: Any) -> str:
    token = str(value).strip()
    if token.endswith('.0') and token[:-2].isdigit():
        token = token[:-2]
    return token


def _coerce_feature_row(feature_row, features, *, original: bool):
    if not isinstance(feature_row, dict) or set(feature_row) != set(features):
        raise AdapterEvidenceError('Exact trained feature names are required')
    values = {}
    for name in features:
        value = feature_row[name]
        if original and name in CATEGORICAL_ORIGINAL:
            if not isinstance(value, str) or not value.strip():
                raise AdapterEvidenceError('Historical category not verified: ' + name)
            values[name] = value
        else:
            if isinstance(value, bool) or value is None:
                raise AdapterEvidenceError('Missing numeric feature: ' + name)
            try:
                num = float(value)
            except (TypeError, ValueError, OverflowError):
                raise AdapterEvidenceError('Non-numeric feature: ' + name) from None
            if not math.isfinite(num):
                raise AdapterEvidenceError('Nonfinite numeric feature: ' + name)
            values[name] = num
    return values


def _standardize_class_result(classes, probabilities, market, *, original):
    labels = [class_token(c) for c in classes]
    if original:
        if set(labels) != set(ORIGINAL_CLASS_MAP) or len(labels) != 3:
            raise AdapterArtifactError('Original trained labels are not 0/1/2')
        labels = [ORIGINAL_CLASS_MAP[label] for label in labels]
    expected = EXPECTED_PROB_CLASSES[market]
    if set(labels) != expected or len(labels) != len(expected):
        raise AdapterArtifactError('Trained probability class mapping changed')
    if len(probabilities) != len(labels):
        raise AdapterArtifactError('Probability output length mismatch')
    out = {}
    for label, p in zip(labels, probabilities):
        p = float(p)
        if not math.isfinite(p) or p < 0 or p > 1:
            raise AdapterArtifactError('Invalid model probability')
        out[label] = p
    if not math.isclose(sum(out.values()), 1.0, abs_tol=1e-5, rel_tol=1e-5):
        raise AdapterArtifactError('Probabilities not normalized')
    return {k: out[k] for k in ('H','D','A') if k in out} if market == 'match_result' else {k: out[k] for k in ('0','1')}


def _as_flat_prediction(array, length: int):
    try:
        import numpy as np
        values = np.asarray(array, dtype=float)
        if values.shape != (1, length):
            raise AdapterArtifactError('Unexpected classifier result shape')
        return values[0]
    except (TypeError, ValueError) as exc:
        raise AdapterArtifactError('Malformed classifier output') from exc


class VerifiedSpecialistAdapterFactory:
    """Create one model-specific callable for every registered specialist.

    No fallback feature filling, mutable shared model state, or user-supplied
    artifact paths. Models load lazily after pinned SHA256 verification.
    """

    def __init__(self, registry_path, *, load_models=True):
        self.registry_path = Path(registry_path)
        self.registry = json.loads(self.registry_path.read_text(encoding='utf-8'))
        r = self.registry
        if (len(r.get('original_models', {})) != 10
                or len(r.get('selected_worldwide_models', {})) != 3
                or r.get('live_routing_enabled') is not False
                or r.get('maximum_matches') != 16):
            raise AdapterArtifactError('Staging registry contract changed')
        self._cache = {}
        self._load_models = bool(load_models)

    def _spec(self, role):
        if role in ORIGINAL_ROLE_MAP:
            return (self.registry['original_models'][ORIGINAL_ROLE_MAP[role]], True)
        if role in WORLDWIDE_ROLE_MAP:
            return (self.registry['selected_worldwide_models'][WORLDWIDE_ROLE_MAP[role]], False)
        raise AdapterArtifactError('Unregistered specialist')

    def _trusted_model_path(self, spec):
        """Resolve registry paths against the release root, not notebook CWD."""
        path = Path(spec['path'])
        if not path.is_absolute():
            path = self.registry_path.resolve().parent.parent / path
        return path.resolve()

    @staticmethod
    def _replay_verified_logistic_compatibility(model, role):
        """Restore missing metadata only; fitted weights are untouched.

        Original Elo and logistic golden replays verified that multinomial
        reproduces the original saved predictions exactly in sklearn 1.6.1.
        """
        if role not in ('elo_only', 'logistic_regression'):
            return model
        from sklearn.linear_model import LogisticRegression
        steps = getattr(model, 'named_steps', None)
        estimators = [e for e in steps.values() if isinstance(e, LogisticRegression)] if steps else []
        if len(estimators) != 1:
            raise AdapterArtifactError('Original LogisticRegression pipeline layout changed')
        classifier = estimators[0]
        # scikit-learn 1.6 exposes a class-level default even when historical
        # fitted instance metadata is absent. hasattr() is therefore unsafe.
        # Explicitly restore the golden-replay-verified mode for these two
        # trusted original classifier roles. Fitted coefficients stay intact.
        classifier.multi_class = 'multinomial'
        return model

    def _get_model(self, role, spec):
        if role in self._cache:
            return self._cache[role]
        if not self._load_models:
            raise AdapterArtifactError('Model execution disabled for contract-only testing')
        path = self._trusted_model_path(spec)
        if not path.is_file() or sha256(path) != spec['sha256']:
            raise AdapterArtifactError('Trusted model file absent or hash mismatched')
        import joblib  # Never deserialize externally supplied or untrusted files
        model = self._replay_verified_logistic_compatibility(joblib.load(path), role)
        expected = list(spec['feature_names'])
        learned_names = getattr(model, 'feature_names_in_', None)
        if learned_names is not None and list(map(str, learned_names)) != expected:
            raise AdapterArtifactError('Trained feature name/order mismatch')
        learned_count = getattr(model, 'n_features_in_', None)
        if learned_count is not None and int(learned_count) != len(expected):
            raise AdapterArtifactError('Trained feature count mismatch')
        self._cache[role] = model
        return model

    def preflight(self, *, load=True, retain_models=False, roles=None):
        """Validate 13 artifacts and trained feature interfaces; no inference.

        Unless retain_models=True, loaded models are released one at a time to
        avoid retaining all 13 large artifacts in Colab memory.
        """
        checked = []
        selected = ALL_ROLES if roles is None else tuple(roles)
        if len(set(selected)) != len(selected) or any(r not in ALL_ROLES for r in selected):
            raise AdapterArtifactError('Unknown or repeated specialist in preflight')
        for role in selected:
            spec, original = self._spec(role)
            path = self._trusted_model_path(spec)
            if not path.is_file() or sha256(path) != spec['sha256']:
                raise AdapterArtifactError('Missing/mismatched model: ' + role)
            expected = spec.get('feature_names')
            if not isinstance(expected, list) or len(expected) != spec.get('feature_count') or len(set(expected)) != len(expected):
                raise AdapterArtifactError('Invalid feature schema: ' + role)
            model = self._get_model(role, spec) if load else None
            if model is not None:
                if role in GOAL_ROLES:
                    if not callable(getattr(model, 'predict', None)):
                        raise AdapterArtifactError('Missing goal-prediction method: ' + role)
                else:
                    if not callable(getattr(model, 'predict_proba', None)):
                        raise AdapterArtifactError('Missing classifier method: ' + role)
                    classes = getattr(model, 'classes_', None)
                    if classes is None:
                        raise AdapterArtifactError('Model classes unavailable: ' + role)
                    labels = [class_token(c) for c in classes]
                    if original:
                        if set(labels) != set(ORIGINAL_CLASS_MAP) or len(labels) != 3:
                            raise AdapterArtifactError('Original class map mismatch: ' + role)
                    else:
                        required = {str(c) for c in spec['required_class_order']}
                        if set(labels) != required or len(labels) != len(required):
                            raise AdapterArtifactError('Worldwide class map mismatch: ' + role)
            checked.append(role)
            if model is not None and not retain_models:
                self._cache.pop(role, None)
                del model
                import gc
                gc.collect()
        return checked

    def adapter_for(self, role):
        spec, original = self._spec(role)
        expected = tuple(spec['feature_names'])
        if role in GOAL_ROLES:
            market = {
                'ft_home_poisson': 'ft_home_expected_goals',
                'ft_away_poisson': 'ft_away_expected_goals',
                'ht_home_poisson': 'ht_home_expected_goals',
                'ht_away_poisson': 'ht_away_expected_goals',
                'sh_home_poisson': 'sh_home_expected_goals',
                'sh_away_poisson': 'sh_away_expected_goals',
            }[role]
        elif role in ORIGINAL_RESULT_ROLES or role == 'worldwide_match_result':
            market = 'match_result'
        else:
            market = WORLDWIDE_ROLE_MAP[role]

        def adapter(*, fixture, features, evidence):
            if not isinstance(fixture, dict) or not isinstance(evidence, dict):
                raise AdapterEvidenceError('Verified fixture/evidence required')
            for field in ('fixture_id', 'source_fixture_id', 'home_id', 'away_id'):
                if fixture.get(field) != evidence.get(field):
                    raise AdapterEvidenceError('Fixture identity and feature source mismatch')
            if fixture.get('verified') is not True or evidence.get('verified') is not True:
                raise AdapterEvidenceError('Unverified fixture/feature source')
            evidence_id = evidence.get('evidence_id')
            if not isinstance(evidence_id, str) or not evidence_id.strip():
                raise AdapterEvidenceError('Evidence ID required')
            row = _coerce_feature_row(features, expected, original=original)
            model = self._get_model(role, spec)
            import pandas as pd
            frame = pd.DataFrame([[row[name] for name in expected]], columns=list(expected))
            if role in GOAL_ROLES:
                result = model.predict(frame)
                if len(result) != 1:
                    raise AdapterArtifactError('Unexpected goal predictor shape')
                value = float(result[0])
                if not math.isfinite(value) or not 0 <= value <= 25:
                    raise AdapterArtifactError('Invalid goal estimate')
            else:
                classes = getattr(model, 'classes_', None)
                if classes is None:
                    raise AdapterArtifactError('Model class metadata missing')
                array = _as_flat_prediction(model.predict_proba(frame), len(classes))
                value = _standardize_class_result(classes, array, market, original=original)
            return {'specialist': role, 'market': market, 'value': value,
                    'evidence_id': evidence_id, 'artifact_sha256': spec['sha256']}
        return adapter

    def all_adapters(self):
        return {role: self.adapter_for(role) for role in ALL_ROLES}
