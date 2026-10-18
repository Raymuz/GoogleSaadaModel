
from dataclasses import dataclass
import re
import uuid


class QuotaDenied(Exception):
    """A reservation was rejected by the configured limits."""


class ReservationStateError(Exception):
    """A reservation could not be finished safely."""


@dataclass(frozen=True)
class QuotaLimits:
    user_daily_requests: int
    global_daily_requests: int
    user_daily_budget_cents: int
    global_daily_budget_cents: int
    reserve_cents_per_request: int
    max_concurrent_per_user: int

    def __post_init__(self):
        limits = (
            self.user_daily_requests,
            self.global_daily_requests,
            self.user_daily_budget_cents,
            self.global_daily_budget_cents,
            self.reserve_cents_per_request,
            self.max_concurrent_per_user,
        )

        if any(
            type(value) is not int or value <= 0
            for value in limits
        ):
            raise ValueError(
                "Quota limits must be positive integers"
            )

        if self.reserve_cents_per_request > min(
            self.user_daily_budget_cents,
            self.global_daily_budget_cents,
        ):
            raise ValueError(
                "Reservation exceeds configured budget"
            )


class PostgresQuotaEngine:
    """
    Atomic reservation bookkeeping for a trusted
    PostgreSQL backend.

    Does not authenticate identities or call the
    explanation provider.

    Pass only verified, server-derived principal digests.

    The connection factory must yield fresh,
    transactional PostgreSQL connections.
    """

    def __init__(self, connection_factory, limits):
        if not callable(connection_factory):
            raise ValueError(
                "Connection factory must be callable"
            )

        if not isinstance(limits, QuotaLimits):
            raise ValueError("Invalid quota limits")

        self.connection_factory = connection_factory
        self.limits = limits

    @staticmethod
    def _principal(value):
        if (
            not isinstance(value, str)
            or re.fullmatch(r"[0-9a-f]{64}", value) is None
        ):
            raise ValueError(
                "Invalid trusted principal digest"
            )

        return value

    @staticmethod
    def _request_id(value):
        try:
            rid = uuid.UUID(str(value))
        except (TypeError, ValueError, AttributeError):
            raise ValueError(
                "Invalid request identifier"
            ) from None

        if rid.version != 4:
            raise ValueError(
                "Request identifier must be UUIDv4"
            )

        return rid

    def reserve(self, principal_digest, request_id):
        """
        Reserve allowance before calling a billable API.

        Any denial rolls back the entire transaction.
        """

        principal = self._principal(principal_digest)
        rid = self._request_id(request_id)
        lim = self.limits

        with self.connection_factory() as conn:
            with conn.transaction():
                with conn.cursor() as cur:

                    # Use the database clock in UTC.
                    cur.execute(
                        "SELECT (CURRENT_TIMESTAMP "
                        "AT TIME ZONE 'UTC')::date"
                    )

                    today = cur.fetchone()[0]

                    if today is None:
                        raise RuntimeError(
                            "Database did not provide UTC day"
                        )

                    # Create missing accounting rows.
                    cur.execute("""
                        INSERT INTO gs_agent_principals
                            (principal_digest)
                        VALUES (%s)
                        ON CONFLICT DO NOTHING
                    """, (principal,))

                    cur.execute("""
                        INSERT INTO gs_agent_daily_user
                            (principal_digest, usage_day)
                        VALUES (%s, %s)
                        ON CONFLICT DO NOTHING
                    """, (principal, today))

                    cur.execute("""
                        INSERT INTO gs_agent_daily_global
                            (usage_day)
                        VALUES (%s)
                        ON CONFLICT DO NOTHING
                    """, (today,))

                    # Fixed lock order:
                    # Principal -> user/day -> global/day.

                    cur.execute("""
                        SELECT active_requests
                        FROM gs_agent_principals
                        WHERE principal_digest=%s
                        FOR UPDATE
                    """, (principal,))

                    active_row = cur.fetchone()

                    cur.execute("""
                        SELECT request_count, reserved_cents
                        FROM gs_agent_daily_user
                        WHERE principal_digest=%s
                          AND usage_day=%s
                        FOR UPDATE
                    """, (principal, today))

                    user_row = cur.fetchone()

                    cur.execute("""
                        SELECT request_count, reserved_cents
                        FROM gs_agent_daily_global
                        WHERE usage_day=%s
                        FOR UPDATE
                    """, (today,))

                    global_row = cur.fetchone()

                    if (
                        active_row is None
                        or user_row is None
                        or global_row is None
                    ):
                        raise RuntimeError(
                            "Quota row unavailable"
                        )

                    # Reject duplicate reservations.
                    cur.execute("""
                        SELECT request_id
                        FROM gs_agent_reservations
                        WHERE request_id=%s
                    """, (rid,))

                    if cur.fetchone() is not None:
                        raise QuotaDenied(
                            "duplicate_request"
                        )

                    active = active_row[0]

                    user_requests, user_reserved = user_row
                    global_requests, global_reserved = global_row

                    amount = lim.reserve_cents_per_request

                    # Concurrency protection.
                    if active >= lim.max_concurrent_per_user:
                        raise QuotaDenied(
                            "concurrency_limit"
                        )

                    # User request quota.
                    if user_requests >= lim.user_daily_requests:
                        raise QuotaDenied(
                            "user_daily_request_quota"
                        )

                    # Global request quota.
                    if global_requests >= lim.global_daily_requests:
                        raise QuotaDenied(
                            "global_daily_request_quota"
                        )

                    # User spending allowance.
                    if (
                        user_reserved + amount
                        > lim.user_daily_budget_cents
                    ):
                        raise QuotaDenied(
                            "user_daily_budget"
                        )

                    # Global spending allowance.
                    if (
                        global_reserved + amount
                        > lim.global_daily_budget_cents
                    ):
                        raise QuotaDenied(
                            "global_daily_budget"
                        )

                    # Update active concurrency.
                    cur.execute("""
                        UPDATE gs_agent_principals
                        SET active_requests=active_requests+1
                        WHERE principal_digest=%s
                    """, (principal,))

                    # Update user usage.
                    cur.execute("""
                        UPDATE gs_agent_daily_user
                        SET request_count=request_count+1,
                            reserved_cents=reserved_cents+%s
                        WHERE principal_digest=%s
                          AND usage_day=%s
                    """, (
                        amount,
                        principal,
                        today
                    ))

                    # Update global usage.
                    cur.execute("""
                        UPDATE gs_agent_daily_global
                        SET request_count=request_count+1,
                            reserved_cents=reserved_cents+%s
                        WHERE usage_day=%s
                    """, (amount, today))

                    # Record reservation.
                    cur.execute("""
                        INSERT INTO gs_agent_reservations
                        (
                            request_id,
                            principal_digest,
                            usage_day,
                            reserved_cents,
                            state
                        )
                        VALUES (%s, %s, %s, %s, 'ACTIVE')
                    """, (
                        rid,
                        principal,
                        today,
                        amount
                    ))

        # Returned only after successful transaction exit.
        return {
            "authorized": True,
            "request_id": str(rid),
            "usage_day": str(today),
            "reserved_cents": amount,
        }

    def finish(self, request_id):
        """
        Release the active concurrency slot.

        Reserved budget is retained conservatively.
        """

        rid = self._request_id(request_id)

        with self.connection_factory() as conn:
            with conn.transaction():
                with conn.cursor() as cur:

                    cur.execute("""
                        SELECT principal_digest, state
                        FROM gs_agent_reservations
                        WHERE request_id=%s
                        FOR UPDATE
                    """, (rid,))

                    row = cur.fetchone()

                    if row is None or row[1] != "ACTIVE":
                        raise ReservationStateError(
                            "reservation_not_active"
                        )

                    principal = row[0]

                    cur.execute("""
                        UPDATE gs_agent_reservations
                        SET state='FINISHED',
                            completed_at=CURRENT_TIMESTAMP
                        WHERE request_id=%s
                          AND state='ACTIVE'
                        RETURNING request_id
                    """, (rid,))

                    if cur.fetchone() is None:
                        raise ReservationStateError(
                            "reservation_transition_failed"
                        )

                    cur.execute("""
                        UPDATE gs_agent_principals
                        SET active_requests=active_requests-1
                        WHERE principal_digest=%s
                          AND active_requests > 0
                        RETURNING active_requests
                    """, (principal,))

                    if cur.fetchone() is None:
                        raise ReservationStateError(
                            "concurrency_state_invalid"
                        )

        return True
