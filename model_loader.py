
import hashlib
import json
from pathlib import Path

import joblib

BASE = Path(__file__).resolve().parent

DEPLOY = BASE / "deployment_files"

MANIFEST = DEPLOY / "manifest.json"


def _sha256(path):

    digest = hashlib.sha256()

    with path.open("rb") as stream:

        for chunk in iter(
            lambda: stream.read(1024 * 1024),
            b""
        ):
            digest.update(chunk)

    return digest.hexdigest()


def load_soccer_models():

    manifest = json.loads(
        MANIFEST.read_text(encoding="utf-8")
    )

    models = {}

    for role, metadata in manifest["models"].items():

        filename = metadata["filename"]

        path = DEPLOY / "models" / filename

        if not path.is_file():
            raise FileNotFoundError(path)

        if _sha256(path) != metadata["source_sha256"]:
            raise RuntimeError(
                "Model checksum verification failed: "
                + role
            )

        models[role] = joblib.load(path)

    return models


def production_predictions_enabled():

    manifest = json.loads(
        MANIFEST.read_text(encoding="utf-8")
    )

    return bool(
        manifest.get(
            "production_predictions_enabled",
            False
        )
    )
