# GoogleSaada V8 staging acceptance

- Verified local evidence: 13/13 historical models, 38/38 Gradio callbacks, unified HTTP 5/5, Neon dev account lifecycle, Hostinger real activation code.
- NOT YET VERIFIED: hosted Hugging Face OAuth callback, browser login/logout, hosted account flows, native Judge, OOS calibration, complete model-artifact inclusion.
- PUBLIC PREDICTIONS MUST REMAIN DISABLED.
- Secrets: DATABASE_URL (Neon production only for actual production, dev for staging), GS_ACTIVATION_PEPPER, GS_HOSTINGER_API_TOKEN, GS_MAIL_FROM, and OAuth/secret configuration. Never commit these.
- Configure HF OAuth according to the actual provider integration and callback URL; owner permissions must derive from validated provider identity.
- Source package alone is not proof that large model artifacts are available in HF staging.
