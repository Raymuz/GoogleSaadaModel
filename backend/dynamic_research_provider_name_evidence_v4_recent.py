"""Versioned recent-final-results integration for the ORIGINAL ten-model provider.

The historical model snapshot and models are never rewritten. All appended
results are independently verified ESPN FINAL records from Stage A and pass
Stage B historical team-key proof checks. Disabled by default for rollout.
"""
from __future__ import annotations
from pathlib import Path
from datetime import datetime, timezone
from threading import RLock
import importlib.util
import os
import pandas as pd

HERE = Path(__file__).resolve().parent

def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError('Versioned verified source unavailable')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

PARENT = load(HERE/'dynamic_research_provider_name_evidence_v2_mass_safe.py',
              'gs15i6_base_provider_v2')
FEED = load(HERE/'google_saada_recent_scores_v1.py','gs15i6_espn_verified_finals')
OVERLAY = load(HERE/'google_saada_recent_feature_overlay_v1.py','gs15i6_verified_feature_overlay')
BASE = PARENT.BASE

class ProviderNameEvidenceResearch(PARENT.ProviderNameEvidenceResearch):
    """New opt-in inference class, preserving every old model method/check."""

    def __init__(self):
        super().__init__()
        self._gs_last_recent_context = {'status': 'NOT_REQUESTED','added': 0}
        self._gs_recent_lock = RLock()

    def _gs_verified_historical_proofs(self, finals):
        """Never trust GPT text or guess a historical team ID."""
        result = {}
        with self.resolver_module.ClubIdentityResolver(self.registry_path) as resolver:
            for event in finals:
                fixture = {
                    'source':'ESPN', 'phase':'FINAL', 'key':event['key'],
                    'league':BASE.LEAGUES and next((lg for lg,code in BASE.LEAGUES.items()
                                               if code == event['source_competition_id']),''),
                    'source_competition_id':event['source_competition_id'],
                    'source_match_id':event['source_match_id'],
                    'home':event['home'], 'away':event['away'],
                    'home_team_id':event['home_team_id'],
                    'away_team_id':event['away_team_id'],
                    'kickoff_utc':event['kickoff_utc'],
                    'source_name_options':{
                        'home':list(event['home_name_options']),
                        'away':list(event['away_name_options'])}}
                # Provider-name evidence is accepted only from the Stage A
                # server-retrieved source record, not from the Agent/user.
                prior = self._trusted_provider_names.get(event['key'])
                self._trusted_provider_names[event['key']] = fixture
                try:
                    lg, _, error = self._competition_proposal(resolver, fixture)
                    if error or not lg:
                        continue
                    before = pd.Timestamp(BASE.stamp_utc(event['kickoff_utc'])).floor('D')
                    league_name = BASE.latest_prior_unique(self._labels_league.get(lg,[]),before)
                    if not league_name:
                        continue
                    for side in ('home','away'):
                        candidate, evidence, status = self._exact_candidate(resolver,fixture,side,lg,before)
                        if (candidate and evidence.get('candidate_count') == 1
                            and evidence.get('historical_name_prior_to_kickoff')
                            and candidate.get('canonical_id')):
                            result[(event['key'],side)]={
                              'source_competition_id':event['source_competition_id'],
                              'provider_team_id':event[side+'_team_id'],
                              'canonical_id':candidate['canonical_id'],
                              'historical_league_id':lg,
                              'historical_team_key':candidate['team_key'],
                              'historical_label':evidence['historical_name_prior_to_kickoff'],
                              'league_name':league_name,
                              'candidate_count':1,
                              'evidence_kind':'SOURCE_NAME_UNIQUE_EXACT_HISTORICAL_ALIAS'}
                finally:
                    if prior is None: self._trusted_provider_names.pop(event['key'],None)
                    else: self._trusted_provider_names[event['key']] = prior
        return result

    def analyze(self, fixtures):
        # Entire compute path is serialized for this engine instance. The
        # earlier provider ancestor also uses a compatible reentrant lock.
        with self._provider_source_lock, self._gs_recent_lock:
            previous = self.feature_module.build_safe_soccer_features
            context = {'status':'FROZEN_BASELINE_DEFAULT','added':0,
                       'provider_source_errors':0}
            audit_data = {}
            if (os.environ.get('GS_RECENT_FEATURES_ENABLED','0') == '1'
                and fixtures and all(f.get('phase')=='UPCOMING' for f in fixtures)):
                codes = sorted(set(f['source_competition_id'] for f in fixtures))
                earliest = min(BASE.stamp_utc(f['kickoff_utc']) for f in fixtures)
                # The collector rejects partial provider responses and
                # same-day / future completed results.
                try:
                    finals, source = FEED.completed(
                        codes, earliest.isoformat(), self.latest_history,
                        lookback_days=14)
                    context = {'status':source['status'],
                               'provider_source_errors':source.get('source_errors',0),
                               'source_fetched_utc':source.get('source_fetch_utc'),
                               'provider_final_results':len(finals),
                               'added':0}
                    if source['status'] == 'RECENT_SOURCE_COMPLETE' and finals:
                        # Resolution independent of users/LLMs.
                        proofs = self._gs_verified_historical_proofs(finals)
                        def resolver_fn(event, side):
                            return proofs.get((event['key'],side))
                        def model_builder(original_frame):
                            features, detail = OVERLAY.build_model_features(
                                original_frame, finals, source,
                                kickoff_utc=earliest.isoformat(),
                                history_last_utc=self.latest_history,
                                trusted_identity_resolver=resolver_fn,
                                original_feature_builder=previous)
                            audit_data.update(detail)
                            return features
                        self.feature_module.build_safe_soccer_features = model_builder
                except (OSError,ValueError,LookupError,RuntimeError,TimeoutError) as exc:
                    # No partially trusted provider data reaches a model.
                    context = {'status':'FROZEN_BASELINE_SOURCE_OR_IDENTITY_ERROR',
                               'added':0, 'provider_error_type':type(exc).__name__,
                               'provider_source_errors':1}
            try:
                results = super().analyze(fixtures)
            finally:
                self.feature_module.build_safe_soccer_features = previous
            context['added'] = int(audit_data.get('added',0))
            if audit_data:
                context['status'] = audit_data.get('status',context['status'])
                context['rejected_identity_or_causality'] = audit_data.get('rejected',0)
                context['last_added_final_utc'] = audit_data.get('last_added_final_utc')
            for row in results:
                # Count only if actual original feature builder was reached.
                row['gs_recent_feature_context'] = dict(context)
            self._gs_last_recent_context = dict(context)
            return results
