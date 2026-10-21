# Agent Supra legal review — 21 October 2026

The `/terms` and `/privacy` pages describe the current private beta. Their product and data-flow claims were checked against the code and deployment notes on 21 October 2026. This is an operational review, not a lawyer's opinion or public-launch legal sign-off.

Before inviting the general public, have a qualified adviser confirm:

- The operator's full legal name or registered entity, business address, and the support/privacy contact to publish. The current terms identify the operator only as "Sanchit".
- The terms' governing law, dispute process, eligibility, liability language, and any mandatory consumer rights for the places where users may sign up.
- Privacy disclosures, request handling, retention and backup deletion, international processing, and the applicable law's effective dates. In particular, confirm the current obligations under India's Digital Personal Data Protection Act and Rules.
- Whether any notice or consent step is needed at signup beyond the linked terms and privacy pages.

Do not claim that this checklist alone makes the pages legally compliant. Keep the private-beta wording until these decisions are made.

Official sources for that review: India's [Digital Personal Data Protection Act, 2023](https://www.meity.gov.in/static/uploads/2024/02/Digital-Personal-Data-Protection-Act-2023.pdf), the [2025 Rules and enforcement timeline](https://www.meity.gov.in/documents/act-and-policies/digital-personal-data-protection-rules-2025-gDOxUjMtQWa?pageTitle=Digit), and the [commencement notification](https://www.meity.gov.in/static/uploads/2025/11/c56ceae6c383460ca69577428d36828b.pdf). The Act and Rules phase in different provisions; review the dates applicable to the launch rather than assuming that every provision is already in force.

## Findings and actions, in priority order

1. **Contracting identity is incomplete.** `/terms` names only “Sanchit”. Confirm whether the operator is an individual or registered entity, then add its full legal name, appropriate business/service address, and public support/privacy contact. Do not infer the legal entity from GitHub, domain registration, or the Orrico Labs Slack workspace.
2. **Audience and jurisdictions are undecided.** Public signup has no geographic or age restriction. Counsel should decide the intended countries and eligibility, then review applicable consumer, privacy, and child-data duties. Draft governing-law, dispute, and liability language only after that choice. A free beta is not automatically exempt.
3. **Signup acceptance needs review and version evidence.** The form now links the private-beta terms and privacy page beside Create account, but does not store the terms version a user accepted. Counsel should specify the appropriate notice/consent flow for the chosen audience. Do not treat a generic “I consent to the privacy policy” checkbox as a substitute for a clear processing notice.
4. **Privacy-request operations need an owner.** Users can delete an account from Settings or email the operator. There is no self-service export/correction flow or documented procedure for requests involving retained backups and copies already sent to Slack/email. Assign an owner and response process for access, correction, withdrawal, complaints, incidents, and backup expiry before promising response times.
5. **Processor and transfer review remains.** Check Vercel, Neon, GitHub Actions, Resend, Slack, and Google Fonts terms, account configurations, regional processing, and cross-border transfer conditions. A Vercel Singapore function region does not establish all providers' storage locations. Decide whether to keep the external Google Fonts request or use a local/system font.
6. **Operational promises need supporting procedures.** The terms promise advance notice of material changes. Establish how those notices will be sent. Confirm the daily active-data cleanup, the lifetime of Neon snapshots and restored branches, and whether provider request logs outlive app records.

## Product evidence

- `agent_supra/server/templates/auth.html` shows email/password signup with near-submit terms and privacy links; `base.html` also has footer links. `web.py` permits public signup when `ALLOW_SIGNUP` is true, its current default. There is no stored acceptance-version record.
- `agent_supra/server/db.py` stores email, salted password hash, key metadata/hash, session metadata/hash, workspace settings including a Slack webhook, task/run/event details, and error records. `accounts.py` removes active workspace data on deletion.
- `app.py` prunes run/error records; `vercel.json` schedules daily pruning. `.github/workflows/database-backup.yml` retains encrypted artifacts for 30 days. Neon snapshots and separately restored branches can last longer.
- `base.html` loads Inter from Google Fonts. `alerts.py` delivers run alerts to Slack/email, creating copies outside the active database. `sdk.py` uses keyed tool fingerprints and does not automatically transmit prompts, replies, or source code, although user-supplied names, IDs, and notes can contain sensitive content.

For jurisdictions beyond India, review the EU's [General Data Protection Regulation](https://eur-lex.europa.eu/eli/reg/2016/679) if the service is offered to or monitors people in the EU. The updated privacy page now discloses hashed secret values separately from retained metadata, functional cookies, Google Fonts requests, and downstream alert copies.

**Release gate:** operational copy review is complete. Public-launch legal approval remains open until operator identity, target jurisdictions, signup notice, request handling, and final legal text are approved. Beta feedback is a separate product gate.
