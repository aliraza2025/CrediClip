const form = document.getElementById('analyze-form');
const analyzerCard = document.getElementById('analyzer-card');
const resultsCard = document.getElementById('results-card');
const loadingPanel = document.getElementById('loading-panel');
const resultsContent = document.getElementById('results-content');
const loadingStatus = document.getElementById('loading-status');
const statusBar = document.getElementById('status-bar');
const scoreDiv = document.getElementById('score');
const evaluationSummary = document.getElementById('evaluation-summary');
const tryAgainBtn = document.getElementById('try-again-btn');
const submitBtn = form.querySelector('button[type="submit"]');
const legacyClaimsEl = document.getElementById('claims');
const legacyFlagsEl = document.getElementById('flags');

const LOADING_MESSAGES = [
  'Preparing request...',
  'Fetching metadata and transcript...',
  'Analyzing evidence signals...',
  'Computing final score...',
];
let loadingTimer = null;
let loadingPct = 0;
let loadingMsgIdx = 0;

function escapeHtml(str) {
  return String(str ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function isLinkOnlySubmission(payload) {
  return payload.url && !payload.caption?.trim() && !payload.transcript?.trim();
}

async function createAnalysisJob(payload) {
  const response = await fetch('/api/jobs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url: payload.url }),
  });
  const job = await response.json();
  if (!response.ok) {
    throw new Error(job.detail || 'Could not create analysis job');
  }
  return job;
}

async function fetchAnalysisJob(jobId) {
  const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`);
  const job = await response.json();
  if (!response.ok) {
    throw new Error(job.detail || 'Could not load analysis job');
  }
  return job;
}

async function waitForJobResult(job, { pollMs = 1500 } = {}) {
  let current = job;
  let reusedMessageShown = Boolean(job.reused);
  while (true) {
    if (current.status === 'completed' && current.result) {
      return current.result;
    }
    if (current.status === 'failed') {
      throw new Error(current.error || 'Analysis failed');
    }

    if (current.status === 'queued') {
      loadingStatus.textContent = current.reused
        ? 'Reusing existing analysis job and waiting for a worker...'
        : 'Queued for worker processing...';
      loadingPct = Math.max(loadingPct, 18);
      statusBar.style.width = `${loadingPct}%`;
    } else if (current.status === 'processing') {
      loadingStatus.textContent = current.reused || reusedMessageShown
        ? 'Reusing existing analysis job. Worker is finishing the result...'
        : 'Worker is extracting evidence and scoring the video...';
      reusedMessageShown = reusedMessageShown || Boolean(current.reused);
      loadingPct = Math.max(loadingPct, 54);
      statusBar.style.width = `${loadingPct}%`;
    }

    await sleep(pollMs);
    current = await fetchAnalysisJob(current.id);
  }
}

function hideLegacyResultBlocks() {
  if (legacyClaimsEl) {
    legacyClaimsEl.innerHTML = '';
    legacyClaimsEl.hidden = true;
  }
  if (legacyFlagsEl) {
    legacyFlagsEl.innerHTML = '';
    legacyFlagsEl.hidden = true;
  }
}

async function startLoading() {
  if (loadingTimer) {
    clearInterval(loadingTimer);
  }
  loadingPct = 8;
  loadingMsgIdx = 0;
  loadingStatus.textContent = LOADING_MESSAGES[0];
  statusBar.style.width = `${loadingPct}%`;

  analyzerCard.classList.add('swipe-up');
  await sleep(320);
  analyzerCard.hidden = true;
  analyzerCard.classList.remove('swipe-up');
  resultsCard.hidden = false;
  loadingPanel.hidden = false;
  resultsContent.hidden = true;
  hideLegacyResultBlocks();

  loadingTimer = setInterval(() => {
    loadingPct = Math.min(92, loadingPct + 3.5);
    statusBar.style.width = `${loadingPct}%`;

    if (loadingPct >= 30 && loadingMsgIdx < 1) {
      loadingMsgIdx = 1;
      loadingStatus.textContent = LOADING_MESSAGES[1];
    } else if (loadingPct >= 58 && loadingMsgIdx < 2) {
      loadingMsgIdx = 2;
      loadingStatus.textContent = LOADING_MESSAGES[2];
    } else if (loadingPct >= 80 && loadingMsgIdx < 3) {
      loadingMsgIdx = 3;
      loadingStatus.textContent = LOADING_MESSAGES[3];
    }
  }, 220);
}

function stopLoading() {
  if (loadingTimer) {
    clearInterval(loadingTimer);
    loadingTimer = null;
  }
  loadingPct = 100;
  statusBar.style.width = '100%';
}

async function showScoreView() {
  loadingPanel.classList.add('fade-out');
  await sleep(180);
  loadingPanel.hidden = true;
  loadingPanel.classList.remove('fade-out');
  resultsContent.hidden = false;
  resultsContent.classList.add('score-enter');
  await sleep(260);
  resultsContent.classList.remove('score-enter');
}

function formatLevel(level) {
  return String(level || 'unknown').toLowerCase();
}

function narrativeFromFlags(result) {
  const flags = result.flags || [];
  const byType = Object.fromEntries(flags.map((f) => [f.type, f]));
  const positives = [];
  const caveats = [];

  const origin = byType.generation_origin;
  if (origin) {
    const lvl = formatLevel(origin.level);
    if (lvl === 'low') {
      positives.push('Looks Human-Written: Very likely created by a person, not AI.');
    } else if (lvl === 'high') {
      positives.push('Looks AI-Generated: Strong signs this content was created or heavily altered by AI.');
    } else {
      caveats.push('AI vs Human likelihood: Mixed signals, likely AI-assisted or partially synthetic.');
    }
  }

  const misinformation = byType.misinformation;
  if (misinformation) {
    const lvl = formatLevel(misinformation.level);
    if (lvl === 'low') positives.push('Misinformation Risk: Low — nothing strongly misleading detected.');
    else if (lvl === 'medium') caveats.push('Misinformation Risk: Moderate — some claims may need fact verification.');
    else caveats.push('Misinformation Risk: High — likely misleading or unsupported claims detected.');
  }

  const scam = byType.scam;
  if (scam) {
    const lvl = formatLevel(scam.level);
    if (lvl === 'low') positives.push('Scam Risk: Very low — no typical scam patterns found.');
    else if (lvl === 'medium') caveats.push('Scam Risk: Moderate — some scam-like persuasion patterns detected.');
    else caveats.push('Scam Risk: High — multiple scam-like patterns detected.');
  }

  const manipulation = byType.manipulation;
  if (manipulation) {
    const lvl = formatLevel(manipulation.level);
    const scoreNum = Number(manipulation.score || 0);
    if (lvl === 'low' && scoreNum <= 0.1) {
      positives.push('Manipulation Risk: None detected — content appears authentic.');
    } else if (lvl === 'low') {
      positives.push('Manipulation Risk: Low — no strong signs of synthetic media.');
    } else if (lvl === 'medium') {
      caveats.push('Manipulation Risk: Moderate — some edited/synthetic signals are present.');
    } else {
      caveats.push('Manipulation Risk: High — strong deepfake or synthetic-media signals detected.');
    }
  }

  const uncertainty = byType.uncertainty;
  if (uncertainty) {
    const lvl = formatLevel(uncertainty.level);
    if (lvl === 'low') positives.push('Low Uncertainty: There is enough evidence to be reasonably confident.');
    else if (lvl === 'medium') {
      caveats.push('Moderate Uncertainty: Some supporting evidence is missing, so confidence is limited.');
    } else {
      caveats.push('High Uncertainty: There isn’t enough strong supporting evidence to be fully confident.');
    }
  }

  const evidence = byType.evidence_quality;
  if (evidence) {
    const lvl = formatLevel(evidence.level);
    if (lvl === 'low') positives.push('Evidence Quality: Strong — sources/backing information are solid.');
    else if (lvl === 'medium') {
      caveats.push('Evidence Quality: Fair — some useful backing exists, but not enough for high confidence.');
    } else {
      caveats.push(
        'Evidence Quality: The sources or backing information are weak, which lowers overall trust.',
      );
    }
  }

  return { positives, caveats };
}

function renderEvaluationSummary(result) {
  if (!evaluationSummary) return;
  const { positives, caveats } = narrativeFromFlags(result);
  const summaryList = [...positives, ...caveats];
  const lines = summaryList.length ? summaryList : ['No strong evaluation signals were detected.'];

  evaluationSummary.innerHTML = `
    <h3 class="summary-title">What We Found</h3>
    <ul class="summary-list">
      ${lines.map((p) => `<li>${escapeHtml(p)}</li>`).join('')}
    </ul>
  `;
}

function resetToMainScreen() {
  analyzerCard.hidden = false;
  analyzerCard.classList.add('slide-in');
  resultsCard.hidden = true;
  loadingPanel.hidden = true;
  resultsContent.hidden = true;
  scoreDiv.textContent = '';
  if (evaluationSummary) evaluationSummary.innerHTML = '';
  form.reset();
  hideLegacyResultBlocks();
  setTimeout(() => analyzerCard.classList.remove('slide-in'), 260);
  document.getElementById('url').focus();
}

if (tryAgainBtn) {
  tryAgainBtn.addEventListener('click', resetToMainScreen);
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();

  const payload = {
    url: document.getElementById('url').value,
    caption: document.getElementById('caption').value,
    transcript: document.getElementById('transcript').value,
  };

  submitBtn.disabled = true;
  submitBtn.textContent = 'Analyzing...';
  scoreDiv.textContent = '';
  if (evaluationSummary) evaluationSummary.innerHTML = '';
  hideLegacyResultBlocks();
  await startLoading();

  try {
    let result;
    if (isLinkOnlySubmission(payload)) {
      const job = await createAnalysisJob(payload);
      if (job.reused) {
        loadingStatus.textContent = 'Reusing existing analysis job...';
        loadingPct = Math.max(loadingPct, 14);
        statusBar.style.width = `${loadingPct}%`;
      }
      result = await waitForJobResult(job);
    } else {
      const response = await fetch('/api/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      result = await response.json();
      if (!response.ok) {
        throw new Error(result.detail || 'Analysis failed');
      }
    }

    scoreDiv.innerHTML = `
      <span class="score-main">${escapeHtml(result.credibility_score)} / 100</span>
      <span class="score-label">Credibility Score</span>
    `;
    renderEvaluationSummary(result);

    stopLoading();
    await showScoreView();
  } catch (error) {
    stopLoading();
    scoreDiv.textContent = `Error: ${error.message}`;
    if (evaluationSummary) evaluationSummary.innerHTML = '';
    await showScoreView();
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = 'Analyze';
  }
});
