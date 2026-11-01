
from pathlib import Path
from difflib import SequenceMatcher
from urllib.parse import quote
import sqlite3
import unicodedata


EVIDENCE_RANK = {
    'EXACT_NORMALIZED_NAME': 3,
    'UNAPPROVED_PROVIDER_ALIAS': 2,
    'FUZZY_HUMAN_REVIEW_ONLY': 1,
    'NAME_ONLY_EXACT_HISTORICAL': 3,
    'NAME_ONLY_UNAPPROVED_ALIAS': 2,
    'NAME_ONLY_FUZZY_REVIEW': 1,
}


def normalize_club_name(value):
    """Use the SAME normalization policy as Step 12B.1A."""
    value = str(value or '').casefold()
    for left, right in (
        ('ß', 'ss'), ('æ', 'ae'), ('œ', 'oe'),
        ('ø', 'o'), ('ł', 'l')
    ):
        value = value.replace(left, right)
    value = unicodedata.normalize('NFKD', value)
    value = ''.join(c for c in value if not unicodedata.combining(c))
    return ' '.join(''.join(c if c.isalnum() else ' ' for c in value).split())


class ClubIdentityResolver:
    """Deterministic research resolver; never grants approval."""

    def __init__(self, db_path):
        self.db_path = Path(db_path).resolve()
        if not self.db_path.is_file():
            raise FileNotFoundError(self.db_path)
        # SQLite URI read-only. No migration or changes to original registry.
        uri = 'file:' + quote(str(self.db_path), safe='/') + '?mode=ro'
        self.connection = sqlite3.connect(uri, uri=True)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute('PRAGMA query_only=ON')
        self._verify_schema()

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def _verify_schema(self):
        required = {
            'historical_clubs', 'historical_aliases',
            'league_proposals', 'provider_teams',
            'provider_names', 'candidate_links',
            'cross_provider_alias_hints'
        }
        found = {
            r[0] for r in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if not required.issubset(found):
            raise RuntimeError(f'Unsupported registry schema: {sorted(required - found)}')
        if self.connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('Registry database integrity check failed')
        if self.connection.execute('PRAGMA foreign_key_check').fetchall():
            raise RuntimeError('Registry has invalid foreign keys')
        if self.connection.execute(
            "SELECT count(*) FROM league_proposals WHERE approval_status!='PENDING'"
        ).fetchone()[0]:
            raise RuntimeError('Unexpected approved league mapping in research registry')
        if self.connection.execute(
            "SELECT count(*) FROM provider_teams "
            "WHERE decision_status!='UNRESOLVED' OR approved_canonical_id IS NOT NULL"
        ).fetchone()[0]:
            raise RuntimeError('Unexpected approved team mapping in research registry')
        if self.connection.execute(
            "SELECT count(*) FROM candidate_links WHERE approval_status!='PROPOSAL_ONLY'"
        ).fetchone()[0]:
            raise RuntimeError('Unexpected authorized candidate link')

    def _league(self, competition):
        return self.connection.execute(
            'SELECT historical_league_id, approval_status '
            'FROM league_proposals WHERE source_competition_id=?',
            (competition,)
        ).fetchone()

    def _alias_display(self, canonical_id):
        row = self.connection.execute(
            'SELECT spelling FROM historical_aliases WHERE canonical_id=? '
            'ORDER BY observed_rows DESC, spelling LIMIT 1',
            (canonical_id,)
        ).fetchone()
        return row['spelling'] if row else ''

    def _enrich(self, evidence, limit=5):
        # evidence: canonical_id -> (rank, evidence_type, score)
        data = []
        for cid, (rank, kind, score) in evidence.items():
            club = self.connection.execute(
                'SELECT historical_league_id, historical_team_key '
                'FROM historical_clubs WHERE canonical_id=?',
                (cid,)
            ).fetchone()
            if club is None:
                raise RuntimeError('Orphaned canonical ID in resolver evidence')
            data.append({
                'canonical_id': cid,
                'historical_league_id': club['historical_league_id'],
                'historical_team_key': club['historical_team_key'],
                'display_name': self._alias_display(cid),
                'evidence_type': kind,
                'similarity': round(float(score), 6),
                '_rank': rank,
            })
        data.sort(key=lambda item: (
            -item['_rank'], -item['similarity'], item['canonical_id']
        ))
        total = len(data)
        for item in data:
            del item['_rank']
        return data[:limit], total

    def _name_evidence(self, historical_league, name, competition):
        """Names alone are only suggestions; never independent ID proof."""
        normalized = normalize_club_name(name)
        if not normalized:
            return {}
        found = {}
        exact = self.connection.execute(
            'SELECT a.canonical_id FROM historical_aliases a '
            'JOIN historical_clubs c ON c.canonical_id=a.canonical_id '
            'WHERE c.historical_league_id=? AND a.normalized=?',
            (historical_league, normalized)
        ).fetchall()
        for item in exact:
            found[item['canonical_id']] = (
                3, 'NAME_ONLY_EXACT_HISTORICAL', 1.0
            )
        # Names known from the original Bundesliga source may differ
        # from ESPN names. Hints are never approved equivalences.
        if not found and competition == 'bl1':
            alternatives = self.connection.execute(
                'SELECT right_spelling FROM cross_provider_alias_hints '
                'WHERE left_spelling=? AND approval_status=?',
                (normalized, 'UNAPPROVED')
            ).fetchall()
            for alternative in alternatives:
                for item in self.connection.execute(
                    'SELECT a.canonical_id FROM historical_aliases a '
                    'JOIN historical_clubs c ON c.canonical_id=a.canonical_id '
                    'WHERE c.historical_league_id=? AND a.normalized=?',
                    (historical_league, alternative['right_spelling'])
                ):
                    found[item['canonical_id']] = (
                        2, 'NAME_ONLY_UNAPPROVED_ALIAS', 1.0
                    )
        if found:
            return found
        # No auto translation, no deleting meaningful team words.
        # Fuzzy names help display candidates, not choose an identity.
        for item in self.connection.execute(
            'SELECT a.canonical_id, a.normalized FROM historical_aliases a '
            'JOIN historical_clubs c ON c.canonical_id=a.canonical_id '
            'WHERE c.historical_league_id=?',
            (historical_league,)
        ):
            ratio = SequenceMatcher(
                None, normalized, item['normalized']
            ).ratio()
            if ratio >= 0.70:
                cid = item['canonical_id']
                existing = found.get(cid, (0, '', 0))
                if ratio > existing[2]:
                    found[cid] = (1, 'NAME_ONLY_FUZZY_REVIEW', ratio)
        return found

    def resolve(self, source, competition, provider_team_id=None,
                display_name=None, limit=5):
        """Return candidate keys, all *unauthorized* for model features."""
        source = str(source or '').strip()
        competition = str(competition or '').strip()
        provider_team_id = str(provider_team_id or '').strip()
        display_name = str(display_name or '').strip()
        if not source or not competition or not isinstance(limit, int) or not 1 <= limit <= 20:
            raise ValueError('Provide source, competition and a result limit of 1–20')
        if not provider_team_id and not display_name:
            raise ValueError('Provide a provider team ID or a display name')
        league = self._league(competition)
        base = {
            'source': source,
            'source_competition_id': competition,
            'provider_team_id': provider_team_id,
            'input_name': display_name,
            'proposed_historical_league_id': (
                league['historical_league_id'] if league else None
            ),
            'league_approved': False,
            'identity_approved': False,
            'model_feature_use_authorized': False,
            'auto_registered_alias': False,
            'needs_user_confirmation': False,
            'candidates': [],
            'candidate_count_total': 0,
        }
        if league is None:
            return {**base, 'status': 'UNKNOWN_COMPETITION_REVIEW'}

        evidence = {}
        known_provider = False
        observed_norms = set()
        if provider_team_id:
            provider = self.connection.execute(
                'SELECT decision_status, approved_canonical_id '
                'FROM provider_teams WHERE source=? AND source_competition_id=? '
                'AND source_team_id=?',
                (source, competition, provider_team_id)
            ).fetchone()
            known_provider = provider is not None
            if known_provider:
                if provider['approved_canonical_id'] is not None:
                    raise RuntimeError('Research registry unexpectedly contains approval')
                observed_norms = {
                    row['normalized']
                    for row in self.connection.execute(
                        'SELECT normalized FROM provider_names WHERE source=? '
                        'AND source_competition_id=? AND source_team_id=?',
                        (source, competition, provider_team_id)
                    )
                }
                for row in self.connection.execute(
                    'SELECT canonical_id, evidence_type, similarity '
                    'FROM candidate_links WHERE source=? AND source_competition_id=? '
                    'AND source_team_id=?',
                    (source, competition, provider_team_id)
                ):
                    kind = row['evidence_type']
                    evidence[row['canonical_id']] = (
                        EVIDENCE_RANK.get(kind, 0), kind, float(row['similarity'])
                    )

        # If the ID is unknown, candidate names are still useful for
        # interactive clarification, but MUST NOT authorize a source-ID link.
        if not known_provider and display_name:
            evidence = self._name_evidence(
                league['historical_league_id'], display_name, competition
            )

        candidates, total = self._enrich(evidence, limit)
        base.update({
            'known_provider_id': known_provider,
            'candidates': candidates,
            'candidate_count_total': total,
        })
        mismatch = bool(
            known_provider and display_name and
            normalize_club_name(display_name) not in observed_norms
        )
        if mismatch:
            status = 'SOURCE_ID_DISPLAY_NAME_MISMATCH_REVIEW'
        elif not evidence:
            status = 'UNKNOWN_SOURCE_ID_NO_CANDIDATE' if not known_provider else 'NO_CANDIDATE_FOUND'
        else:
            top_rank = max(item[0] for item in evidence.values())
            top_candidates = sum(item[0] == top_rank for item in evidence.values())
            if top_candidates > 1:
                status = 'AMBIGUOUS_CANDIDATES_CONFIRMATION_REQUIRED'
            elif top_rank == 3:
                status = 'EXACT_HISTORICAL_CANDIDATE_NOT_APPROVED'
            elif top_rank == 2:
                status = 'UNAPPROVED_ALIAS_CANDIDATE'
            else:
                status = 'FUZZY_CANDIDATES_REVIEW_ONLY'
        if provider_team_id and not known_provider:
            status = 'UNREGISTERED_PROVIDER_ID_REVIEW__' + status
        base['status'] = status
        base['needs_user_confirmation'] = bool(provider_team_id and not known_provider) or status in {
            'AMBIGUOUS_CANDIDATES_CONFIRMATION_REQUIRED',
            'UNKNOWN_SOURCE_ID_NO_CANDIDATE',
            'SOURCE_ID_DISPLAY_NAME_MISMATCH_REVIEW',
            'FUZZY_CANDIDATES_REVIEW_ONLY'
        }
        return base

    def resolve_fixture(self, source, competition,
                        home_id, home_name, away_id, away_name):
        home = self.resolve(source, competition, home_id, home_name)
        away = self.resolve(source, competition, away_id, away_name)
        same_id = bool(home_id and away_id and str(home_id) == str(away_id))
        collision = bool(
            home['candidates'] and away['candidates'] and
            home['candidates'][0]['canonical_id'] ==
            away['candidates'][0]['canonical_id']
        )
        return {
            'home': home,
            'away': away,
            'provider_id_collision': same_id,
            'top_candidate_collision': collision,
            'prediction_authorized': False,
            'status': (
                'HOLD_SAME_PROVIDER_TEAM_ID' if same_id else
                'HOLD_SAME_HISTORICAL_CANDIDATE' if collision else
                'RESEARCH_CANDIDATES_ONLY_NOT_AUTHORIZED'
            ),
        }
