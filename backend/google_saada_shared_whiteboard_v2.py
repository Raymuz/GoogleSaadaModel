"""GoogleSaada staging-only append-only per-fixture model-output whiteboard."""
import copy
import math

class SharedWhiteboard:
    def __init__(self, fixture_ids, markets):
        self.fixture_ids = frozenset(fixture_ids)
        self.markets = frozenset(markets)
        self._records = {}

    def publish(self, fixture_id, market, specialist, probabilities, evidence_id):
        if fixture_id not in self.fixture_ids or market not in self.markets:
            raise ValueError('Unregistered fixture or market')
        if not isinstance(specialist, str) or not specialist.strip():
            raise ValueError('Specialist identity required')
        if not isinstance(evidence_id, str) or not evidence_id.strip():
            raise ValueError('Evidence provenance required')
        if not isinstance(probabilities, dict):
            raise ValueError('Probability distribution required')
        expected = {'H', 'D', 'A'} if market == 'match_result' else {'0','1'}
        if set(probabilities) != expected:
            raise ValueError('Incorrect class labels')
        vals = tuple(probabilities.values())
        if any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in vals):
            raise ValueError('Invalid probability')
        if not math.isclose(sum(vals), 1.0, abs_tol=1e-6, rel_tol=1e-6):
            raise ValueError('Probabilities not normalized')
        record = self._records.setdefault(fixture_id, {}).setdefault(market, {})
        if specialist in record:
            raise ValueError('Duplicate specialist')
        record[specialist] = {'probabilities': dict(probabilities), 'evidence_id': evidence_id}

    def snapshot(self):
        return copy.deepcopy(self._records)
