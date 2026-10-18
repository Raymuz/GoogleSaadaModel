"""Strict per-request unapproved identity candidates for Soccer AI research only.

This module *never* approves source team mappings. A unique, league-constrained,
exact normalized name from completed-match history may supply feature inputs for
an explicitly unverified, stale-history research run. Fuzzy matches are held.
"""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
import importlib.util
import re


import importlib.util as _ilu
BASE_FILE = Path(__file__).resolve().parent / 'dynamic_research_inference.py'
if not BASE_FILE.is_file():
    raise RuntimeError('Original Step13B.2A research inference module unavailable')
BASE_SPEC = _ilu.spec_from_file_location('soccer_original_dynamic_inference_13b21', BASE_FILE)
BASE = _ilu.module_from_spec(BASE_SPEC)
BASE_SPEC.loader.exec_module(BASE)


class ExactEvidenceResearchInference(BASE.DynamicResearchInference):
    """Source-ID lookup plus strict, request-only exact-alias evidence.

    Registered provider IDs: honor the existing immutable resolver's exact
    candidate and reject any source name mismatch. Previously unseen IDs:
    compare the current publisher display name to historical aliases strictly
    within a proposed league. Do NOT persist provider IDs or authorize mappings.
    """

    def __init__(self):
        super().__init__()
        self._run_lock = RLock()
        self._identity_diagnostics = {}

    def _competition_proposal(self, resolver, fixture):
        code = fixture['source_competition_id']
        direct = resolver._league(code)
        if direct is not None and direct['approval_status'] == 'PENDING':
            return BASE.stable_id(direct['historical_league_id']), 'REGISTRY_PENDING_PROPOSAL', None
        # Exact cross-source proposal. ESPN calls German Bundesliga 'ger.1'.
        # Original OpenLigaDB registry uses 'bl1'. Propose, NEVER approve.
        if code == 'ger.1' and fixture.get('league') == 'Bundesliga':
            alternate = resolver._league('bl1')
            if alternate is None or alternate['approval_status'] != 'PENDING':
                return None, None, 'HOLD_BUNDESLIGA_REGISTRY_EVIDENCE'
            lg = BASE.stable_id(alternate['historical_league_id'])
            names = self._labels_league.get(lg, [])
            if not any('bundesliga' in self.resolver_module.normalize_club_name(n)
                       for _, n in names):
                return None, None, 'HOLD_BUNDESLIGA_HISTORICAL_LEAGUE_NAME'
            return lg, 'ESPN_GER1_TO_OPENLIGADB_BL1_UNAPPROVED_PROPOSAL', None
        return None, None, 'HOLD_UNKNOWN_COMPETITION_PROPOSAL'

    def _exact_candidate(self, resolver, fixture, side, historical_lg, cutoff):
        source_name = str(fixture[side] or '').strip()
        source_id = str(fixture[side + '_team_id'] or '').strip()
        code = fixture['source_competition_id']
        evidence = {
            'provider_id': source_id, 'provider_name': source_name,
            'provider_id_already_in_original_registry': False,
            'evidence_kind': 'NONE', 'candidate_count': 0,
            'proposed_canonical_id': None, 'identity_approved': False,
        }
        norm = self.resolver_module.normalize_club_name(source_name)
        if (not norm or not re.fullmatch(r'\d{1,20}', source_id) or
                source_name != source_name.strip()):
            return None, evidence, 'HOLD_INVALID_PROVIDER_TEAM_EVIDENCE'

        registered = resolver.connection.execute(
            'SELECT 1 FROM provider_teams WHERE source=? '
            'AND source_competition_id=? AND source_team_id=?',
            ('ESPN', code, source_id)
        ).fetchone() is not None
        evidence['provider_id_already_in_original_registry'] = registered

        if registered:
            original = resolver.resolve('ESPN', code, source_id, source_name, limit=20)
            evidence['candidate_count'] = original['candidate_count_total']
            if (original.get('status') != 'EXACT_HISTORICAL_CANDIDATE_NOT_APPROVED'
                    or original['candidate_count_total'] != 1):
                return None, evidence, 'HOLD_KNOWN_PROVIDER_ID_NO_SINGLE_EXACT_CANDIDATE'
            candidate = original['candidates'][0]
            # A source-ID candidate does not prove its current name. Require the
            # CURRENT source name to match an alias of the exact same club.
            actual = resolver.connection.execute(
                'SELECT 1 FROM historical_aliases WHERE canonical_id=? AND normalized=?',
                (candidate['canonical_id'], norm)
            ).fetchone()
            if actual is None:
                return None, evidence, 'HOLD_KNOWN_ID_CURRENT_NAME_NOT_EXACT_HISTORICAL_ALIAS'
            evidence['evidence_kind'] = 'KNOWN_ID_AND_CURRENT_EXACT_HISTORICAL_ALIAS'
        else:
            # No source-ID approval: exact name only in the *proposed* league.
            # Both same-name duplicates and zero historical candidates are held.
            rows = resolver.connection.execute(
                'SELECT DISTINCT a.canonical_id, c.historical_league_id, '
                'c.historical_team_key FROM historical_aliases a '
                'JOIN historical_clubs c ON c.canonical_id=a.canonical_id '
                'WHERE c.historical_league_id=? AND a.normalized=?',
                (historical_lg, norm)
            ).fetchall()
            evidence['candidate_count'] = len(rows)
            if not rows:
                return None, evidence, 'HOLD_NEW_PROVIDER_ID_NO_EXACT_HISTORICAL_NAME'
            if len(rows) != 1:
                return None, evidence, 'HOLD_NEW_PROVIDER_ID_AMBIGUOUS_EXACT_HISTORICAL_NAME'
            candidate = dict(rows[0])
            evidence['evidence_kind'] = 'NEW_ID_REQUEST_ONLY_UNAPPROVED_EXACT_NAME'

        candidate_lg = BASE.stable_id(candidate['historical_league_id'])
        team_key = BASE.stable_id(candidate['historical_team_key'])
        if candidate_lg != historical_lg or not team_key:
            return None, evidence, 'HOLD_CANDIDATE_WRONG_HISTORICAL_LEAGUE'
        # Ensure model categorical labels have actually appeared prior to
        # fixture date. No future labels and no guessed replacements.
        hist_label = BASE.latest_prior_unique(
            self._labels_sides.get((historical_lg, team_key, side), []), cutoff)
        if not hist_label:
            return None, evidence, 'HOLD_NO_PRIOR_HISTORICAL_SIDE_LABEL'
        evidence['proposed_canonical_id'] = candidate['canonical_id']
        evidence['historical_name_prior_to_kickoff'] = hist_label
        return {'league_id': historical_lg,
                'team_key': team_key,
                'canonical_id': candidate['canonical_id']}, evidence, 'RESEARCH_EXACT_IDENTITY_EVIDENCE_ONLY'

    def _eligible(self, fixture, resolver):
        key = fixture['key']
        diag = {
            'source_fixture_key': key,
            'home': fixture.get('home'), 'away': fixture.get('away'),
            'source_competition_id': fixture['source_competition_id'],
            'identity_approved': False, 'persistent_mapping_written': False,
            'production_prediction_authorized': False,
        }
        self._identity_diagnostics[key] = diag
        if (fixture['phase'] != 'UPCOMING' or
                BASE.stamp_utc(fixture['kickoff_utc']) <= datetime.now(timezone.utc)):
            diag['identity_status'] = 'HOLD_NOT_UPCOMING'
            return None, 'HOLD_NOT_UPCOMING'
        if fixture['home_team_id'] == fixture['away_team_id']:
            diag['identity_status'] = 'HOLD_SAME_PROVIDER_TEAM_ID'
            return None, 'HOLD_IDENTITY_COLLISION'
        historical_lg, proposal_type, problem = self._competition_proposal(resolver, fixture)
        diag['competition_proposal_type'] = proposal_type
        diag['historical_league_id_proposed'] = historical_lg
        if problem:
            diag['identity_status'] = problem
            return None, problem
        cutoff = BASE.pd.Timestamp(BASE.stamp_utc(fixture['kickoff_utc'])).floor('D')
        home,he,hs = self._exact_candidate(resolver, fixture, 'home', historical_lg, cutoff)
        away,ae,ase = self._exact_candidate(resolver, fixture, 'away', historical_lg, cutoff)
        diag['home_identity'] = he
        diag['away_identity'] = ae
        diag['home_decision'] = hs
        diag['away_decision'] = ase
        if not home or not away:
            status = 'HOLD_EXACT_IDENTITY_EVIDENCE_INCOMPLETE'
            diag['identity_status'] = status
            return None, status
        if home['canonical_id'] == away['canonical_id'] or home['team_key'] == away['team_key']:
            diag['identity_status'] = 'HOLD_SAME_HISTORICAL_IDENTITY'
            return None, 'HOLD_IDENTITY_COLLISION'
        diag['identity_status'] = 'EXACT_PAIR_UNAPPROVED_REQUEST_SCOPED_RESEARCH'
        return {
            'league_id': historical_lg,
            'home_team_key': home['team_key'],
            'away_team_key': away['team_key'],
        }, 'RESEARCH_EXACT_PAIR_UNAPPROVED_NO_PERSISTENT_LINK'

    def analyze(self, fixtures):
        # Original feature builder uses a large frozen-history state; serialize
        # calls to protect the request-local diagnostic context.
        with self._run_lock:
            self._identity_diagnostics = {}
            # Reject one source team ID receiving multiple contradictory names
            # in the same fetched fixture batch (never guess which is correct).
            source_names = defaultdict(set)
            for fx in fixtures:
                if not isinstance(fx, dict):
                    raise ValueError('Provider fixture record invalid')
                for side in ('home','away'):
                    k = (fx.get('source'), fx.get('source_competition_id'),
                         str(fx.get(side + '_team_id')))
                    source_names[k].add(self.resolver_module.normalize_club_name(fx.get(side)))
            if any(len(names) != 1 for names in source_names.values()):
                raise ValueError('Provider team ID has inconsistent names within source batch')
            results = super().analyze(fixtures)
            for result in results:
                result['request_scoped_identity_evidence'] = dict(
                    self._identity_diagnostics.get(result['key'], {}))
                # Freeze the source/authorization distinction on every row.
                result['identity_approved'] = False
                result['calibration_verified'] = False
                result['production_prediction_authorized'] = False
            return results
