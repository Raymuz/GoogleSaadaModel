# Stage 8J acceptance and migration

- Only run against the Neon **dev** branch; apply a reviewed migration for `gs_email_activation_codes` (see `ActivationBase.metadata`). Do not auto-create tables in production at app startup.
- Configure `GS_ACTIVATION_PEPPER` (at least 32 random characters), `GS_HOSTINGER_API_TOKEN`, and `GS_MAIL_FROM=support@ntmcm.com` as private Space secrets. Never log activation codes.
- Registration creates an inactive native account, sends a 10-digit code, and requires activation. A failed mail send must not authenticate the account.
- Hugging Face OAuth remains independent and does NOT require a GoogleSaada email code; provider callback needs real hosted verification.
- The adapter currently sends text emails from `support@ntmcm.com`; display name is not assured.
- A complete staging security review must test registration retries, duplicate accounts, code expiry, five-attempt lockout, provider failure, CSRF, OAuth identity, sessions, and password recovery.
- Public predictions stay disabled pending native Judge and OOS calibration. No public launch is approved by source checks alone.
