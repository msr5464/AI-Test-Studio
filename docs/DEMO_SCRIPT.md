# AI Test Studio — Demo Video Script

**Target length:** 8–10 minutes  
**Recording tool:** QuickTime (Mac) · OBS · Loom · or any screen recorder  
**Audio:** Read each [VOICEOVER] block aloud, or paste into a TTS tool (e.g. ElevenLabs, Murf, Descript)  
**Browser:** http://localhost:5001 — open in full-screen, hide bookmarks bar  
**Recommended resolution:** 1920×1080, 60 fps  

---

## Pre-recording checklist

- [ ] App running: `bash scripts/run.sh`
- [ ] QA Agent Network server running: `cd QA-Agent-Network && bash scripts/run-server.sh`
- [ ] Browser at http://localhost:5001, logged in as a customer user
- [ ] Browser zoom at 100%, hide dev tools
- [ ] Have sample requirement text ready to paste (see Appendix A)
- [ ] Have a TestRail project with "Pending Automation" cases configured
- [ ] Microphone test done — quiet room, no notifications

---

## SEGMENT 1 — Introduction (0:00–0:45)

### Shot: Landing page (AI Test Generator tab visible)

**[VOICEOVER]**

> "Meet AI Test Studio — a QA workspace powered by AI that connects your requirements, your test cases, and your automation framework in one place.
>
> In this demo I'll walk you through all three features: the AI Test Generator, the Tests to Automation pipeline, and the Talk to Tests knowledge chat.
>
> Let's dive in."

**[ACTION]** Show the full customer portal. Pan slowly from the header down so viewers see the three tab labels: **Generate Tests**, **Tests → Automation**, and **Talk to Tests**.

---

## SEGMENT 2 — Tab 1: AI Test Generator (0:45–3:30)

### Shot 2a — Tab overview (0:45)

**[VOICEOVER]**

> "We'll start with the AI Test Generator. This tab turns any requirement — whether it's pasted text, an uploaded document, or a Confluence page — into a full set of test cases, with gap analysis against what you already have."

**[ACTION]** Click the **Generate Tests** tab if not already selected. Show the three input mode buttons: **Paste Text**, **Upload File**, **Confluence URL**.

---

### Shot 2b — Enter requirements (1:00)

**[VOICEOVER]**

> "I'll paste a set of requirements for a login feature. You can equally upload a PDF, Word doc, Excel file, or point it at a Confluence page — the engine handles all of those."

**[ACTION]** Click **Paste Text**. Paste the sample requirements from Appendix A into the text area. Point out the **Include P2–P3 tests** checkbox. Click **Generate Tests**.

---

### Shot 2c — Streaming progress (1:20)

**[VOICEOVER]**

> "Notice the live progress stream. The app is extracting individual requirements, then for each one it's searching the knowledge base for related existing tests using semantic similarity — not just keyword matching."

**[ACTION]** Let the SSE progress stream run. Show the per-requirement status indicators updating in real time.

---

### Shot 2d — Results: Existing Tests (1:50)

**[VOICEOVER]**

> "The first section shows related tests already in your suite. These are tests the AI found in your TestRail knowledge base that cover the same requirements. This tells you what you already have — so you don't duplicate work."

**[ACTION]** Scroll to the **Existing Tests** section. Expand one result card to show the test title, TestRail case ID, and similarity score.

---

### Shot 2e — Tests Needing Update + Update with AI (2:10)

**[VOICEOVER]**

> "The second section is more interesting — tests that *partially* cover the requirements but are outdated or incomplete. Click 'Update with AI' on any of these and the system rewrites the test steps to match your current requirements, right here in the UI."

**[ACTION]** Scroll to the **Tests Needing Update** section. Click **Update with AI** on one case. Show the rewritten test steps appearing inline.

---

### Shot 2f — New Tests (2:35)

**[VOICEOVER]**

> "The third section is the generated test cases for requirement gaps — things your suite doesn't cover yet. Each test has a priority, a clear title, and step-by-step test steps ready to use. You can select the ones you want and push them directly to TestRail with one click."

**[ACTION]** Scroll to the **New Tests** section. Show two or three generated test cases. Select them using the checkboxes. Click **Push to TestRail**. Show the success toast.

---

### Shot 2g — E2E Tests (3:05)

**[VOICEOVER]**

> "And finally, the E2E section. These are cross-requirement workflow tests — scenarios that span multiple features and simulate real user journeys. The AI also flags which existing tests might be impacted if this feature changes — that's your regression signal."

**[ACTION]** Scroll to the **E2E Tests** section. Expand one E2E test to show its multi-step scenario and the regression impact list.

---

## SEGMENT 3 — Tab 2: Tests → Automation (3:30–7:30)

### Shot 3a — Tab overview (3:30)

**[VOICEOVER]**

> "Now for the most powerful tab: Tests to Automation. This is where plain English becomes real, runnable Java test code — automatically written, run, fixed, and shipped as a GitHub pull request."

**[ACTION]** Click the **Tests → Automation** tab. Show the three sub-tabs: **Write**, **Saved Drafts**, **From TestRail**.

---

### Shot 3b — Write sub-tab (3:50)

**[VOICEOVER]**

> "The Write mode is the fastest path. Type your test steps in plain English, give the module a name, pick your platform — Web, Mobile, or API — and hit Run Agent."

**[ACTION]** Click the **Write** sub-tab. Fill in:
- Module name: `LoginModule`
- Platform: `Web`
- Test description: paste the sample steps from Appendix B

Click **Run Agent**.

---

### Shot 3c — Live streaming console — Step 1: Parse (4:15)

**[VOICEOVER]**

> "Watch the live console. Step one: the AI parses your description into a structured plan — page objects, helper classes, test methods. You can see exactly what it's planning before it writes a single line of code."

**[ACTION]** Show the **Step 1 — Parse** progress indicator going green. Scroll the console to show the structured plan output.

---

### Shot 3d — Step 2: Validate Web Selectors (4:35)

**[VOICEOVER]**

> "Step two is unique: the agent spins up a headless browser, actually navigates to your web app, and validates that every DOM selector it plans to use really exists on the page. No more automation code that compiles but fails at runtime because a locator was wrong."

**[ACTION]** Show **Step 2 — Validate Web** updating. Show selector validation output in the console — pass/fail indicators for each locator.

---

### Shot 3e — Step 3: Generate (5:00)

**[VOICEOVER]**

> "Step three: code generation. The agent writes your Page Object classes, helper methods, API enums if needed, and the test class itself — all following your team's automation framework conventions."

**[ACTION]** Show **Step 3 — Generate** completing. Scroll through a snippet of the generated Java code in the console output.

---

### Shot 3f — Step 4: Run & Fix (5:25)

**[VOICEOVER]**

> "Step four is where the magic happens. Maven runs the generated tests against your actual environment. If any test fails, the agent reads the error, rewrites the offending code, and re-runs — automatically — up to a configurable number of attempts. No manual debugging loop."

**[ACTION]** Show **Step 4 — Run & Fix**. If there are fix iterations, show the 'Attempt 2' log entry. Show Maven output and a green BUILD SUCCESS at the end.

---

### Shot 3g — Step 5: Ship (5:55)

**[VOICEOVER]**

> "Finally, step five: the agent commits the code to a new branch, pushes it, opens a GitHub pull request, and sends a Slack notification. Your team gets a PR ready to review — without a developer ever touching the test code."

**[ACTION]** Show **Step 5 — Ship** completing. Show the GitHub PR link appearing in the console. Click the PR link to briefly show the opened GitHub PR (or show a pre-recorded screenshot if live GitHub isn't available).

---

### Shot 3h — From TestRail sub-tab (6:20)

**[VOICEOVER]**

> "But what if you already have manual tests in TestRail and want to automate them? The 'From TestRail' sub-tab handles exactly that."

**[ACTION]** Click the **From TestRail** sub-tab. Show the project and suite dropdowns populating.

---

### Shot 3i — Fetch automatable cases (6:35)

**[VOICEOVER]**

> "Select your TestRail project, suite, and section. Filter by priority if you want to target P0 and P1 cases first — the ones most critical to automate. Click Fetch and the app pulls every case flagged as 'Pending Automation' from TestRail."

**[ACTION]** Select a project, suite, and P0/P1 filter. Click **Fetch Cases**. Show the list of cases appearing with color-coded priority badges.

---

### Shot 3j — Improve for Automation (6:55)

**[VOICEOVER]**

> "Manual test steps are often written for a human — vague, implied, assumption-heavy. The 'Improve for Automation' button rewrites those steps to be explicit and deterministic — the kind of precise instructions an automation agent can act on reliably."

**[ACTION]** Click **Improve for Automation** on one case with vague steps. Show the before steps and then the AI-rewritten steps side by side.

---

### Shot 3k — Add to queue and run (7:15)

**[VOICEOVER]**

> "Add the improved cases to the agent queue and hit Run Agent — the same five-step pipeline kicks off, but now seeded from your existing TestRail backlog."

**[ACTION]** Click **Add to Queue** on two or three cases. Click **Run Agent**. Show the streaming console starting up.

---

## SEGMENT 4 — Tab 3: Talk to Tests (7:30–9:00)

### Shot 4a — Tab overview (7:30)

**[VOICEOVER]**

> "The third tab is Talk to Tests — a natural-language chat interface over your entire test knowledge base. This is your QA team's collective knowledge, made searchable in plain English."

**[ACTION]** Click the **Talk to Tests** tab. Show the clean chat interface.

---

### Shot 4b — Ask a question with Internal Docs (7:45)

**[VOICEOVER]**

> "I'll ask it something a new team member might want to know — 'What test coverage do we have for the checkout flow?' With Internal Docs mode on, the answer comes from your actual test cases and documents, not from the LLM's general training."

**[ACTION]** Ensure **Internal Docs** toggle is ON. Type: *"What test coverage do we have for the checkout flow?"* Press Enter. Let the answer stream in. Show the **Sources** section below the answer — the specific documents and test cases it pulled from.

---

### Shot 4c — LLM Only mode (8:20)

**[VOICEOVER]**

> "Toggle to LLM Only mode and you can ask general testing questions — best practices, how to structure a test plan, explain a concept — without needing your docs involved. It's a general-purpose QA assistant mode."

**[ACTION]** Toggle to **LLM Only**. Type: *"What's the difference between regression testing and smoke testing?"* Show a concise answer streaming in.

---

## SEGMENT 5 — Wrap-up (9:00–9:45)

### Shot: Return to landing / overview pan

**[VOICEOVER]**

> "To summarize: AI Test Studio gives your QA team three connected superpowers.
>
> One — the AI Test Generator turns any requirement into test cases with gap analysis and direct TestRail integration.
>
> Two — the Tests to Automation pipeline takes plain-English descriptions or existing TestRail cases and delivers production-ready Java automation code, tested and shipped as a GitHub PR.
>
> Three — Talk to Tests puts your entire test knowledge base one question away.
>
> All three features share the same knowledge base — your TestRail cases, Confluence pages, and uploaded documents — so everything stays in sync.
>
> Thanks for watching. The project is open source — link in the description."

**[ACTION]** Slowly pan across all three tabs one more time, then fade to black.

---

## Appendix A — Sample requirement text (paste into Generate Tests)

```
Feature: User Login

Requirements:
1. Users must be able to log in with a valid email and password.
2. The system must display an error message for invalid credentials.
3. After 5 failed attempts the account must be locked for 15 minutes.
4. Users must be able to reset their password via email.
5. Session must expire after 30 minutes of inactivity.
6. Login page must support single sign-on (SSO) via Google and Microsoft.
```

---

## Appendix B — Sample test steps (paste into Write → automation)

```
Module: LoginModule
Platform: Web

Test: Successful login with valid credentials
Steps:
1. Navigate to the login page at /login
2. Enter a valid username in the email field
3. Enter the correct password in the password field
4. Click the Sign In button
5. Verify the user is redirected to the dashboard
6. Verify the user's name appears in the top-right navigation

Test: Login fails with invalid password
Steps:
1. Navigate to /login
2. Enter a valid email address
3. Enter an incorrect password
4. Click Sign In
5. Verify an error message 'Invalid credentials' is displayed
6. Verify the user remains on the login page
```

---

## Recording tips

- **Pace yourself** — pause 1–2 seconds after each click before speaking so edits are easy.
- **Mouse movements** — move the cursor slowly and deliberately to guide viewer attention.
- **Zoom in** for code/detail shots using browser zoom (Cmd +) then zoom back out.
- **If a step loads slowly** — fill the silence with a natural "…and you can see the results coming in" type of line rather than dead air.
- **Post-production** — chapters in YouTube/Loom can match the segment timestamps above.
