
from datetime import datetime, timezone
from pathlib import Path
import base64
import hashlib
import hmac
import json
import sqlite3
import time


class PolicyDenied(Exception):
    pass


def _decode64(value):
    if not isinstance(value, str) or not value:
        raise PolicyDenied("invalid_identity")

    padding = "=" * (-len(value) % 4)

    try:
        return base64.urlsafe_b64decode(value + padding)
    except Exception:
        raise PolicyDenied("invalid_identity")


class LocalPaidPolicy:
    """
    Offline / single-host policy implementation.

    No external calls.
    No token issuance.
    No connection to the paid explanation provider.

    Requires a signed identity assertion supplied by a
    future trusted authentication integration.

    SQLite is local to one host. Not a distributed quota store.
    """

    def __init__(
        self,
        db_path,
        signing_key,
        issuer,
        audience,
        daily_requests,
        daily_budget_cents,
        reserve_cents_per_request,
        max_concurrent=1
    ):
        if not isinstance(signing_key, bytes) or len(signing_key) < 32:
            raise ValueError("Signing key must contain at least 32 bytes")

        if not issuer or not audience:
            raise ValueError("Issuer and audience are required")

        if (
            daily_requests < 1
            or daily_budget_cents < 1
            or reserve_cents_per_request < 1
            or max_concurrent < 1
        ):
            raise ValueError("Invalid security limits")

        self.db_path = str(db_path)
        self.key = signing_key
        self.issuer = issuer
        self.audience = audience
        self.daily_requests = daily_requests
        self.daily_budget = daily_budget_cents
        self.reserve_cost = reserve_cents_per_request
        self.max_concurrent = max_concurrent

        self._initialize()

    def _connect(self):
        conn = sqlite3.connect(
            self.db_path,
            timeout=5,
            isolation_level=None
        )
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _initialize(self):
        conn = self._connect()

        try:
            conn.execute("PRAGMA journal_mode=WAL")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS usage (
                    identity TEXT NOT NULL,
                    utc_day TEXT NOT NULL,
                    requests INTEGER NOT NULL,
                    reserved_cents INTEGER NOT NULL,
                    active INTEGER NOT NULL,
                    PRIMARY KEY (identity, utc_day)
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS reservations (
                    request_id TEXT PRIMARY KEY,
                    identity TEXT NOT NULL,
                    utc_day TEXT NOT NULL,
                    reserved_cents INTEGER NOT NULL,
                    state TEXT NOT NULL
                )
            """)
        finally:
            conn.close()

    def verify_identity(self, assertion):
        """
        Strict HS256 assertion verification for offline testing.

        Production authentication must use a trusted issuer,
        controlled signing keys, and separately audited integration.
        """

        if not isinstance(assertion, str) or len(assertion) > 8192:
            raise PolicyDenied("invalid_identity")

        parts = assertion.split(".")

        if len(parts) != 3:
            raise PolicyDenied("invalid_identity")

        header_b64, payload_b64, signature_b64 = parts

        signed = (header_b64 + "." + payload_b64).encode("ascii")

        expected = hmac.new(
            self.key, signed, hashlib.sha256
        ).digest()

        supplied = _decode64(signature_b64)

        if not hmac.compare_digest(expected, supplied):
            raise PolicyDenied("invalid_signature")

        try:
            header = json.loads(_decode64(header_b64))
            claims = json.loads(_decode64(payload_b64))
        except (ValueError, TypeError, UnicodeError):
            raise PolicyDenied("invalid_identity")

        if (
            not isinstance(header, dict)
            or header.get("alg") != "HS256"
            or header.get("typ") != "JWT"
            or not isinstance(claims, dict)
        ):
            raise PolicyDenied("invalid_identity")

        now = int(time.time())

        iat = claims.get("iat")
        exp = claims.get("exp")

        if (
            type(iat) is not int
            or type(exp) is not int
            or iat > now + 30
            or iat < now - 300
            or exp <= now
            or exp <= iat
            or exp - iat > 300
        ):
            raise PolicyDenied("expired_or_invalid_identity")

        if (
            claims.get("iss") != self.issuer
            or claims.get("aud") != self.audience
        ):
            raise PolicyDenied("wrong_issuer_or_audience")

        scopes = claims.get("scope", "")

        if (
            not isinstance(scopes, str)
            or "googlesaada:explain" not in scopes.split()
        ):
            raise PolicyDenied("permission_denied")

        user_id = claims.get("sub")

        if (
            not isinstance(user_id, str)
            or not user_id.strip()
            or len(user_id) > 128
        ):
            raise PolicyDenied("invalid_user")

        # Store a keyed pseudonymous identifier, not raw username.
        return hmac.new(
            self.key,
            ("googlesaada-user:" + user_id).encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

    def reserve(self, assertion, request_id):
        """
        Atomically authorize and reserve a local budget allowance.

        A denied request never invokes the provider.
        Reserved allowances are conservatively not refunded.
        """

        identity = self.verify_identity(assertion)

        if (
            not isinstance(request_id, str)
            or not (8 <= len(request_id) <= 128)
        ):
            raise PolicyDenied("invalid_request_id")

        utc_day = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        conn = self._connect()

        try:
            conn.execute("BEGIN IMMEDIATE")

            duplicate = conn.execute(
                "SELECT 1 FROM reservations WHERE request_id=?",
                (request_id,)
            ).fetchone()

            if duplicate:
                raise PolicyDenied("duplicate_request")

            row = conn.execute("""
                SELECT requests, reserved_cents, active
                FROM usage
                WHERE identity=? AND utc_day=?
            """, (identity, utc_day)).fetchone()

            requests, reserved, active = row or (0, 0, 0)

            if requests >= self.daily_requests:
                raise PolicyDenied("daily_request_quota")

            if active >= self.max_concurrent:
                raise PolicyDenied("concurrency_limit")

            if reserved + self.reserve_cost > self.daily_budget:
                raise PolicyDenied("daily_budget_limit")

            conn.execute("""
                INSERT INTO usage (
                    identity, utc_day,
                    requests, reserved_cents, active
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(identity, utc_day)
                DO UPDATE SET
                    requests = requests + 1,
                    reserved_cents = reserved_cents + excluded.reserved_cents,
                    active = active + 1
            """, (
                identity, utc_day, 1, self.reserve_cost, 1
            ))

            conn.execute("""
                INSERT INTO reservations (
                    request_id, identity, utc_day,
                    reserved_cents, state
                )
                VALUES (?, ?, ?, ?, 'ACTIVE')
            """, (
                request_id, identity, utc_day, self.reserve_cost
            ))

            conn.commit()

            return {
                "authorized": True,
                "request_id": request_id,
                "reserved_cents": self.reserve_cost
            }

        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def finish(self, request_id):
        """
        Release concurrency slot.

        Reserved budget is retained conservatively.
        This is internal bookkeeping, not a public API endpoint.
        """

        conn = self._connect()

        try:
            conn.execute("BEGIN IMMEDIATE")

            row = conn.execute("""
                SELECT identity, utc_day, state
                FROM reservations
                WHERE request_id=?
            """, (request_id,)).fetchone()

            if not row or row[2] != "ACTIVE":
                raise PolicyDenied("invalid_reservation")

            identity, utc_day, state = row

            conn.execute("""
                UPDATE reservations
                SET state='FINISHED'
                WHERE request_id=? AND state='ACTIVE'
            """, (request_id,))

            conn.execute("""
                UPDATE usage
                SET active=active-1
                WHERE identity=? AND utc_day=? AND active > 0
            """, (identity, utc_day))

            conn.commit()
            return True

        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
