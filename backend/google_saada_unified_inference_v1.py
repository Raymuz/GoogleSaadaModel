
import hashlib
import json
import math
from pathlib import Path

MAX_MATCHES = 16

class PredictionUnavailable(Exception):
    pass

class ModelCompatibilityError(Exception):
    pass

def validate_limit(n):
    if type(n) is not int or not 1 <= n <= MAX_MATCHES:
        raise ValueError("Select between 1 and 16 matches.")
    return n

def load_registry(path):
    path = Path(path)
    registry = json.loads(path.read_text(encoding="utf-8"))

    if registry.get("maximum_matches") != MAX_MATCHES:
        raise ModelCompatibilityError("Invalid registry limit.")

    return registry

def file_hash(path):
    h = hashlib.sha256()

    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)

    return h.hexdigest()

def canonical_probability_vector(
    probabilities,
    model_classes,
    expected_classes
):
    actual = [str(x) for x in model_classes]
    expected = [str(x) for x in expected_classes]

    if len(set(actual)) != len(actual):
        raise ModelCompatibilityError("Duplicate model classes.")

    if set(actual) != set(expected):
        raise ModelCompatibilityError(
            "Prediction classes are incompatible."
        )

    if len(probabilities) != len(actual):
        raise ModelCompatibilityError(
            "Probability vector length mismatch."
        )

    ordered = [
        float(probabilities[actual.index(label)])
        for label in expected
    ]

    if not all(math.isfinite(p) for p in ordered):
        raise ModelCompatibilityError(
            "Nonfinite probability generated."
        )

    if any(p < 0 or p > 1 for p in ordered):
        raise ModelCompatibilityError(
            "Probability outside [0,1]."
        )

    if not math.isclose(
        sum(ordered), 1.0,
        rel_tol=1e-6, abs_tol=1e-6
    ):
        raise ModelCompatibilityError(
            "Probabilities are not normalized."
        )

    return dict(zip(expected, ordered))

def validate_feature_row(feature_row, feature_names):
    if not isinstance(feature_row, dict):
        raise PredictionUnavailable(
            "Verified model features are unavailable."
        )

    if not isinstance(feature_names, list):
        raise ModelCompatibilityError("Invalid feature schema.")

    missing = [
        name for name in feature_names
        if name not in feature_row
    ]

    if missing:
        raise PredictionUnavailable(
            "Required features missing: "
            + ", ".join(missing[:8])
        )

    # No automatic zero filling, invented Elo, or fabricated
    # team statistics. Actual model preprocessing must match
    # the validated training feature generator.
    row = {}

    for name in feature_names:
        value = feature_row[name]

        if isinstance(value, bool):
            raise ModelCompatibilityError(
                "Boolean feature supplied: " + name
            )

        try:
            number = float(value)
        except (ValueError, TypeError):
            raise PredictionUnavailable(
                "Non-numeric feature: " + name
            )

        if not math.isfinite(number):
            raise PredictionUnavailable(
                "Missing/nonfinite feature: " + name
            )

        row[name] = number

    return row

def validate_batch(fixtures):
    if not isinstance(fixtures, list):
        raise ValueError("Fixtures must be a list.")

    validate_limit(len(fixtures))

    seen = set()

    for fixture in fixtures:
        if not isinstance(fixture, dict):
            raise ValueError("Invalid fixture record.")

        fid = str(fixture.get("fixture_id") or "").strip()

        if not fid:
            raise ValueError("Fixture identifier required.")

        if fid in seen:
            raise ValueError("Duplicate fixture identifier.")

        seen.add(fid)

    return True

def staging_predict_one(
    market,
    verified_feature_row,
    registry_path
):
    """
    Internal staging helper only.

    Caller must have independently verified:
      - fixture identity
      - pre-kickoff feature provenance
      - data cutoff and source
      - model and market eligibility

    This function DOES NOT authenticate users or charge credits.
    It MUST NOT be exposed directly as a public endpoint.
    """

    registry = load_registry(registry_path)
    models = registry["selected_worldwide_models"]

    if market not in models:
        raise PredictionUnavailable(
            "No selected worldwide model for this market."
        )

    specification = models[market]

    if specification.get("production_approved") is True:
        raise ModelCompatibilityError(
            "Staging registry unexpectedly marked approved."
        )

    row = validate_feature_row(
        verified_feature_row,
        specification["feature_names"]
    )

    path = Path(specification["path"])

    if not path.is_file():
        raise PredictionUnavailable("Model artifact missing.")

    if file_hash(path) != specification["sha256"]:
        raise ModelCompatibilityError(
            "Model artifact hash changed."
        )

    # Only use trusted, locally generated model artifacts.
    # joblib deserialization can execute Python code.
    import joblib
    import pandas as pd

    model = joblib.load(path)

    names = specification["feature_names"]

    trained_names = getattr(
        model, "feature_names_in_", None
    )

    if trained_names is not None:
        if list(map(str, trained_names)) != names:
            raise ModelCompatibilityError(
                "Trained feature order mismatch."
            )

    trained_count = getattr(
        model, "n_features_in_", None
    )

    if trained_count is not None:
        if int(trained_count) != len(names):
            raise ModelCompatibilityError(
                "Trained feature count mismatch."
            )

    if not callable(getattr(model, "predict_proba", None)):
        raise ModelCompatibilityError(
            "Model cannot return probabilities."
        )

    classes = getattr(model, "classes_", None)

    if classes is None:
        raise ModelCompatibilityError(
            "Model class order unavailable."
        )

    X = pd.DataFrame(
        [[row[name] for name in names]],
        columns=names
    )

    probabilities = model.predict_proba(X)

    if getattr(probabilities, "shape", None) != (
        1, len(classes)
    ):
        raise ModelCompatibilityError(
            "Unexpected probability output shape."
        )

    result = canonical_probability_vector(
        probabilities[0],
        classes,
        specification["required_class_order"]
    )

    return {
        "market": market,
        "probabilities": result,
        "model_version": specification["version"],
        "model_sha256": specification["sha256"],
        "staging_only": True,
        "fixture_verified_by_this_function": False,
        "features_verified_by_this_function": False,
    }
