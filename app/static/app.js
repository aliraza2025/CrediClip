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

function parseAnalysisConfidence(result) {
  const notes = Array.isArray(result.notes) ? result.notes : [];
  const note = notes.find((entry) => /Analysis-confidence score:/i.test(String(entry)));
  if (!note) return null;
  const match = String(note).match(/Analysis-confidence score:\s*([0-9.]+)/i);
  if (!match) return null;
  return Number(match[1]);
}

function labelBucket(level, options = {}) {
  const normalized = formatLevel(level);
  const { low = 'Low', medium = 'Moderate', high = 'High' } = options;
  if (normalized === 'low') return low;
  if (normalized === 'medium') return medium;
  if (normalized === 'high') return high;
  return medium;
}

function toneBucket(label) {
  const normalized = String(label || '').toLowerCase();
  if (['low', 'strong', 'human'].includes(normalized)) return 'good';
  if (['moderate', 'mixed', 'weak'].includes(normalized)) return 'warn';
  return 'bad';
}

function buildCompactSummary(result) {
  const flags = result.flags || [];
  const byType = Object.fromEntries(flags.map((f) => [f.type, f]));

  const misinformation = labelBucket(byType.misinformation?.level, {
    low: 'Low',
    medium: 'Moderate',
    high: 'High',
  });
  const scam = labelBucket(byType.scam?.level, {
    low: 'Low',
    medium: 'Moderate',
    high: 'High',
  });

  const originLevel = formatLevel(byType.generation_origin?.level);
  const aiContent = originLevel === 'low' ? 'Human' : originLevel === 'high' ? 'AI' : 'Mixed';
  const manipulation = labelBucket(byType.manipulation?.level, {
    low: 'Low',
    medium: 'Moderate',
    high: 'High',
  });

  const confidenceScore = parseAnalysisConfidence(result);
  const confidence = confidenceScore === null
    ? 'Medium'
    : confidenceScore >= 65
      ? 'High'
      : confidenceScore >= 35
        ? 'Medium'
        : 'Low';

  const evidenceFlag = byType.evidence_quality;
  let evidence = 'Weak';
  if (formatLevel(evidenceFlag?.level) === 'low') {
    evidence = 'Strong';
  } else if (
    formatLevel(evidenceFlag?.level) === 'high' &&
    ((result.evidence_coverage?.total_tokens ?? 0) === 0 || Number(evidenceFlag?.score ?? 0) >= 85)
  ) {
    evidence = 'Missing';
  }

  const score = Number(result.credibility_score ?? 0);
  const misinformationLevel = formatLevel(byType.misinformation?.level);
  const scamLevel = formatLevel(byType.scam?.level);
  const manipulationLevel = formatLevel(byType.manipulation?.level);

  let oneLineSummary = 'Video needs verification';
  if (scamLevel === 'high') oneLineSummary = 'Possible scam video';
  else if (misinformationLevel === 'high') oneLineSummary = 'Possibly misleading video';
  else if (originLevel === 'high') oneLineSummary = 'Likely AI-made video';
  else if (originLevel === 'medium' && manipulationLevel !== 'low') oneLineSummary = 'Possibly AI-assisted video';
  else if (manipulationLevel === 'high') oneLineSummary = 'Likely edited video';
  else if (evidence === 'Missing') oneLineSummary = 'Video lacks evidence';
  else if (evidence === 'Weak' && confidence === 'Low') oneLineSummary = 'Video needs verification';
  else if (score >= 75) oneLineSummary = 'Likely trustworthy video';
  else if (score >= 60) oneLineSummary = 'Mostly trustworthy video';
  else if (score < 45) oneLineSummary = 'Questionable video credibility';

  return {
    oneLineSummary,
    rows: [
      { label: 'Misinformation', value: misinformation, tone: toneBucket(misinformation) },
      { label: 'Scam', value: scam, tone: toneBucket(scam) },
      { label: 'AI Content', value: aiContent, tone: toneBucket(aiContent) },
      { label: 'Manipulation', value: manipulation, tone: toneBucket(manipulation) },
    ],
    confidence: { value: confidence, tone: toneBucket(confidence) },
    evidence: { value: evidence, tone: toneBucket(evidence) },
  };
}

function renderEvaluationSummary(result) {
  if (!evaluationSummary) return;
  const compact = buildCompactSummary(result);

  evaluationSummary.innerHTML = `
    <p class="result-summary-line">${escapeHtml(compact.oneLineSummary)}</p>
    <div class="compact-panel">
      <div class="compact-section">
        <span class="compact-heading">Summary</span>
        <span class="compact-summary-chip">${escapeHtml(compact.oneLineSummary)}</span>
      </div>
      <div class="compact-section">
        <span class="compact-heading">Signals</span>
        <div class="compact-rows">
          ${compact.rows
            .map(
              (row) => `
                <div class="compact-row">
                  <span class="compact-label">${escapeHtml(row.label)}</span>
                  <span class="compact-value compact-${escapeHtml(row.tone)}">${escapeHtml(row.value)}</span>
                </div>`,
            )
            .join('')}
        </div>
      </div>
      <div class="compact-section compact-footer">
        <div class="compact-row">
          <span class="compact-label">Confidence</span>
          <span class="compact-value compact-${escapeHtml(compact.confidence.tone)}">${escapeHtml(compact.confidence.value)}</span>
        </div>
        <div class="compact-row">
          <span class="compact-label">Evidence</span>
          <span class="compact-value compact-${escapeHtml(compact.evidence.tone)}">${escapeHtml(compact.evidence.value)}</span>
        </div>
      </div>
    </div>
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
      <span class="score-label">Score</span>
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
