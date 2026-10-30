
import math
import numpy as np

class NativeContractError(RuntimeError):
    pass


def extract_bundle(bundle):
    """
    Accept a fitted estimator or an original training bundle.

    Supported bundle patterns:
      estimator
      {"model": estimator, "feature_columns": [...]}
      {
        "fitted": {
          "model": estimator,
          "imputer": fitted_imputer,
          "indices": selected_indices
        },
        "feature_columns": [...]
      }

    Unknown structures fail closed.
    """

    if not isinstance(bundle, dict):
        return bundle, None, None, None

    fitted = bundle.get("fitted", bundle)

    if not isinstance(fitted, dict):
        raise NativeContractError(
            "Unsupported fitted bundle structure"
        )

    model = fitted.get("model")

    if model is None:
        raise NativeContractError(
            "Original fitted model missing"
        )

    columns = (
        bundle.get("feature_columns")
        or bundle.get("native_59_feature_names")
        or fitted.get("feature_columns")
    )

    imputer = fitted.get("imputer")
    indices = fitted.get("indices")

    return model, columns, imputer, indices


def native_predict(
    bundle,
    feature_record,
    *,
    approved_columns=None,
    expected_classes=None
):
    """
    Perform genuine inference against an explicitly supplied
    fitted model object.

    This function does not load model artifacts, grant
    production approval or activate public endpoints.
    """

    import pandas as pd

    model, bundle_columns, imputer, indices = (
        extract_bundle(bundle)
    )

    columns = (
        list(approved_columns)
        if approved_columns is not None
        else list(bundle_columns)
        if bundle_columns is not None
        else None
    )

    if not columns:
        raise NativeContractError(
            "Native feature names unavailable"
        )

    if (
        len(set(columns)) != len(columns)
        or not all(
            isinstance(x, str) and x
            for x in columns
        )
    ):
        raise NativeContractError(
            "Invalid original feature contract"
        )

    if not isinstance(feature_record, dict):
        raise NativeContractError(
            "Features must be a named record"
        )

    missing = [
        name for name in columns
        if name not in feature_record
    ]

    unexpected = [
        name for name in feature_record
        if name not in columns
    ]

    if missing or unexpected:
        raise NativeContractError(
            "Feature contract mismatch; "
            f"missing={missing[:8]}, "
            f"unexpected={unexpected[:8]}"
        )

    # Exact preserved order, not alphabetical order.
    row = {
        name: feature_record[name]
        for name in columns
    }

    X = pd.DataFrame([row], columns=columns)

    for name in columns:
        X[name] = pd.to_numeric(
            X[name], errors="raise"
        )

    values = X.to_numpy(dtype=float)

    # NaN may be valid when the original fitted imputer
    # exists; infinity is never permitted.
    if np.isinf(values).any():
        raise NativeContractError(
            "Infinite feature value"
        )

    if (
        np.isnan(values).any()
        and imputer is None
    ):
        # A fitted pipeline might implement imputation.
        # Without evidence, do not assume that it does.
        if not hasattr(model, "named_steps"):
            raise NativeContractError(
                "Missing values without fitted imputer"
            )

    # Reuse the ORIGINAL fitted transformer.
    # Never fit a new imputer at inference time.
    if imputer is not None:
        if not callable(
            getattr(imputer, "transform", None)
        ):
            raise NativeContractError(
                "Fitted imputer unavailable"
            )

        values = np.asarray(
            imputer.transform(X),
            dtype=float
        )

    # Use the ORIGINAL selected feature indices.
    if indices is not None:
        selected = np.asarray(indices)

        if selected.dtype.kind == "b":
            if selected.ndim != 1 or len(selected) != values.shape[1]:
                raise NativeContractError(
                    "Invalid boolean feature mask"
                )
            selected = np.flatnonzero(selected)

        elif selected.dtype.kind in ("i", "u"):
            if selected.ndim != 1:
                raise NativeContractError(
                    "Invalid selected feature indices"
                )
            selected = selected.astype(int)

        else:
            raise NativeContractError(
                "Unsupported selected index type"
            )

        if (
            len(selected) == 0
            or len(set(selected.tolist())) != len(selected)
            or np.any(selected < 0)
            or np.any(selected >= values.shape[1])
        ):
            raise NativeContractError(
                "Selected feature indices out of range"
            )

        values = values[:, selected]

    # A direct fitted estimator expecting names can
    # receive the original named dataframe when no
    # separate fitted preprocessing/selection is used.
    model_input = (
        X if imputer is None and indices is None
        else values
    )

    expected_count = getattr(
        model, "n_features_in_", None
    )

    if (
        expected_count is not None
        and int(expected_count) != model_input.shape[1]
    ):
        raise NativeContractError(
            "Fitted model feature count mismatch"
        )

    # Classification.
    if callable(
        getattr(model, "predict_proba", None)
    ):
        probabilities = np.asarray(
            model.predict_proba(model_input),
            dtype=float
        )

        if probabilities.ndim != 2 or probabilities.shape[0] != 1:
            raise NativeContractError(
                "Invalid classification output shape"
            )

        classes = getattr(model, "classes_", None)

        if classes is None:
            if expected_classes is None:
                raise NativeContractError(
                    "Original class labels unavailable"
                )
            classes = expected_classes

        classes = [
            str(value)
            for value in list(classes)
        ]

        if len(classes) != probabilities.shape[1]:
            raise NativeContractError(
                "Class probability dimension mismatch"
            )

        if not np.isfinite(probabilities).all():
            raise NativeContractError(
                "Nonfinite classification probabilities"
            )

        if (
            np.any(probabilities < -1e-8)
            or np.any(probabilities > 1 + 1e-8)
            or not np.isclose(
                probabilities.sum(axis=1)[0],
                1.0,
                atol=1e-5
            )
        ):
            raise NativeContractError(
                "Invalid probability distribution"
            )

        return {
            "task_type": "classification",
            "classes": classes,
            "probabilities": probabilities[0].tolist(),
            "predicted_class": classes[
                int(np.argmax(probabilities[0]))
            ],
            "feature_count": len(columns),
            "model_input_width": int(
                model_input.shape[1]
            )
        }

    # Regression, including fitted Poisson predictors.
    if callable(
        getattr(model, "predict", None)
    ):
        prediction = np.asarray(
            model.predict(model_input),
            dtype=float
        ).reshape(-1)

        if len(prediction) != 1 or not np.isfinite(prediction[0]):
            raise NativeContractError(
                "Invalid regression output"
            )

        return {
            "task_type": "regression",
            "prediction": float(prediction[0]),
            "feature_count": len(columns),
            "model_input_width": int(
                model_input.shape[1]
            )
        }

    raise NativeContractError(
        "Fitted model has no supported inference method"
    )
