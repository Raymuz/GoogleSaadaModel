
from pathlib import Path
from hashlib import sha256

import importlib.util
import json
import os
import sys
import tempfile

import numpy as np
import pandas as pd


source = Path(sys.argv[1]).resolve()
expected = sys.argv[2]

old_bytes = source.read_bytes()

if sha256(old_bytes).hexdigest() != expected:
    raise RuntimeError(
        "Trusted V2M source digest changed"
    )


spec = importlib.util.spec_from_file_location(
    "gs_v2r_v2m_immutable",
    source
)

m = importlib.util.module_from_spec(spec)

sys.modules[spec.name] = m

spec.loader.exec_module(m)

original_ensure = m.ensure_history

FIELDS = [
    "id",
    "date_utc",
    "home_team_id",
    "away_team_id",
    "goals_home",
    "goals_away"
]

audit_path = Path(
    os.environ["GS_V2R_COLLISION_AUDIT"]
)


# ------------------------------------------------------------
# A. Normalize equivalent historical data safely
# ------------------------------------------------------------

def normalized(frame):

    z = frame[FIELDS].copy()

    if z[FIELDS].isna().any().any():
        raise RuntimeError(
            "Nulls in scored history"
        )

    for name in (
        "id",
        "home_team_id",
        "away_team_id"
    ):
        z[name] = z[name].astype(str)

    z["date_utc"] = pd.to_datetime(
        z["date_utc"],
        utc=True,
        errors="raise"
    ).dt.as_unit("ns")

    for name in (
        "goals_home",
        "goals_away"
    ):

        values = pd.to_numeric(
            z[name],
            errors="raise"
        )

        if not np.isfinite(
            values.to_numpy(dtype="float64")
        ).all():
            raise RuntimeError(
                "Nonfinite scored goals: " + name
            )

        if not (
            values == values.astype("int64")
        ).all():
            raise RuntimeError(
                "Noninteger scored goals: " + name
            )

        z[name] = values.astype("int64")

    z = z.sort_values(
        ["date_utc", "id"]
    ).reset_index(drop=True)

    if z["id"].duplicated().any():
        raise RuntimeError(
            "Repeated scored match ID"
        )

    return z


# ------------------------------------------------------------
# B. Preserve original historical integrity checks
# ------------------------------------------------------------

def strict_history(
    world,
    added,
    pinned_hash,
    proof_hash
):

    try:

        return original_ensure(
            world,
            added,
            pinned_hash,
            proof_hash
        )

    except RuntimeError as exc:

        if str(exc) != (
            "Previously staged history differs; "
            "refusing overwrite"
        ):
            raise

    # Original function detected a filename collision.
    # Reconstruct both historical datasets and compare.

    identity_digest = sha256(
        json.dumps(
            {
                "pinned": pinned_hash,
                "proof": proof_hash,
                "espn_ids": sorted(
                    str(r["id"]) for r in added
                )
            },
            sort_keys=True
        ).encode()
    ).hexdigest()[:16]

    original = (
        m.STAGE /
        (
            "v2m_verified_world_history_"
            + identity_digest
            + ".parquet"
        )
    )

    if (
        not original.is_file()
        or original.is_symlink()
    ):
        raise RuntimeError(
            "Colliding history must be an "
            "existing regular file"
        )

    base = world[FIELDS].copy()

    for name in (
        "id",
        "home_team_id",
        "away_team_id"
    ):
        base[name] = base[name].astype(str)

    extra = pd.DataFrame(
        added,
        columns=FIELDS
    )

    previous = normalized(
        pd.read_parquet(original)
    )

    revised = normalized(
        pd.concat(
            [base, extra],
            ignore_index=True
        )
    )

    # Fixture identities may never change silently.

    if set(previous["id"]) != set(revised["id"]):
        raise RuntimeError(
            "History fixture identities differ; "
            "automatic versioning blocked"
        )

    old = previous.set_index("id")
    new = revised.set_index("id")

    old = old.loc[sorted(old.index)]
    new = new.loc[sorted(new.index)]

    for field in (
        "home_team_id",
        "away_team_id"
    ):

        if not old[field].equals(new[field]):
            raise RuntimeError(
                "Historical team identity changed: "
                + field
            )

    # Identify actual score/date revisions.

    changed = []

    for field in (
        "date_utc",
        "goals_home",
        "goals_away"
    ):

        difference = (
            old[field] != new[field]
        )

        for match_id in old.index[difference]:
            changed.append(
                (str(match_id), field)
            )

    match_ids = sorted(
        set(mid for mid, _ in changed)
    )

    report = {
        "schema":
            "GOOGLESAADA_V2R_HISTORY_COLLISION_V1",

        "old_path": str(original),

        "old_sha256": m.sha(original),

        "old_rows": len(previous),

        "new_rows": len(revised),

        "changed_match_count": len(match_ids),

        "change_fields": {
            field: sum(
                1 for _, value in changed
                if value == field
            )
            for field in (
                "date_utc",
                "goals_home",
                "goals_away"
            )
        },

        "changed_match_id_digest": sha256(
            json.dumps(match_ids).encode()
        ).hexdigest(),

        "old_file_preserved": True,

        "mode": None
    }

    # --------------------------------------------------------
    # C. Representation-only mismatch
    # --------------------------------------------------------

    if not changed:

        report["mode"] = (
            "SAME_VALUES_DIFFERENT_DTYPES_OR_PRECISION"
        )

        audit_path.write_text(
            json.dumps(report, indent=2) + "\n",
            encoding="utf-8"
        )

        print(
            "[PASS] Scored history values are identical "
            "after canonical normalization"
        )

        print(
            "[PASS] Original immutable history retained"
        )

        return (
            original,
            m.sha(original),
            revised
        )

    # --------------------------------------------------------
    # D. Materially different history — strict safeguards
    # --------------------------------------------------------

    maximum = int(
        os.environ[
            "GS_V2R_MAX_HISTORY_REVISIONS"
        ]
    )

    if len(match_ids) > maximum:

        report["mode"] = (
            "MATERIAL_SOURCE_DRIFT_TOO_LARGE_BLOCKED"
        )

        audit_path.write_text(
            json.dumps(report, indent=2) + "\n",
            encoding="utf-8"
        )

        raise RuntimeError(
            f"{len(match_ids)} actual historical "
            f"matches changed (limit {maximum}). "
            f"Manual source review required: {audit_path}"
        )

    # --------------------------------------------------------
    # E. Create an immutable content-addressed revision
    # --------------------------------------------------------

    canonical = [
        [
            str(t[0]),
            t[1].isoformat(),
            str(t[2]),
            str(t[3]),
            int(t[4]),
            int(t[5])
        ]
        for t in revised.itertuples(
            index=False,
            name=None
        )
    ]

    content_digest = sha256(
        json.dumps(
            canonical,
            ensure_ascii=False,
            separators=(",", ":")
        ).encode()
    ).hexdigest()[:16]

    versioned = (
        m.STAGE /
        (
            "v2m_verified_world_history_"
            + identity_digest
            + "_revision_"
            + content_digest
            + ".parquet"
        )
    )

    if versioned.exists():

        if (
            versioned.is_symlink()
            or not normalized(
                pd.read_parquet(versioned)
            ).equals(revised)
        ):
            raise RuntimeError(
                "Existing content-addressed history "
                "differs; refusing replacement"
            )

    else:

        temp_path = None

        try:

            with tempfile.NamedTemporaryFile(
                dir=m.STAGE,
                prefix=".gs_v2r_history_",
                suffix=".parquet",
                delete=False
            ) as item:
                temp_path = Path(item.name)

            revised.to_parquet(
                temp_path,
                index=False
            )

            if not normalized(
                pd.read_parquet(temp_path)
            ).equals(revised):
                raise RuntimeError(
                    "Serialized history failed "
                    "integrity round-trip"
                )

            # Atomic creation. Never overwrite history.
            os.link(
                temp_path,
                versioned
            )

        finally:

            if (
                temp_path
                and temp_path.exists()
            ):
                temp_path.unlink()

    report["mode"] = (
        "NEW_IMMUTABLE_VERIFIED_SOURCE_REVISION"
    )

    report["new_path"] = str(versioned)

    report["new_sha256"] = m.sha(versioned)

    audit_path.write_text(
        json.dumps(report, indent=2) + "\n",
        encoding="utf-8"
    )

    print(
        "[PASS] Verified upstream revisions:",
        len(match_ids),
        "scored matches"
    )

    print(
        "[PASS] Previous historical file preserved"
    )

    print(
        "[NEW IMMUTABLE HISTORY]",
        versioned.name
    )

    return (
        versioned,
        m.sha(versioned),
        revised
    )


# ------------------------------------------------------------
# F. Execute the ORIGINAL V2M pipeline with the safe
#    history resolver attached only in memory.
# ------------------------------------------------------------

m.ensure_history = strict_history

m.main()

if (
    sha256(source.read_bytes()).hexdigest()
    != expected
):
    raise RuntimeError(
        "Original V2M source changed during execution"
    )

