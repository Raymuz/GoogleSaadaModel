"""Verify original GoogleSaada models inside Docker."""

from pathlib import Path
import gc
import hashlib
import importlib.util
import json
import sys

import joblib

ROOT = Path(__file__).resolve().parent

EXPECTED = {
    "full_time_xgboost",
    "full_time_elo",
    "full_time_logistic",
    "full_time_random_forest",
    "full_time_home_goals",
    "full_time_away_goals",
    "first_half_home_goals",
    "first_half_away_goals",
    "second_half_home_goals",
    "second_half_away_goals",
}

def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for part in iter(lambda: f.read(1048576), b""):
            h.update(part)
    return h.hexdigest()

data = json.loads(
    (ROOT / "deployment_files/manifest.json").read_text()
)

assert data.get("exported_model_count") == 10
assert set(data["models"]) == EXPECTED
assert data.get("production_predictions_enabled") is False

module_file = ROOT / (
    "backend/dynamic_research_provider_name_evidence_v2_mass_safe.py"
)

spec = importlib.util.spec_from_file_location(
    "gs_cloud_verified_dynamic_models",
    module_file
)

if spec is None or spec.loader is None:
    raise RuntimeError("Original model engine missing")

mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)

# This checks original identity-registry hashes,
# historical data, feature builder, training-class mapping,
# and the same ten model roles as the Colab inference engine.
engine = mod.ProviderNameEvidenceResearch()

if set(engine.models) != EXPECTED:
    raise RuntimeError("Incomplete trained model registry")

classifiers = {
    "full_time_xgboost",
    "full_time_elo",
    "full_time_logistic",
    "full_time_random_forest",
}

for index, role in enumerate(sorted(EXPECTED), start=1):
    path = engine.models[role]
    expected_hash = data["models"][role]["source_sha256"]

    if sha(path) != expected_hash:
        raise RuntimeError("Model checksum failed: " + role)

    model = joblib.load(path)

    if role in classifiers:
        if not callable(getattr(model, "predict_proba", None)):
            raise RuntimeError("Classifier unavailable: " + role)

        labels = {
            str(v).removesuffix(".0")
            for v in model.classes_
        }

        if labels != {"0", "1", "2"}:
            raise RuntimeError(
                "Original class mapping invalid: " + role
            )
    else:
        if not callable(getattr(model, "predict", None)):
            raise RuntimeError(
                "Regressor unavailable: " + role
            )

    print(
        f"[CLOUD MODEL PASS {index}/10] {role}",
        flush=True
    )

    del model
    gc.collect()

print("GOOGLESAADA_10_MODELS_LOAD_OK", flush=True)
