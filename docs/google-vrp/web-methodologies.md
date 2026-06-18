# Google Web VRP Methodologies

Date researched: 2026-06-19 +08.

Reader: Puppetmaster prompt authors and security-review agents working on
authorized, defensive Google web VRP research briefs.

Post-read action: choose high-signal Google web vulnerability hypotheses, assign
safe subagent work, and produce evidence-focused artifacts without live Google
testing, noisy scanning, destructive payloads, credential theft, or out-of-scope
product programs. This document excludes Chromium, Android, kernel/driver, and
product-specific non-web programs.

## Source Set

Primary Google sources:

- Google and Alphabet VRP rules:
  https://bughunters.google.com/about/rules/google-friends/google-and-alphabet-vulnerability-reward-program-vrp-rules
- Bug Hunters rules overview:
  https://bughunters.google.com/about/rules/about-this-section
- Google Bug Hunters report quality guidance:
  https://bughunters.google.com/learn/improving-your-reports/about-this-section
- Google Bug Hunters automated PoC guidance:
  https://bughunters.google.com/learn/improving-your-reports/how-to-report/create-an-exceptional-automated-poc
- Google Bug Hunters invalid report guidance:
  https://bughunters.google.com/learn/invalid-reports/about-this-section
- Google Bug Hunters invalid report examples:
  https://bughunters.google.com/learn/invalid-reports/web-platform/navigation/open-redirectors
  https://bughunters.google.com/learn/invalid-reports/network-protocols/invalid-spf-policy-and-e-mail-spoofing-issues
  https://bughunters.google.com/learn/invalid-reports/google-products/csv-formula-injection
- Google Bug Hunters public domain tiers:
  https://github.com/google/bughunters/tree/main/domain-tiers
- Google Cloud VRP launch guidance:
  https://cloud.google.com/blog/products/identity-security/google-cloud-launches-new-vulnerability-rewards-program
- Google VRP 2025 year in review:
  https://blog.google/security/vrp-2025-year-in-review/
- Google Identity OAuth for web server applications:
  https://developers.google.com/identity/protocols/oauth2/web-server
- Google Sign-In with OpenID Connect:
  https://developers.google.com/identity/openid-connect/openid-connect

Representative disclosed reports and writeups:

- OAuth/postMessage target-origin issue:
  https://bughunters.google.com/reports/vrp/wG2bN8vZr
- Search Console low-privilege export authorization issue:
  https://bughunters.google.com/reports/vrp/5XZmpiNfg
- Query users by email information disclosure:
  https://bughunters.google.com/reports/vrp/TiNeBTPio/report
- Cross-tenant Application Design Center compromise:
  https://bughunters.google.com/reports/vrp/HRDqQWSLs
- Unauthorized overwrite of user-shared images:
  https://bughunters.google.com/reports/vrp/ywk7nssKg/report
- Auth bypass via unverified Firebase ID token:
  https://bughunters.google.com/reports/vrp/mjyKuWrfd/comments
- Google Support customer data exposure writeup:
  https://michaeldalton.au/posts/hacking-google-support
- Awesome Google VRP writeups index:
  https://github.com/xdavidhu/awesome-google-vrp-writeups

General defensive references:

- OWASP API1:2023 Broken Object Level Authorization:
  https://owasp.org/API-Security/editions/2023/en/0xa1-broken-object-level-authorization/
- OWASP WSTG OAuth testing:
  https://owasp.org/www-project-web-security-testing-guide/latest/4-Web_Application_Security_Testing/05-Authorization_Testing/05-Testing_for_OAuth_Weaknesses
- OWASP WSTG SSRF testing:
  https://owasp.org/www-project-web-security-testing-guide/latest/4-Web_Application_Security_Testing/07-Input_Validation_Testing/19-Testing_for_Server-Side_Request_Forgery
- OWASP XSS prevention:
  https://cheatsheetseries.owasp.org/cheatsheets/Cross_Site_Scripting_Prevention_Cheat_Sheet.html
- OWASP CSRF prevention:
  https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html
- OWASP clickjacking defense:
  https://cheatsheetseries.owasp.org/cheatsheets/Clickjacking_Defense_Cheat_Sheet.html

## Operating Guardrails

Use this document for prompt research, public writeup analysis, local artifact
review, and safe hypothesis generation only. Do not perform live testing against
Google from this workflow.

Agents should not create mass-scanning plans, payload lists, exploit chains that
steal credentials, destructive proof of concepts, denial-of-service procedures,
or instructions for bypassing authorization outside an explicitly authorized
test account setup. For live VRP work, the researcher must follow the current
Google Bug Hunters rules, the selected program scope, and the report form
instructions directly.

The highest-value prompt posture is "impact-first and boundary-aware":
identify the user, tenant, account, project, Workspace domain, or support/admin
boundary; explain the attacker's starting privileges; and only then choose a
test hypothesis.

## Methodology Categories

### Asset Intake And Scope Triage

Start with Google-controlled scope and sensitivity, not with a broad domain list.
The public domain tiers explain that Google classifies many web application
domains by sensitivity, with tier 0 being highest sensitivity and tier 4 covering
sandboxed, third-party, or user-controlled content. The domain-tier README also
states that TIER4 domains are not in scope for Google VRP and that parent-domain
classification may apply to Google subdomains.

Prompt rules:

- Record the program candidate: Google and Alphabet VRP, Cloud VRP, Abuse VRP,
  AI VRP, OSS VRP, or another program. For this document, keep only Google and
  Alphabet web-facing targets plus web-relevant Cloud/Workspace surfaces.
- Prefer targets that handle sensitive user data, admin actions, support data,
  OAuth flows, Workspace content, project/tenant boundaries, or account linking.
- Treat acquisition/Bets domains and Google domains differently because the tier
  inheritance model differs.
- Exclude TIER4, user-content sandbox hosts, static marketing pages, and targets
  with no plausible sensitive-data or integrity impact unless a chain changes
  that conclusion.
- Produce an asset-intake artifact with target URL, program candidate, tier
  evidence, authentication requirements, data classes, and known safe test
  accounts needed.

### Account, Identity, Session, OAuth, And OIDC Boundaries

Google web targets often cross product, identity, and iframe boundaries. Public
reports show high-value issues around loose `postMessage` target-origin checks,
unverified Firebase ID tokens, OAuth authorization-code exposure, and support
systems that bridge user-facing and internal tooling.

Prompt rules:

- Map the identity parties: Google account, Workspace account, service account,
  external IdP, support agent, admin, project owner, viewer, and anonymous user.
- Identify where auth state is stored and transferred: cookies, ID tokens,
  access tokens, authorization codes, `state`, `nonce`, redirect URIs, iframe
  messages, deep links, and backend session IDs.
- Check the expected validation contract, not payload tricks: exact origin
  matching, exact redirect URI matching, token audience/issuer/signature
  validation, CSRF state binding, nonce binding, scope minimization, token
  lifetime, and revocation behavior.
- For OAuth/OIDC hypotheses, focus on data-flow evidence: who initiates the
  flow, where the code or token is delivered, which origin receives messages,
  and whether a lower-privilege party can influence a higher-privilege callback.
- Favor local/static analysis and public disclosed patterns. Do not attempt to
  capture real user credentials or tokens.

Useful artifacts:

- Identity boundary map.
- OAuth/OIDC flow table with initiator, redirect URI, state/nonce handling,
  token audience, token sink, and trust checks.
- Session transition table for account switching, Workspace tenant switching,
  and delegated admin flows.

### Authorization, IDOR, And Function-Level Access

OWASP API1:2023 frames broken object-level authorization as an endpoint accepting
an object identifier and failing to verify that the current user may act on that
object. Google disclosed reports show this remains high-signal when it crosses
meaningful object, user, project, or admin boundaries: examples include low
privilege Search Console exports, querying user data by email, removing or
modifying shared resources, and accessing private uploads.

Prompt rules:

- Compare roles, not just users: anonymous, signed-in consumer, resource owner,
  viewer, commenter, editor, Workspace member, Workspace admin, support agent,
  Cloud project viewer, project editor, and organization admin.
- Inventory object identifiers visible in URLs, GraphQL variables, REST paths,
  JSON bodies, batch APIs, export jobs, upload IDs, document IDs, organization
  IDs, tenant slugs, and email addresses.
- Ask what the server should know independently of the client. Client-side UI
  gating is weak evidence unless a backend request also enforces the boundary.
- Prioritize read/write/delete/export operations and asynchronous jobs because
  they often cross from UI permissions into backend service permissions.
- Document starting permission and ending impact. For Cloud-like surfaces,
  express the "privilege delta": exact role before, exact data or control after.

Useful artifacts:

- Role and object matrix.
- Endpoint authorization table.
- Minimal safe reproduction narrative using researcher-owned or synthetic
  resources only.

### SSRF, Metadata, And Server-Side Data Access Boundaries

SSRF is high value only when the server-side request crosses into sensitive
networks, metadata services, privileged APIs, or trusted backend integrations.
Public Google VRP history includes SSRF and metadata-related findings, but
Google also receives many low-impact reports. The safe methodology is to reason
about request sinks and trust boundaries without probing internal hosts.

Prompt rules:

- Identify server-side fetch features: URL preview, import, webhook, connector,
  feed reader, image proxy, document conversion, API integration, package/repo
  mirror, and cloud resource validation.
- Ask what credentials the server might attach: service account, customer token,
  metadata identity, internal cookies, allowlisted network source, or signed
  request.
- Focus on boundary proof: can a user-controlled URL influence a privileged
  server request, and what sensitive system could be reached in principle?
- Avoid internal IP payload inventories, cloud metadata request payloads,
  blind-scanning setups, or destructive callbacks in prompts.
- Prefer static evidence, product documentation, response-difference reasoning,
  and researcher-controlled benign endpoints if live testing is separately
  authorized.

Useful artifacts:

- SSRF sink inventory.
- Trust-boundary diagram with caller identity and outbound network zone.
- Safe validation plan that proves server-side fetch behavior without accessing
  internal resources.

### XSS With Meaningful Google Impact

Generic reflected XSS on a low-sensitivity acquisition or marketing domain is
often low value. XSS becomes high quality when it can cross a sensitive Google
boundary: account actions, OAuth code/token handling, Workspace content,
privileged admin UI, support data, trusted `postMessage`, extension/app bridges,
or same-origin access to sensitive APIs.

Prompt rules:

- Classify the origin and tier before classifying the bug.
- Identify reachable sensitive APIs from the vulnerable origin.
- Check whether cookies are protected and whether the origin has access to
  session-bearing APIs, OAuth callbacks, document data, admin actions, or
  cross-window messaging.
- For DOM and client-side flows, map sources, sinks, sanitizers, Trusted Types,
  CSP, framework escaping, iframe sandboxing, and `postMessage` origin checks.
- Do not include payload bypass lists. Prompts should ask agents to find
  untrusted-data paths and impact, not to spray payloads.

Useful artifacts:

- Source-to-sink trace.
- Same-origin impact matrix.
- Message-channel trust table.

### CSRF And Clickjacking Where Still Meaningful

Modern browser defaults and Google defenses make many bare CSRF and
clickjacking reports low value. They become meaningful when the action has high
security impact, the victim interaction is realistic, and the attacker can cause
state change or data disclosure despite modern controls. A 2025 public Google
Docs clickjacking report is a useful reminder that UI redress can matter when it
leaks Workspace data or drives sensitive document actions.

Prompt rules:

- Reject "missing header only" findings unless a concrete, sensitive user action
  or data exposure is demonstrated.
- For CSRF, require state-changing impact, authentication context, missing or
  bypassed origin/token checks, and a browser condition that still sends
  credentials.
- For clickjacking, require a believable UI redress path, target action, victim
  role, and sensitive data or integrity impact.
- Do not build deceptive live pages or social-engineering material in this
  workflow.

Useful artifacts:

- Sensitive-action inventory.
- Browser-control table: SameSite, Origin/Referer checks, CSRF token,
  `X-Frame-Options`, CSP `frame-ancestors`, user activation, and reauth prompts.

### File Upload And Content Handling

File and content features are strong candidates when they cross parsing,
storage, serving, sharing, or ownership boundaries. Public reports include
unauthorized overwrite of shared images, private upload IDORs, path traversal,
Zip Slip, EXIF leakage, and content transformation issues.

Prompt rules:

- Track the lifecycle: upload, validation, transformation, storage, serving,
  sharing, overwrite/delete, export, and retention.
- Identify ownership checks at each transition, especially when a public URL,
  upload ID, object key, or processing job references user-controlled content.
- Look for content-type confusion, path traversal, archive extraction,
  metadata retention, public/private ACL mismatch, and overwrite semantics.
- Keep validation benign: use harmless test files and synthetic metadata in
  authorized environments only.

Useful artifacts:

- Content lifecycle diagram.
- Ownership and ACL table.
- Parser/transformer risk list.

### API, Schema, GraphQL, Batch, And Async Behavior

Google web products often expose rich APIs behind compact UIs. High-value bugs
come from mismatches between UI permissions and backend API semantics, especially
batch operations, export jobs, GraphQL mutations, hidden fields, and legacy API
versions.

Prompt rules:

- Inventory API surfaces observed in public JavaScript, docs, disclosed reports,
  or local artifacts. Avoid brute forcing.
- Compare read, write, delete, export, and admin verbs across roles.
- Look for mass assignment, excessive data exposure, object property-level
  authorization, batch operation privilege gaps, race-prone approval flows, and
  stale/legacy endpoint behavior.
- Treat asynchronous jobs as separate principals: who creates the job, who owns
  it, whose data it reads, who can stop it, and where output is delivered.

Useful artifacts:

- API surface inventory.
- Schema/field sensitivity map.
- Async job ownership table.

### Multi-Tenant Data Exposure

Multi-tenant findings usually outrank single-user issues because they cross an
organization, Workspace domain, Cloud project, customer, support-case, or
application-space boundary. Public reports include cross-tenant Cloud/ADC
compromise, Google Support customer data exposure, Search Console permission
gaps, and support/chat transcript exposure.

Prompt rules:

- Name the tenant boundary and the tenant identifier.
- Specify whether the attacker has no relationship to the victim tenant, has a
  low-privilege role, or controls an adjacent resource.
- Track indirect references: bucket names, email addresses, organization IDs,
  shared space IDs, case IDs, export job IDs, support chat IDs, and document IDs.
- Prefer hypotheses where the attacker starts with low privilege and ends with
  data or control from another tenant.

Useful artifacts:

- Tenant boundary map.
- Cross-tenant object reference table.
- Impact statement listing affected data classes.

### Business Logic And Workflow Abuse

Business logic bugs are strongest when the report explains why the product's
security invariant is broken, not just that a surprising UI path exists. Look at
approval flows, entitlement checks, account linking, invite acceptance, trial or
student eligibility, support escalation, export cancellation, and resource
sharing.

Prompt rules:

- State the intended invariant: "only an owner can export", "only an admin can
  bind a tenant", "only the invited account can accept", "only verified users can
  reach this benefit".
- Compare UI restrictions, backend enforcement, and asynchronous side effects.
- Check account switching, stale invites, pending approvals, email aliases,
  group membership changes, and partially completed onboarding.
- Avoid fraud instructions. Frame findings as integrity or authorization
  failures with controlled accounts.

Useful artifacts:

- Workflow state machine.
- Invariant checklist.
- Role/transition matrix.

### Workspace And Admin Boundaries

Workspace, Docs, Drive, Gmail, Calendar, Groups, Search Console, admin consoles,
and support/admin tooling are sensitive because users and organizations rely on
fine-grained sharing and delegation. High-quality hypotheses target document
sharing, editor/viewer differences, domain admin delegation, group membership,
export/import, add-ons, support flows, and account-linking boundaries.

Prompt rules:

- Always separate consumer Google accounts, Workspace users, Workspace admins,
  external collaborators, groups, service accounts, support agents, and product
  admins.
- Track resource scope: file, folder, drive, group, calendar, property, domain,
  project, organization, and support case.
- Prioritize bugs where a lower role gets data/control reserved for a higher
  role or another organization.
- Report evidence should use researcher-controlled domains/accounts where live
  authorization exists.

Useful artifacts:

- Workspace role/resource matrix.
- Sharing and delegation graph.
- Admin action audit table.

## Usually Low-Value Or Non-Qualifying Unless Chained

These classes are usually poor standalone Google web VRP hypotheses unless they
are chained to meaningful confidentiality or integrity impact:

- Open redirectors used only for phishing or generic reputation abuse.
- Missing SPF, DKIM, or email-spoofing observations without a concrete product
  security boundary impact.
- CSV formula injection where the only impact depends on a user opening a
  downloaded spreadsheet and approving risky behavior.
- Missing clickjacking headers with no sensitive action or realistic data
  exposure.
- CSRF on logout, preference changes, or low-impact settings.
- Version banners, server headers, stack traces, path disclosure, or commit hash
  disclosure with no exploit path.
- Self-XSS, browser console paste attacks, or attacks contained to a single
  user tricking themselves.
- Generic rate limiting, username enumeration, or public profile/email mapping
  that Google documents as intended or low impact.
- Low-sensitivity marketing or static content XSS with no session, API, OAuth,
  Workspace, or admin impact.
- Reports that only assert "AI said it is vulnerable" without reproducible
  technical evidence.
- Scanner output without manual validation, impact analysis, or clear scope.

## Prioritizing Hypotheses Without Noisy Scanning

Use a scoring pass before any testing plan:

1. Scope confidence: current Google Bug Hunters program page, domain tier, and
   product ownership are clear.
2. Boundary value: the hypothesis crosses user, tenant, Workspace, project,
   admin, support, OAuth, or data-class boundaries.
3. Attacker starting position: anonymous or low-privilege starting access is
   better than owner-only or self-only behavior.
4. Ending impact: unauthorized data read, data modification, permission change,
   token/code exposure, admin action, or cross-tenant control is concrete.
5. Evidence path: there is a safe way to show the boundary failure with
   researcher-owned accounts, public docs, local/static analysis, or disclosed
   report patterns.
6. Novelty: the hypothesis is not just a known invalid report class or a
   generic scanner finding.
7. Minimality: the validation path requires the fewest requests and no brute
   force, fuzzing, crawling at scale, or destructive side effects.

Agent prompts should ask for ranked hypotheses, not target dumps. A good output
is "top five account-boundary hypotheses with evidence needed and stop
conditions"; a bad output is "all subdomains and payloads to try".

## Safe Validation Standards

For this repository's research workflow, validation means proving that a report
would be worth manual authorized testing later. It does not mean testing Google
live.

Minimum standards:

- Cite current scope and source URLs.
- Identify attacker, victim, prerequisites, and affected data/control.
- Use only public sources, local artifacts, researcher-owned sample data, or
  already disclosed reports.
- Avoid accessing, modifying, exporting, or deleting third-party data.
- Avoid service disruption, stress testing, rate-limit probing, brute force,
  credential capture, or malware-like behavior.
- Avoid payload details that enable unauthorized exploitation.
- Define stop conditions: any sign of real user data, internal-only systems,
  destructive side effects, or uncertain scope stops the test and requires
  program-rule review.
- Preserve auditability: timestamps, source URLs, exact observed behavior,
  screenshots only of owned/synthetic data, request/response redaction, and a
  clear statement of what was not tested.

## Report Evidence Standards

Google's public guidance and Cloud VRP launch notes emphasize clear affected
components, reproducible steps, detailed attack scenarios, attacker starting
position, victim assumptions, and what the attacker gains.

An agent-ready report evidence packet should contain:

- One-sentence summary naming the violated boundary.
- Program and scope evidence.
- Affected component and URL pattern.
- Attacker starting role and victim role.
- Preconditions and explicit assumptions.
- Minimal reproduction using owned/synthetic resources.
- Expected versus observed security decision.
- Impact statement tied to data class, tenant, account, project, or admin
  control.
- Safety statement: no third-party data accessed, no destructive actions, no
  mass scanning.
- Redacted evidence: screenshots, HAR excerpts, request IDs, logs, or flow
  diagrams as appropriate.
- Suggested severity rationale and why common invalid-report categories do not
  apply.

## Suggested Subagent Roles And Outputs

Asset scope analyst:

- Inputs: candidate URLs, public domain tiers, Google Bug Hunters rules.
- Output: `asset-intake.md` with program, tier, ownership, sensitivity, auth
  requirements, exclusions, and source URLs.

Identity and OAuth mapper:

- Inputs: public docs, client-side code snapshots if available, disclosed OAuth
  reports.
- Output: `identity-flow-map.md` with actors, redirect URIs, message channels,
  state/nonce handling, token sinks, and trust checks.

Authorization matrix analyst:

- Inputs: UI/API observations from allowed artifacts, role definitions,
  disclosed IDOR/access-control patterns.
- Output: `authorization-matrix.md` with roles, objects, operations, expected
  checks, risky gaps, and safe validation ideas.

API and schema reviewer:

- Inputs: public JS, public API docs, GraphQL/schema snippets from allowed
  artifacts.
- Output: `api-surface.md` with endpoints, object IDs, sensitive fields, async
  jobs, batch operations, and authorization hypotheses.

Workspace/admin boundary analyst:

- Inputs: Workspace/admin product docs, sharing model notes, public reports.
- Output: `workspace-boundaries.md` with role/resource graph, sharing and
  delegation assumptions, admin-only actions, and cross-domain risks.

Content handling analyst:

- Inputs: upload/export/conversion features from public docs or allowed
  artifacts.
- Output: `content-lifecycle.md` with ownership checks, ACL transitions,
  parser/transformer risks, and safe synthetic-file validation plan.

Invalid-class filter:

- Inputs: ranked hypotheses and Google invalid-report guidance.
- Output: `qualification-filter.md` marking likely non-qualifying items,
  required chains, and evidence needed to raise impact.

Report evidence editor:

- Inputs: validated hypothesis packet.
- Output: `report-evidence.md` with concise summary, reproduction narrative,
  impact, safety statement, redactions, and open questions.

## Prompt Encoding Checklist

Every Google web VRP research prompt should include:

- "Do not perform live testing against Google unless explicitly authorized in a
  separate task."
- "Do not mass scan, fuzz at scale, brute force, stress test, steal
  credentials, or access third-party data."
- "Start from scope, program, tier, and boundary mapping."
- "Rank hypotheses by attacker starting privilege, victim boundary, ending
  impact, and safe evidence path."
- "Prefer account/tenant/OAuth/authorization/data-exposure hypotheses over
  generic scanner classes."
- "Filter low-value classes unless chained to concrete Google user, Workspace,
  Cloud, support, admin, or OAuth impact."
- "Produce artifacts, not payload lists."
- "Stop on unclear scope, real user data, destructive side effects, or
  unauthorized access."
