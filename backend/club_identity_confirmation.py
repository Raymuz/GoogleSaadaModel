
"""Optional end-user club clarification, strictly request-scoped.

Use from Gradio later as:
    with ClubIdentityResolver(registry_path) as resolver:
        session = ClubClarificationSession(resolver)
        prompt = session.prepare(source, competition, provider_id, name)
        # If prompt['confirmation_available'], render prompt['options'].
        # Only invoke session.select() when the user clicks a real option.

Never treat a user choice or name similarity as verified historical identity.
"""

import secrets
import time


class ClubClarificationSession:
    """In-memory prompt + one-time candidate choice, no persistent writes."""

    def __init__(self, resolver, ttl_seconds=1800):
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or not 60 <= ttl_seconds <= 3600:
            raise ValueError('Session TTL must be between 60 and 3600 seconds')
        self.resolver = resolver
        self.ttl_seconds = ttl_seconds
        self._pending = {}

    def prepare(self, source, competition, provider_team_id='', display_name='', limit=5):
        """Suggest a clarification prompt; never authorize a prediction."""
        response = self.resolver.resolve(
            source=source,
            competition=competition,
            provider_team_id=provider_team_id,
            display_name=display_name,
            limit=limit,
        )
        if any(response.get(flag) for flag in (
            'league_approved', 'identity_approved',
            'model_feature_use_authorized', 'auto_registered_alias'
        )):
            raise RuntimeError('An unauthorized identity was marked approved')

        candidates = response.get('candidates') or []
        # Never hide a long list behind an apparently unique first choice.
        total = int(response.get('candidate_count_total', len(candidates)))
        raw_status = response['status']
        known = bool(response.get('known_provider_id', False))

        if not candidates:
            state = 'NO_CANDIDATE_REVIEW_REQUIRED'
        elif total > len(candidates):
            state = 'TRUNCATED_CANDIDATES_ADMIN_REVIEW'
        elif total > 1:
            state = 'OPTIONAL_USER_CLARIFICATION_AVAILABLE'
        elif (not known or 'FUZZY' in raw_status or
              'ALIAS' in raw_status or 'MISMATCH' in raw_status):
            state = 'OPTIONAL_USER_CLARIFICATION_AVAILABLE'
        else:
            state = 'SINGLE_PROPOSAL_NO_PROMPT_REQUIRED'

        offer = state == 'OPTIONAL_USER_CLARIFICATION_AVAILABLE'
        options = [
            {
                'option_id': f'choice_{i + 1}',
                'club_display_name': item['display_name'] or 'Unnamed historical club',
                'historical_league_id_proposal': item['historical_league_id'],
                'reason': item['evidence_type'],
                'is_verified': False,
            }
            for i, item in enumerate(candidates)
        ]
        token = None
        if offer:
            token = secrets.token_urlsafe(24)
            self._pending[token] = {
                'created': time.monotonic(),
                'options_by_id': {
                    f'choice_{i + 1}': dict(candidate)
                    for i, candidate in enumerate(candidates)
                },
                'source': str(source),
                'competition': str(competition),
                'provider_team_id': str(provider_team_id or ''),
                'input_name': str(display_name or ''),
            }

        return {
            'source': str(source),
            'competition': str(competition),
            'provider_team_id': str(provider_team_id or ''),
            'input_name': str(display_name or ''),
            'resolution_status': raw_status,
            'prompt_status': state,
            'confirmation_available': offer,
            'candidate_count_total': total,
            'options': options if offer else [],
            'confirmation_token': token,
            'user_confirmation_is_global_approval': False,
            'identity_verified': False,
            'model_feature_use_authorized': False,
            'historical_update_authorized': False,
            'production_predictions_enabled': False,
        }

    def select(self, confirmation_token, option_id):
        """Accept only an issued option for an active one-use local prompt."""
        stored = self._pending.get(str(confirmation_token or ''))
        if stored is None:
            raise ValueError('Unknown, expired or consumed confirmation token')
        if time.monotonic() - stored['created'] > self.ttl_seconds:
            self._pending.pop(confirmation_token, None)
            raise ValueError('Club-confirmation session expired')
        item = stored['options_by_id'].get(str(option_id or ''))
        if item is None:
            raise ValueError('Selected club is not an offered candidate')
        # One confirmation token is never reusable or globally persisted.
        self._pending.pop(confirmation_token, None)
        return {
            'result': 'REQUEST_ONLY_SELECTION_RECORDED_UNVERIFIED',
            'source': stored['source'],
            'competition': stored['competition'],
            'provider_team_id': stored['provider_team_id'],
            'user_input_name': stored['input_name'],
            'selected_display_name': item['display_name'],
            'selected_canonical_id_for_request': item['canonical_id'],
            'historical_team_key_proposal': item['historical_team_key'],
            'historical_league_id_proposal': item['historical_league_id'],
            'request_scoped_only': True,
            'independently_verified_identity': False,
            'global_alias_registered': False,
            'historical_update_authorized': False,
            'model_feature_use_authorized': False,
            'production_predictions_enabled': False,
        }

    def cancel(self, confirmation_token):
        self._pending.pop(str(confirmation_token or ''), None)
        return {'status': 'CLARIFICATION_CANCELLED', 'model_feature_use_authorized': False}


def validate_fixture_pair(home_result, away_result):
    """Never permit a same-club or unverified selection into prediction."""
    h = home_result.get('selected_canonical_id_for_request', '')
    a = away_result.get('selected_canonical_id_for_request', '')
    if h and a and h == a:
        reason = 'BLOCK_SAME_CLUB_ON_BOTH_SIDES'
    else:
        reason = 'RESEARCH_FIXTURE_IDENTITIES_UNVERIFIED'
    return {
        'status': reason,
        'fixture_identity_verified': False,
        'prediction_authorized': False,
        'historical_update_authorized': False,
    }
