"""GoogleSaada deterministic V2 manager — STAGING ONLY.

Interprets registered markets, selects eligible model specialists, reviews
append-only orchestrator snapshots. It cannot authenticate, settle credits,
choose unvalidated ensemble weights, or authorize a release.
"""
from dataclasses import dataclass
from typing import Tuple
from google_saada_master_orchestrator_v2 import MARKET_BY_SPECIALIST, SPECIALISTS

SUPPORTED_MARKETS = tuple(dict.fromkeys(MARKET_BY_SPECIALIST.values()))
MAX_MATCHES = 16

@dataclass(frozen=True)
class AnalysisPlan:
    fixture_ids: Tuple[str, ...]
    markets: Tuple[str, ...]
    assigned_specialists: Tuple[str, ...]
    status: str = 'STAGING_PLAN_ONLY'

class GoogleSaadaManagerAgent:
    def __init__(self, registered_markets):
        self.registered_markets = frozenset(registered_markets)
        if not self.registered_markets.issubset(SUPPORTED_MARKETS):
            raise ValueError('Unknown registered market')

    def create_plan(self, fixture_ids, markets):
        if not isinstance(fixture_ids, (list, tuple)) or not 1 <= len(fixture_ids) <= MAX_MATCHES:
            raise ValueError('1–16 fixture IDs required')
        ids = tuple(str(i).strip() for i in fixture_ids)
        if any(not i for i in ids) or len(ids) != len(set(ids)):
            raise ValueError('Blank or duplicate fixture ID')
        if not isinstance(markets, (list, tuple)) or not markets:
            raise ValueError('At least one market required')
        requested = tuple(dict.fromkeys(markets))
        if any(not isinstance(m, str) or m not in self.registered_markets for m in requested):
            raise ValueError('Unregistered market')
        assigned = tuple(k for k in SPECIALISTS if MARKET_BY_SPECIALIST[k] in requested)
        if not assigned:
            raise ValueError('No registered specialist for these markets')
        return AnalysisPlan(ids, requested, assigned)

    def review(self, plan, whiteboard):
        """Check coverage, never generate or blend missing probabilities."""
        if not isinstance(plan, AnalysisPlan):
            raise TypeError('AnalysisPlan required')
        records = whiteboard.snapshot()
        coverage = {fid: {market: 'UNAVAILABLE' for market in plan.markets}
                    for fid in plan.fixture_ids}
        counts = {fid: {market: 0 for market in plan.markets}
                  for fid in plan.fixture_ids}
        if isinstance(records, list):
            # Real MasterOrchestratorV2 SpecialistWhiteboard.snapshot() contract.
            for row in records:
                if not isinstance(row, dict):
                    raise ValueError('Invalid orchestrator Whiteboard row')
                fid, market, specialist = (row.get('fixture_id'), row.get('market'),
                                           row.get('specialist'))
                if (fid not in coverage or market not in coverage[fid] or
                        MARKET_BY_SPECIALIST.get(specialist) != market):
                    raise ValueError('Unregistered Whiteboard evidence')
                if not isinstance(row.get('evidence_id'), str) or not row['evidence_id']:
                    raise ValueError('Whiteboard provenance missing')
                counts[fid][market] += 1
        elif isinstance(records, dict):
            # Legacy SharedWhiteboard has fixture -> market -> specialist -> record.
            for fid, markets in records.items():
                if fid not in coverage or not isinstance(markets, dict):
                    raise ValueError('Unregistered legacy Whiteboard fixture')
                for market, members in markets.items():
                    if market not in coverage[fid] or not isinstance(members, dict):
                        raise ValueError('Unregistered legacy Whiteboard market')
                    if any(MARKET_BY_SPECIALIST.get(k) != market for k in members):
                        raise ValueError('Unknown legacy specialist')
                    counts[fid][market] += len(members)
        else:
            raise ValueError('Unknown Whiteboard snapshot format')
        for fid, markets in counts.items():
            for market, count in markets.items():
                if count:
                    coverage[fid][market] = 'AVAILABLE'
        return {'status': 'STAGING_REVIEW_ONLY', 'coverage': coverage,
                'contribution_counts': counts, 'ready_for_live_release': False}
