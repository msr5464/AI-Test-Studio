# Instructions for AI (Cursor / assistants working on this repo)

**Before you tell the user that any task is completed:**

1. **Run self-testing** so the task is not marked done with broken behavior.
2. **If the change touched backend/API or tests:** run the relevant test suite (e.g. `pytest tests/` or the specific test file).
3. **If the change touched the UI (customer portal, admin portal, or any frontend HTML/JS/CSS):** run the **UI self-test checklist** and only then say the task is complete.
   - **Before UI self-test:** Kill the old server and start it again so the app loads fresh code/data; wait until the server responds (e.g. curl returns 200).
   - **Checklist:** [tests/README.md](../tests/README.md#ui-self-test-checklist-run-after-any-uifrontend-change) — **UI self-test checklist** section.
   - **Scope:** Customer portal (Ask, Requirement Analysis, Generated tests, Push selected), Admin portal (TestRail Sync, Confluence Sync, documents, ChromaDB). For Confluence Sync, click the **Confluence** "Sync Now" button (id `confluenceSyncNowBtn`), not TestRail's; wait for logs in UI and runtime.

**Do not report "task complete" or "done" until you have run the appropriate self-testing (unit/integration and/or UI checklist) and confirmed it passes (or documented the failure and fixed it).**

- **You must actually run the tests / UI steps.** Do not skip self-testing and then say the task is complete.
- If you cannot run the full suite (e.g. no venv: `ModuleNotFoundError`), run what you can (e.g. `pytest tests/test_confluence_connector.py` or tests that don't need Flask) and **explicitly tell the user** what they must run or verify manually (e.g. "Run `pytest tests/` with venv activated" or "Verify in browser: Confluence Sync → click `#confluenceSyncNowBtn`, check logs") before considering the task done.

This applies to every task that modifies code or UI in this project.
