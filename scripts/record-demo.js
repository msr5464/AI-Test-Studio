/**
 * AI Test Studio — Demo Video Recorder (v2)
 * One focused action per tab, waits for full completion before moving on.
 *
 * Prerequisites:
 *   - App running:               bash scripts/run.sh
 *   - QA Agent Network running:  cd QA-Agent-Network && bash scripts/run-server.sh
 *   - Node.js + Playwright:      npm install -g playwright && npx playwright install chromium
 *   - ffmpeg installed:          brew install ffmpeg
 *
 * Usage:
 *   NODE_PATH=/usr/local/lib/node_modules node scripts/record-demo.js
 *
 * Output:
 *   /tmp/ait-demo-final.mp4
 */

const { chromium } = require('playwright');
const { execSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const BASE_URL   = 'http://localhost:5001';
const VIDEO_DIR  = '/tmp/ait-demo-v2';
const OUTPUT_MP4 = '/tmp/ait-demo-final.mp4';
const W = 1440, H = 880;

const USERNAME = 'admin';
const PASSWORD = 'admin123';

// ── Helpers ───────────────────────────────────────────────────────────────────
const sleep = ms => new Promise(r => setTimeout(r, ms));

/** Full-screen title card with fade-in/out */
async function showTitle(page, heading, sub = '') {
  await page.evaluate(({ heading, sub }) => {
    document.getElementById('__demo_title')?.remove();
    const el = document.createElement('div');
    el.id = '__demo_title';
    el.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(10,20,40,0.88);'
      + 'display:flex;flex-direction:column;align-items:center;justify-content:center;'
      + 'z-index:99999;pointer-events:none;opacity:0;transition:opacity 0.5s;';
    el.innerHTML = `
      <div style="text-align:center;padding:40px 60px;max-width:900px;">
        <div style="font-size:44px;font-weight:800;color:#fff;letter-spacing:-1px;line-height:1.2;margin-bottom:18px;">${heading}</div>
        ${sub ? `<div style="font-size:21px;color:#94a3b8;font-weight:400;line-height:1.5;">${sub}</div>` : ''}
      </div>`;
    document.body.appendChild(el);
    requestAnimationFrame(() => el.style.opacity = '1');
  }, { heading, sub });
  await sleep(2800);
  await page.evaluate(() => {
    const el = document.getElementById('__demo_title');
    if (el) { el.style.opacity = '0'; setTimeout(() => el.remove(), 600); }
  });
  await sleep(700);
}

/** Golden glow around an element to guide viewer attention */
async function glow(page, selector, ms = 1400) {
  await page.evaluate(s => {
    const el = document.querySelector(s);
    if (!el) return;
    el.style.transition = 'box-shadow 0.3s';
    el.style.boxShadow = '0 0 0 4px #f59e0b, 0 0 28px rgba(245,158,11,0.5)';
  }, selector);
  await sleep(ms);
  await page.evaluate(s => {
    const el = document.querySelector(s);
    if (el) { el.style.boxShadow = ''; }
  }, selector);
  await sleep(200);
}

/** Smooth scroll an element to the top of the viewport */
async function scrollTo(page, selector) {
  await page.evaluate(s => {
    const el = document.querySelector(s);
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, selector);
  await sleep(900);
}

/** Scroll the page by a pixel amount */
async function scrollBy(page, dy) {
  await page.evaluate(y => window.scrollBy({ top: y, behavior: 'smooth' }), dy);
  await sleep(900);
}

/** Type character-by-character for a realistic feel */
async function typeSlowly(page, selector, text, delay = 22) {
  await page.click(selector);
  await page.fill(selector, '');
  await page.type(selector, text, { delay });
}

/** Click a sidebar nav item */
async function navTo(page, tab) {
  await page.click(`.nav-item[data-nav="${tab}"]`);
  await sleep(900);
}

// ── Wait helpers ──────────────────────────────────────────────────────────────

/** Wait until the Generate button is re-enabled — the reliable "analysis done" signal */
async function waitForAnalysisDone(page, timeoutMs = 180000) {
  console.log('  ⏳  Waiting for analysis to complete …');
  // First make sure the button has gone disabled (analysis actually started)
  await page.waitForFunction(
    () => { const b = document.getElementById('reqAnalyzeBtn'); return b && b.disabled; },
    null, { timeout: 15000 }
  ).catch(() => {}); // ignore if it was instant
  // Now wait for it to come back enabled — that means the stream finished
  await page.waitForFunction(
    () => {
      const b = document.getElementById('reqAnalyzeBtn');
      return b && !b.disabled && (b.textContent || '').includes('Generate');
    },
    null, { timeout: timeoutMs }
  );
  console.log('  ✓  Analysis done');
  await sleep(1200);
}

/** Wait until qaLiveStatus shows a terminal state (completed / failed / error / shipped) */
async function waitForAgentDone(page, timeoutMs = 600000) {
  console.log('  ⏳  Waiting for agent to finish (up to 10 min) …');
  await page.waitForFunction(
    () => {
      const el = document.getElementById('qaLiveStatus');
      if (!el) return false;
      const t = el.textContent.trim().toLowerCase();
      return t === 'completed' || t === 'failed' || t === 'error' || t.includes('done') || t.includes('shipped');
    },
    null, { timeout: timeoutMs }
  );
  const status = await page.$eval('#qaLiveStatus', el => el.textContent.trim());
  console.log('  ✓  Agent finished — status:', status);
  await sleep(1200);
}

/** Wait until #askResults has a second message with real content (the answer) */
async function waitForAnswer(page, timeoutMs = 60000) {
  console.log('  ⏳  Waiting for answer …');
  await page.waitForFunction(
    () => {
      const container = document.getElementById('askResults');
      if (!container) return false;
      const msgs = container.querySelectorAll('.chat-message');
      if (msgs.length < 2) return false;
      const last = msgs[msgs.length - 1];
      return last.textContent.trim().length > 80;
    },
    null, { timeout: timeoutMs }
  );
  console.log('  ✓  Answer received');
  // Extra wait so streaming finishes fully before we scroll
  await sleep(4000);
}

// ── Main ──────────────────────────────────────────────────────────────────────
(async () => {
  if (fs.existsSync(VIDEO_DIR)) {
    fs.readdirSync(VIDEO_DIR).forEach(f => fs.unlinkSync(path.join(VIDEO_DIR, f)));
  } else {
    fs.mkdirSync(VIDEO_DIR, { recursive: true });
  }

  console.log('🎬  Launching Chromium …');
  const browser = await chromium.launch({
    headless: false,
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
    slowMo: 30,
  });

  const context = await browser.newContext({
    viewport: { width: W, height: H },
    recordVideo: { dir: VIDEO_DIR, size: { width: W, height: H } },
  });
  const page = await context.newPage();

  // ── Login via API then navigate ────────────────────────────────────────────
  console.log('→ Logging in …');
  await page.goto(BASE_URL, { waitUntil: 'domcontentloaded' });
  await sleep(800);
  try {
    await page.request.post(`${BASE_URL}/api/auth/login`, {
      data: { username: USERNAME, password: PASSWORD },
    });
  } catch (_) {}
  await page.goto(BASE_URL, { waitUntil: 'networkidle' });
  await sleep(1500);

  // ══════════════════════════════════════════════════════════════════════════
  // INTRO
  // ══════════════════════════════════════════════════════════════════════════
  console.log('\n── INTRO ──');
  await page.evaluate(() => window.scrollTo({ top: 0 }));
  await sleep(800);

  await showTitle(page, 'AI Test Studio',
    'Turn requirements into tests · Automate test authoring · Chat with your knowledge base');

  // Pan the sidebar so viewers see all 3 nav items
  await glow(page, '.nav-item[data-nav="analyze"]', 900);
  await glow(page, '.nav-item[data-nav="agents"]',  900);
  await glow(page, '.nav-item[data-nav="ask"]',     900);
  await sleep(1000);

  // ══════════════════════════════════════════════════════════════════════════
  // TAB 1 — AI Test Generator
  // ══════════════════════════════════════════════════════════════════════════
  console.log('\n── TAB 1: AI Test Generator ──');
  await navTo(page, 'analyze');
  await showTitle(page, '① AI Test Generator',
    'Paste a requirement → AI analyses coverage and generates test cases');

  // Show the 3 input modes briefly
  await glow(page, '.analyze-tab[data-pane="paste"]', 700);
  await glow(page, '.analyze-tab[data-pane="file"]',  700);
  await glow(page, '.analyze-tab[data-pane="url"]',   700);
  await sleep(600);

  // Select Paste mode and type ONE focused requirement
  await page.click('.analyze-tab[data-pane="paste"]');
  await sleep(400);
  await glow(page, '#reqSpecText', 600);

  console.log('  → Typing requirement …');
  await typeSlowly(page, '#reqSpecText',
`REQ-001: Users must be able to log in with a valid email and password.
           Failed login attempts should show a clear error message.
           After 5 consecutive failures the account must be locked for 15 minutes.`, 24);

  await sleep(1000);

  // Show the Generate button, then click it
  await glow(page, '#reqAnalyzeBtn', 1200);
  await page.click('#reqAnalyzeBtn');
  await sleep(800);

  // Show the progress section while it runs
  try {
    await page.waitForSelector('#reqProgress', { state: 'visible', timeout: 8000 });
    await scrollTo(page, '#reqProgress');
    console.log('  → Progress visible — watching stages …');
    await sleep(2000);
  } catch (_) {}

  // *** WAIT for full completion before touching anything ***
  await waitForAnalysisDone(page);

  // Results are in — scroll up smoothly to show the summary bar
  await scrollTo(page, '#reqResults');
  await sleep(1200);

  // ── Related Tests tab ──
  console.log('  → Showing Related Tests …');
  await glow(page, '#reqTabRelated', 700);
  await sleep(500);
  await scrollTo(page, '#reqResultPaneRelated');
  await sleep(1000);
  for (let i = 0; i < 3; i++) { await scrollBy(page, 280); }
  await sleep(1500);

  // ── User Story Tests tab ──
  console.log('  → Showing User Story Tests …');
  await page.click('#reqTabGenerated');
  await sleep(600);
  await glow(page, '#reqTabGenerated', 700);
  await scrollTo(page, '#reqResultPaneGenerated');
  await sleep(1000);
  for (let i = 0; i < 3; i++) { await scrollBy(page, 280); }
  await sleep(1500);

  // ── E2E Tests tab ──
  console.log('  → Showing E2E Tests …');
  await page.click('#reqTabE2eflows');
  await sleep(600);
  await glow(page, '#reqTabE2eflows', 700);
  await scrollTo(page, '#reqResultPaneE2eflows');
  await sleep(1000);
  for (let i = 0; i < 3; i++) { await scrollBy(page, 260); }
  await sleep(1500);

  await scrollTo(page, '#reqResults');
  await sleep(1000);

  // ══════════════════════════════════════════════════════════════════════════
  // TAB 2 — Tests → Automation
  // ══════════════════════════════════════════════════════════════════════════
  console.log('\n── TAB 2: Tests → Automation ──');
  await navTo(page, 'agents');
  await showTitle(page, '② Tests → Automation',
    'Describe test steps in plain English → Java automation code → GitHub PR');

  // Show the 3 sub-mode options
  await glow(page, '.analyze-tab[data-qa-mode="new"]',      700);
  await glow(page, '.analyze-tab[data-qa-mode="existing"]', 700);
  await glow(page, '.analyze-tab[data-qa-mode="testrail"]', 700);
  await sleep(600);

  // Write mode — fill fields
  await page.click('.analyze-tab[data-qa-mode="new"]');
  await sleep(500);

  await glow(page, '#qaNewModule', 600);
  await typeSlowly(page, '#qaNewModule', 'github', 80);
  await sleep(400);

  await glow(page, '#qaNewType', 600);
  await page.selectOption('#qaNewType', 'web');
  await sleep(500);

  // Fill textarea instantly (like a paste) — avoids 30s type timeout on long text
  await glow(page, '#qaSpecContent', 700);
  await page.fill('#qaSpecContent',
`Module: github
Type: web

Steps:
1. Navigate to github website
2. Perform login using Username: automationdemo@yopmail.com, Password: automationPassword
3. Then using left sidebar, find and click to this repo: automationdemo/QA-Dashboard, this should open the respective Repo page
4. Validate that the image related to "Test Coverage Data for all the Projects:" is present in the ReadMe file and is properly visible`);
  await sleep(1500);

  // Set the "Save as" name to github_flow
  await glow(page, '#qaNewModuleName', 600);
  await typeSlowly(page, '#qaNewModuleName', 'github_flow', 70);
  await sleep(800);

  // Confirm Auto-push PR is checked
  await glow(page, '#qaAutoPush', 700);
  await sleep(400);

  // Click Run Agent
  await glow(page, '#qaRunBtn', 1200);
  await page.click('#qaRunBtn');
  console.log('  → Agent started — waiting for live card …');
  await sleep(1000);

  // Wait for the live run card to appear
  await page.waitForSelector('#qaLiveCard', { state: 'visible', timeout: 15000 });
  await scrollTo(page, '#qaLiveCard');
  await sleep(1000);

  // Watch progress steps light up
  console.log('  → Watching progress steps …');

  // step watcher: resolves when step is active/done OR agent reaches terminal state
  const waitForStep = (step, ms) => page.waitForFunction(
    ({ step }) => {
      const el = document.querySelector(`.qa-step[data-step="${step}"]`);
      const stepOk = el && (el.classList.contains('running') || el.classList.contains('done') || el.classList.contains('failed'));
      const statusEl = document.getElementById('qaLiveStatus');
      const terminal = statusEl && (() => {
        const t = statusEl.textContent.trim().toLowerCase();
        return t === 'completed' || t === 'failed' || t === 'error' || t.includes('done') || t.includes('shipped');
      })();
      return stepOk || terminal;
    },
    { step }, { timeout: ms }
  );

  // Step 1: Parse
  await waitForStep('parse', 120000);
  await glow(page, '.qa-step[data-step="parse"]', 1000);
  console.log('  ✓  Step 1: Parse');
  await sleep(800);

  // Step 2: Validate Web (GitHub navigation can be slow)
  await waitForStep('validate_web', 300000);
  await glow(page, '.qa-step[data-step="validate_web"]', 1000);
  console.log('  ✓  Step 2: Validate Web');
  await sleep(800);

  // Step 3: Generate
  await waitForStep('generate', 300000);
  await glow(page, '.qa-step[data-step="generate"]', 1000);
  console.log('  ✓  Step 3: Generate');
  await sleep(800);

  // Scroll down so console output is visible while steps 4-5 run
  await scrollTo(page, '#qaConsole');
  await sleep(800);

  // Step 4: Run & Fix
  await waitForStep('run_and_fix', 300000);
  await glow(page, '.qa-step[data-step="run_and_fix"]', 1000);
  console.log('  ✓  Step 4: Run & Fix');
  await sleep(1000);

  // Step 5: Ship
  await waitForStep('ship', 300000);
  await glow(page, '.qa-step[data-step="ship"]', 1000);
  console.log('  ✓  Step 5: Ship');
  await sleep(800);

  // *** WAIT for full agent completion ***
  await waitForAgentDone(page);

  // Scroll back to the full live card to show result summary + PR link
  await scrollTo(page, '#qaLiveCard');
  await sleep(1000);
  await glow(page, '#qaLiveStatus', 1200);
  await sleep(600);
  await glow(page, '#qaLiveResult', 1500);
  await sleep(2000);

  // Scroll through the console log so viewers can read it
  console.log('  → Scrolling through console output …');
  await scrollTo(page, '#qaConsole');
  for (let i = 0; i < 5; i++) { await scrollBy(page, 200); }
  await sleep(2000);

  // ══════════════════════════════════════════════════════════════════════════
  // TAB 3 — Talk to Tests
  // ══════════════════════════════════════════════════════════════════════════
  console.log('\n── TAB 3: Talk to Tests ──');
  await navTo(page, 'ask');
  await showTitle(page, '③ Talk to Tests',
    'Ask questions in plain English — answers grounded in your test knowledge base');

  await page.evaluate(() => window.scrollTo({ top: 0 }));
  await sleep(800);

  // Show Internal Docs toggle
  try {
    const toggleSel = '#ragToggle, .rag-toggle input, [id*="Toggle"]';
    await glow(page, toggleSel, 1000);
    await sleep(400);
  } catch (_) {}

  // Type ONE focused question
  await glow(page, '#questionInput', 800);
  await typeSlowly(page, '#questionInput',
    'Do we have testcases for 2FA at login?', 40);
  await sleep(900);

  // Submit
  await page.keyboard.press('Enter');
  console.log('  → Submitted question — waiting for full answer …');
  await sleep(800);

  // *** WAIT for the answer to be fully rendered ***
  await waitForAnswer(page);

  // Scroll to the answer
  await scrollTo(page, '#askResults');
  await sleep(800);

  // Slowly scroll through the full answer
  for (let i = 0; i < 5; i++) { await scrollBy(page, 220); }
  await sleep(2500);

  // Scroll back to top of answer to show it in full
  await scrollTo(page, '#askResults');
  await sleep(2000);

  // ══════════════════════════════════════════════════════════════════════════
  // OUTRO
  // ══════════════════════════════════════════════════════════════════════════
  console.log('\n── OUTRO ──');
  await navTo(page, 'analyze');
  await sleep(600);

  await glow(page, '.nav-item[data-nav="analyze"]', 700);
  await sleep(200);
  await glow(page, '.nav-item[data-nav="agents"]',  700);
  await sleep(200);
  await glow(page, '.nav-item[data-nav="ask"]',     700);
  await sleep(600);

  await showTitle(page,
    'AI Test Studio',
    '① Generate Tests  ·  ② Automate Tests  ·  ③ Talk to Tests\n\nPowered by QA Agent Network');
  await sleep(1500);

  // ── Wrap up ────────────────────────────────────────────────────────────────
  console.log('\n→ Closing browser and saving video …');
  await context.close();
  await browser.close();

  const files = fs.readdirSync(VIDEO_DIR).filter(f => f.endsWith('.webm'));
  if (!files.length) { console.error('✗ No .webm found'); process.exit(1); }
  const webmPath = path.join(VIDEO_DIR, files[0]);
  const dur = execSync(`ffprobe -v error -show_entries format=duration -of csv=p=0 "${webmPath}" 2>/dev/null`).toString().trim();
  console.log(`✓ Raw video: ${webmPath}  (${(+dur/60).toFixed(1)} min)`);

  console.log('→ Converting to MP4 …');
  execSync(
    `ffmpeg -y -i "${webmPath}" -c:v libx264 -preset fast -crf 20 -pix_fmt yuv420p "${OUTPUT_MP4}"`,
    { stdio: 'inherit' }
  );

  const sizeMB = (fs.statSync(OUTPUT_MP4).size / 1024 / 1024).toFixed(1);
  console.log(`\n✅  Demo video ready: ${OUTPUT_MP4}  (${sizeMB} MB)`);
})();
