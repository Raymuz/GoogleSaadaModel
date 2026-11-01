"""GoogleSaada V2F: signed-evidence, staging-only collaborative inference.

The signing key is held by a *trusted server-side evidence issuer*, never by
Gradio text fields or users. This module is not an authentication gateway or a
live feature builder; the current live prediction callback remains unchanged.

No fallback features, synthetic results, new model weights, quota mutation,
public endpoint, or HF deployment.
"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from hashlib import sha256
import copy
import gc
import hmac
import json
import re
from pathlib import Path

from google_saada_master_orchestrator_v2 import (
    MasterOrchestratorV2, SpecialistWhiteboard, SPECIALISTS,
    MARKET_BY_SPECIALIST, utc, verify_request,
)
from google_saada_manager_agent_v2 import GoogleSaadaManagerAgent
from google_saada_consensus_review_v2 import review as comparison_only_review

SCHEMA = 'GOOGLESAADA_V2F_TRUSTED_EVIDENCE_V1'
MAX_BUNDLE_BYTES = 2_000_000
TTL_SECONDS = 300
ALL_MARKETS = tuple(dict.fromkeys(MARKET_BY_SPECIALIST.values()))
REQUIRED_BODY = frozenset(('schema', 'issued_at_utc', 'expires_at_utc',
                           'fixtures', 'evidence_by_id', 'requested_markets'))

class EvidenceBoundaryError(ValueError):
    """The evidence publisher or its signed artifact violates the trust contract."""


def _canonical(body):
    if not isinstance(body, dict) or set(body) != REQUIRED_BODY:
        raise EvidenceBoundaryError('Evidence envelope fields changed')
    try:
        encoded = json.dumps(body, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, OverflowError) as exc:
        raise EvidenceBoundaryError('Evidence cannot be encoded safely') from exc
    if len(encoded) > MAX_BUNDLE_BYTES:
        raise EvidenceBoundaryError('Evidence exceeds staging size cap')
    return encoded


def _key(secret):
    if not isinstance(secret, bytes) or len(secret) < 32:
        raise EvidenceBoundaryError('Trusted server signing key must be >=32 bytes')
    return secret


def _claims(body, now):
    if body['schema'] != SCHEMA:
        raise EvidenceBoundaryError('Evidence schema not recognized')
    issued, expires = utc(body['issued_at_utc']), utc(body['expires_at_utc'])
    if not issued <= now < expires:
        raise EvidenceBoundaryError('Evidence is not current')
    if not timedelta(seconds=0) < expires - issued <= timedelta(seconds=TTL_SECONDS):
        raise EvidenceBoundaryError('Evidence lifetime exceeds five minutes')
    markets = body['requested_markets']
    if (not isinstance(markets, list) or not markets or
            len(markets) != len(set(markets)) or
            any(not isinstance(m, str) or m not in ALL_MARKETS for m in markets)):
        raise EvidenceBoundaryError('Unsupported signed market request')
    fixtures = body['fixtures']
    evidence = body['evidence_by_id']
    verify_request(fixtures, evidence, now)
    if set(evidence) != {f['fixture_id'] for f in fixtures}:
        raise EvidenceBoundaryError('Extra or missing fixture evidence')
    for f in fixtures:
        e = evidence[f['fixture_id']]
        if not (isinstance(e.get('evidence_id'), str) and e['evidence_id']
                and isinstance(e.get('specialist_features'), dict)):
            raise EvidenceBoundaryError('Missing feature provenance or specialist evidence')
        if len(e['specialist_features']) != len(set(e['specialist_features'])):
            raise EvidenceBoundaryError('Ambiguous specialist inputs')
        if any(k not in SPECIALISTS for k in e['specialist_features']):
            raise EvidenceBoundaryError('Unregistered model evidence')
        # Required witness of *which* audited producer issued feature values.
        # An HMAC proves an envelope was signed, not that its values were
        # causally built; that must be tested separately in the live builder.
        if not (isinstance(e.get('feature_builder_id'), str)
                and e['feature_builder_id'].strip()
                and re.fullmatch(r'[0-9a-f]{64}', str(e.get('source_sha256', '')))):
            raise EvidenceBoundaryError('Feature generator/source proof missing')
    return fixtures, evidence, markets


def sign_server_evidence(body, signing_key):
    """Use only inside the trusted source/backend boundary, never in client UI.

    This function does NOT assert that features are real. It cryptographically
    binds the source-producer's claimed evidence. Source/feature auditing is
    a separate production release requirement.
    """
    return hmac.new(_key(signing_key), _canonical(body), sha256).hexdigest()


def run_collaborative_staging(body, signature, *, signing_key, registry_path,
                              now_utc=None, adapter_factory=None):
    """Real specialists when `adapter_factory` is omitted; staging only.

    An authenticated and audited external caller is required. Do not call this
    module from public Gradio callbacks, even with an HMAC envelope, until the
    worldwide feature builder + authorization/credit settlement are certified.
    `adapter_factory` exists solely for isolated deterministic contract tests.
    """
    now = utc(now_utc or datetime.now(timezone.utc))
    raw = _canonical(body)
    if not isinstance(signature, str) or not re.fullmatch('[0-9a-f]{64}', signature):
        raise EvidenceBoundaryError('Missing signed evidence')
    check = hmac.new(_key(signing_key), raw, sha256).hexdigest()
    if not hmac.compare_digest(check, signature):
        raise EvidenceBoundaryError('Evidence signature mismatch')
    fixtures, evidence, markets = _claims(body, now)
    from google_saada_verified_specialist_adapters_v2 import VerifiedSpecialistAdapterFactory
    registry_path = Path(registry_path).resolve()
    registry = json.loads(registry_path.read_text(encoding='utf-8'))
    if registry.get('live_routing_enabled') is not False:
        raise EvidenceBoundaryError('Staging-only registry required')
    manager = GoogleSaadaManagerAgent(ALL_MARKETS)
    plan = manager.create_plan([f['fixture_id'] for f in fixtures], markets)
    factory = adapter_factory if adapter_factory is not None else VerifiedSpecialistAdapterFactory(registry_path, load_models=True)
    selected = {}
    for role in plan.assigned_specialists:
        wrapped = factory.adapter_for(role)
        def one_at_a_time(*, fixture, features, evidence, _role=role, _fn=wrapped):
            try:
                return _fn(fixture=fixture, features=features, evidence=evidence)
            finally:
                cache = getattr(factory, '_cache', None)
                if isinstance(cache, dict):
                    cache.pop(_role, None)
                gc.collect()
        selected[role] = one_at_a_time
    result = MasterOrchestratorV2(registry, adapters=selected).execute(
        copy.deepcopy(fixtures), copy.deepcopy(evidence),
        now_utc=now, requested_markets=tuple(markets))

    board = SpecialistWhiteboard([f['fixture_id'] for f in fixtures])
    for row in result['whiteboard']:
        board.publish(**row)  # validates trusted model hashes, identity, markets, values
    managerial = manager.review(plan, board)
    comparisons = comparison_only_review(board.snapshot())
    if managerial['coverage'] != result['coverage']:
        raise EvidenceBoundaryError('Manager/Orchestrator coverage diverged')
    if any(c.get('validated_weights_applied') or c.get('consensus_prediction') is not None
           for c in comparisons):
        raise EvidenceBoundaryError('Unapproved consensus calculation')
    contribution_count = len(board.snapshot())
    status = ('STAGING_EVIDENCE_UNAVAILABLE' if not contribution_count else
              'STAGING_PARTIAL' if result['failures'] else 'STAGING_ALL_ELIGIBLE_SPECIALISTS_EXECUTED')
    return {
        'status': status, 'mode': 'STAGING_ONLY_NOT_PUBLIC',
        'plan': {'fixture_ids': list(plan.fixture_ids),
                 'markets': list(plan.markets),
                 'assigned_specialists': list(plan.assigned_specialists)},
        'whiteboard': board.snapshot(),
        'coverage': result['coverage'],
        'manager_review': managerial,
        'consensus_comparison': comparisons,
        'failures': result['failures'],
        'verified_contributions': contribution_count,
        'credits_settled': False,
        'live_callback_connected': False,
        'production_release_approved': False,
        'consensus_probabilities_blended': False,
        'explanation': ('Independent specialist predictions and comparisons only. '
                        'Market disagreement is reported; no unvalidated averaging.')
    }
