# Official Google Web VRP Scope And Rules

Date researched: 2026-06-19 Asia/Singapore. Official pages were rendered and reviewed from Google Bug Hunters on 2026-06-18 UTC.

Reader and intended action: Puppetmaster orchestrators and subagents should use this document to scope documentation-only Google web VRP prompts, route product-specific work to the right official program, and enforce safe research constraints. It is not authorization to test Google systems. Do not perform live testing against Google unless a human separately authorizes a concrete, in-scope, rules-compliant activity.

## Primary Sources

Official Google sources used:

- Google Bug Hunters homepage: https://bughunters.google.com/
- Google and Alphabet Vulnerability Reward Program Rules: https://bughunters.google.com/about/rules/google-friends/google-and-alphabet-vulnerability-reward-program-vrp-rules
- Code of Conduct for Google Vulnerability Reward Programs: https://bughunters.google.com/about/rules/other/code-of-conduct-for-our-vulnerability-reward-programs
- Cloud Vulnerability Reward Program Rules: https://bughunters.google.com/about/rules/google-friends/cloud-vulnerability-reward-program-rules
- Chrome Vulnerability Reward Program Rules: https://bughunters.google.com/about/rules/chrome-friends/chrome-vulnerability-reward-program-rules
- Android and Google Devices Security Reward Program Rules: https://bughunters.google.com/about/rules/android-friends/android-and-google-devices-security-reward-program-rules
- Google Open Source Software Vulnerability Reward Program Rules: https://bughunters.google.com/about/rules/open-source/google-open-source-software-vulnerability-reward-program-rules
- Abuse Vulnerability Reward Program Rules: https://bughunters.google.com/about/rules/google-friends/abuse-vulnerability-reward-program-rules
- AI Vulnerability Reward Program Rules: https://bughunters.google.com/about/rules/google-friends/ai-vulnerability-reward-program-rules
- Bug Hunter University, "Write down the attack scenario": https://bughunters.google.com/learn/improving-your-reports/how-to-report/write-down-the-attack-scenario
- Bug Hunter University, "Help us quickly reproduce the bug": https://bughunters.google.com/learn/improving-your-reports/how-to-report/help-us-quickly-reproduce-the-bug
- Bug Hunter University, "Verify the output of the tools": https://bughunters.google.com/learn/improving-your-reports/avoiding-mistakes/verify-the-output-of-the-tools
- Bug Hunter University, "Open redirectors": https://bughunters.google.com/learn/invalid-reports/web-platform/navigation/open-redirectors
- Bug Hunter University, "XSS in sandbox domains": https://bughunters.google.com/learn/invalid-reports/web-platform/xss/xss-in-sandbox-domains
- Bug Hunter University, "CSRF or clickjacking with no practical use to attackers": https://bughunters.google.com/learn/invalid-reports/web-platform/csrf-clickjacking/csrf-or-clickjacking-with-no-practical-use-to-attackers
- Bug Hunter University, "Understanding API key leaks": https://bughunters.google.com/learn/invalid-reports/google-products/understanding-api-key-leaks

No non-official sources were used for policy conclusions.

## What Counts As General Google Web VRP Scope

For the Google and Alphabet VRP, the relevant target set is Google-owned or Alphabet subsidiary browser extension, mobile, or web application code that handles reasonably sensitive user data. For web-facing research, treat the general web scope as:

- Virtually all content under `*.google.com`.
- Virtually all content under `*.youtube.com`.
- Virtually all content under `*.blogger.com`.
- Virtually all content under `*.deepmind.com`.
- Virtually all content under `*.waymo.com`.
- Virtually all content under `*.wing.com`.
- Google-developed and Waymo-developed Apple App Store apps, when relevant to the general VRP rules.

The program looks for design or implementation issues that substantially affect confidentiality or integrity of user data. The Google rules list common qualifying classes such as cross-site scripting, cross-site request forgery, mixed-content scripts, authentication or authorization flaws, server-side code execution, and XSLeak bugs.

Scope is not only a domain string. Agents must also consider ownership, data sensitivity, product track, acquisition timing, and whether the system is a vendor, partner, customer, sandbox, or specialized-program target.

## Explicit Exclusions And Routing

Keep these exclusions separate from the general web-facing Google VRP unless a rule page explicitly routes the issue back to the general Google and Alphabet VRP:

- Chromium and Chrome Browser bugs belong to the Chrome VRP, not the general web VRP. Chrome's page covers High/Critical Chrome Browser issues, supported Chrome release channels and platforms, Chrome reporting requirements, memory-safety categories, and Chrome-specific disclosure behavior.
- ChromeOS and Chrome Extensions have separate Chrome-family rule pages.
- Android, Pixel, Nest, Fitbit, device firmware, TEE, Secure Element, Android platform, kernel, driver, and device-stack work belongs to Android and Google Devices or Google Mobile rules. Android's page explicitly says backend Google infrastructure, backend APIs, and server-side services interacting with devices are out of scope for that device program and should be reported to the broader Google and Alphabet VRP.
- Google Cloud customer resources are not authorized test targets. Customer-owned resources include applications hosted on domains such as `*.bc.googleusercontent.com` and `*.appspot.com`. The Cloud VRP says unauthorized testing of customer resources is strictly prohibited and makes reports ineligible even if a Google-owned bug is noticed during that testing path.
- Google Cloud products and web services have their own Cloud VRP. Google Workspace products are out of scope for Cloud VRP and should route to the Google VRP instead.
- Google open source repositories, supply-chain compromises, repository settings, and third-party dependencies in Google OSS belong to the Google OSS VRP or patch reward programs, not the general web VRP.
- Abuse issues and AI-specific issues have their own Google & Friends programs. Use those pages only when the issue is abuse-specific or depends on interaction with a large language model or generative AI system.
- Third-party websites are out of scope even when Google-branded. Google says it cannot authorize testing vendor or partner systems on their owners' behalf. If ownership is unclear, stop and ask rather than testing.
- Recent acquisitions normally have a six-month blackout period before reports qualify for rewards, except where a specific program page says otherwise.

## Safe Testing Constraints

The safe default for agents is documentation-only research. Do not run scanners, probes, PoCs, exploit chains, or traffic-generating tools against Google.

If a human later authorizes real testing, the official rules still require these constraints:

- Only target accounts and data owned by the tester.
- Never attempt to access another user's data.
- Do not disrupt, damage, corrupt, alter, or degrade Google services or other users' data.
- Do not perform denial-of-service testing, broad traffic generation, black-hat SEO, spam, phishing, social engineering, physical intrusion, or attempts against Google employees.
- Do not intentionally test products or systems that are explicitly out of scope.
- If unintended access to other-user, Google customer, or sensitive Google data appears, stop testing immediately, access only the minimum needed to demonstrate impact, report immediately, and purge sensitive data from local systems.
- Keep disclosure coordinated. Google asks for reasonable advance notice before public disclosure and may deny rewards for disclosure paths that put users or Google at immediate risk.
- Maintain professional, good-faith communication. The Code of Conduct prohibits harassment, threats, impersonation, fraudulent submissions, deceptive artifacts, repeated final-decision challenges without new evidence, and attempts to bypass designated VRP channels.

## Report Quality And Reward-Impact Signals

Google's general VRP reward model considers two central technical factors:

- Domain tier: for example Tier 0, Tier 1, normal Google applications, acquisition tiers, sandboxed or lower-priority applications, or global impact through an in-scope Google product integration.
- Vulnerability category and impact: server access classes such as remote code execution or unrestricted filesystem/database access; logic flaws affecting specific information tiers or action criticality tiers; and client/session-impact classes such as XSS, CSRF, XSLeaks, clickjacking, and other state-changing actions.

Rewards are discretionary. The general web VRP table currently reaches `101,010 USD` for direct server remote code execution before quality modifiers, with examples showing higher total amounts after an exceptional-quality multiplier. The rules also describe common upgrades such as novelty bonuses and time-limited bonuses, and downgrades for minor impact, required prior access, required project access, significant user interaction, practical unexploitable conditions, Drive ID knowledge, and OAuth consent requirements.

Report quality is an explicit multiplier: low-quality reports may receive `0.8x`, good reports `1x`, and exceptional reports `1.2x`. To aim for good or exceptional quality, a report should include:

- A correct vulnerability description.
- Attack preconditions.
- Security impact analysis, or a clear statement that further impact analysis would require violating VRP rules.
- Complete, ordered reproduction steps or a minimal proof of concept where allowed.
- Target and product context such as product name, hostnames, versions, device details, and URLs when appropriate.
- Reproduction output such as relevant HTTP responses, debugger output, screenshots, or video when appropriate.
- Timely, concise, technically precise follow-up communication.

Bug Hunter University adds practical expectations:

- Write a real attack scenario: who attacks, from what starting access, against what victim, for what gain, and in what order.
- Make reproduction easy. Exact product context and concise ordered steps are more useful than broad claims.
- Verify tool output manually. Automated scanner output, AI output, header heuristics, static-analysis guesses, and copied scanner PoCs must not be submitted without confirming the actual bug and realistic impact.

## Accepted Vs Commonly Non-Qualifying Web Classes

Generally qualifying, when in scope and with real confidentiality or integrity impact:

- XSS that executes in a sensitive Google origin or otherwise accesses sensitive user data.
- CSRF, clickjacking, or state-changing action bugs with practical attacker value.
- XSLeaks with a demonstrated information leak.
- Authentication or authorization flaws, including IDOR-style bugs, that expose or alter protected user data.
- Server-side code execution, command injection, unsafe deserialization, sandbox escape, SQL injection, XXE, unrestricted filesystem access, or unrestricted database access on Google-owned services.
- Mixed-content script issues that materially affect user security.
- Redirector-related or navigation-related chains only when they create a concrete security bypass or meaningful impact beyond phishing assistance.
- Leaked keys or secrets only when they enable access to private information, another user's data, privileged actions, billing abuse, or another concrete security impact.

Commonly non-qualifying or reward-unlikely:

- Testing or reporting Google Cloud customer applications under `*.bc.googleusercontent.com`, `*.appspot.com`, or other customer-owned resources.
- XSS limited to sandbox domains such as user-content hosting domains, unless sensitive user-data impact is demonstrated.
- Owner-supplied JavaScript in Blogger or other user-controlled content surfaces where script execution is expected.
- Open redirect alone.
- Legitimate content proxying or framing without a distinct security failure.
- Bugs requiring exceedingly unlikely user interaction.
- Logout CSRF.
- Issues that affect only out-of-date browsers or plugins.
- Banner or version disclosure alone.
- User enumeration unless the report demonstrates missing protective rate limits or another meaningful impact.
- Email spoofing classes Google already treats through product defenses.
- API key exposure without a malicious-use scenario; public Google API keys are often used for quota or client identification and are not automatically secrets.
- CSRF or clickjacking that does not change account state or changes only inconsequential state.
- Reports based only on unverified scanner output, missing-header heuristics, speculative static analysis, or false assumptions about a non-existent endpoint or technology stack.

## Notes For Orchestrators And Subagents

- Do not perform live testing against Google for research-doc tasks. Use public official documentation, rendered page text, and citations.
- Prefer official Google Bug Hunters and Google Security Blog sources. Label any non-official source as secondary and do not use it to override current official rules.
- Treat current Google VRP policy as time-sensitive. Re-check official pages before each new milestone or prompt pack.
- Route product-specific findings to the right policy page before reasoning about scope or reward.
- When summarizing scope, include both domain and ownership constraints. A Google-looking domain is not enough.
- When summarizing impact, avoid payloads and exploitation instructions. Describe vulnerability classes and report expectations at a policy level.
- If any proposed research would require touching Google systems, customer resources, third-party systems, other-user data, or high-volume tooling, stop and ask the human for explicit authorization and a scoped test plan.
- If a source page and this document conflict, the official source page wins.
