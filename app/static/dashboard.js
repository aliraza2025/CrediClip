const statTotal = document.getElementById('stat-total');
const statQueued = document.getElementById('stat-queued');
const statProcessing = document.getElementById('stat-processing');
const statCompleted = document.getElementById('stat-completed');
const statFailed = document.getElementById('stat-failed');
const oldestQueued = document.getElementById('oldest-queued');
const lastRefresh = document.getElementById('last-refresh');
const liveIndicator = document.getElementById('live-indicator');
const liveStatus = document.getElementById('live-status');
const jobsBody = document.getElementById('jobs-body');
const statusFilter = document.getElementById('status-filter');
const platformFilter = document.getElementById('platform-filter');
const workerFilter = document.getElementById('worker-filter');
const limitFilter = document.getElementById('limit-filter');
const intervalFilter = document.getElementById('interval-filter');
const searchFilter = document.getElementById('search-filter');
const autoRefreshToggle = document.getElementById('auto-refresh-toggle');
const refreshBtn = document.getElementById('refresh-btn');
const laneGrid = document.getElementById('lane-grid');
const platformGrid = document.getElementById('platform-grid');
const latestFailure = document.getElementById('latest-failure');
const activityFeed = document.getElementById('activity-feed');
const jobDetail = document.getElementById('job-detail');
const queueSparklines = document.getElementById('queue-sparklines');
const systemSummary = document.getElementById('system-summary');
const snapshotStatus = document.getElementById('snapshot-status');
const modeGrid = document.getElementById('mode-grid');
const opsReadout = document.getElementById('ops-readout');

let timer = null;
let selectedJobId = null;

const state = {
  jobs: [],
  previousJobs: new Map(),
  activity: [],
  stats: null,
  workerOptions: [],
  statHistory: [],
};

const KNOWN_LANES = [
  {
    workerId: 'oracle-worker-1',
    label: 'Main lane',
    description: 'Instagram + TikTok',
    platforms: new Set(['instagram', 'tiktok', 'unknown']),
    badgeClass: 'lane-main',
  },
  {
    workerId: 'oracle-worker-youtube',
    label: 'YouTube lane',
    description: 'YouTube Shorts',
    platforms: new Set(['youtube_shorts']),
    badgeClass: 'lane-youtube',
  },
];

function esc(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function fmtDate(value) {
  if (!value) return '-';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString();
}

function fmtRelative(value) {
  if (!value) return '-';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  const seconds = Math.max(0, Math.round((Date.now() - d.getTime()) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
}

function detectPlatform(url) {
  const value = String(url || '').toLowerCase();
  if (value.includes('youtube.com/shorts/') || value.includes('youtu.be/')) return 'youtube_shorts';
  if (value.includes('instagram.com/')) return 'instagram';
  if (value.includes('tiktok.com/')) return 'tiktok';
  return 'unknown';
}

function fmtPlatform(platform) {
  if (platform === 'youtube_shorts') return 'YouTube';
  if (platform === 'instagram') return 'Instagram';
  if (platform === 'tiktok') return 'TikTok';
  return 'Unknown';
}

function setLiveState(mode, text) {
  liveIndicator.className = `live-indicator ${mode}`;
  liveStatus.textContent = text;
}

function noteLines(job) {
  return [
    ...(Array.isArray(job.ingest_notes) ? job.ingest_notes : []),
    ...(Array.isArray(job.debug_notes) ? job.debug_notes : []),
    ...(Array.isArray(job.result?.notes) ? job.result.notes : []),
  ];
}

function detectModelMode(job) {
  const joined = noteLines(job).join(' ').toLowerCase();
  if (joined.includes('openai llm') || joined.includes('claim verification attempted via openai')) {
    return 'openai';
  }
  if (joined.includes('using heuristics') || joined.includes('open-source evidence heuristics')) {
    return 'heuristic';
  }
  return 'unknown';
}

function detectIngestState(job) {
  const joined = noteLines(job).join(' ').toLowerCase();
  if (
    job.platform === 'youtube_shorts' &&
    (
      joined.includes('bot check') ||
      joined.includes('bot-blocked') ||
      joined.includes('sign-in / bot check') ||
      joined.includes('using lighter fallback analysis') ||
      joined.includes('skipped worker video download because youtube richer extraction is bot-blocked')
    )
  ) {
    return 'degraded_youtube';
  }
  return 'normal';
}

function ingestBadge(job) {
  const ingestState = detectIngestState(job);
  if (ingestState === 'degraded_youtube') {
    return '<span class="pill ingest degraded">Degraded ingest</span>';
  }
  return '';
}

function modeBadge(mode) {
  const label = mode === 'openai' ? 'OpenAI' : mode === 'heuristic' ? 'Heuristic' : 'Unknown';
  return `<span class="pill mode ${esc(mode)}">${esc(label)}</span>`;
}

function flashMetric(el, nextValue) {
  const next = String(nextValue ?? '0');
  if (el.textContent !== '-' && el.textContent !== next) {
    el.classList.remove('stat-changed');
    void el.offsetWidth;
    el.classList.add('stat-changed');
  }
  el.textContent = next;
}

function summarizeJob(job) {
  const platform = fmtPlatform(job.platform);
  const score = job.result?.credibility_score;
  const scoreText = typeof score === 'number' ? `score ${score.toFixed(2)}` : job.status;
  return `${platform} ${scoreText}`;
}

function collectActivity(nextJobs) {
  const nextMap = new Map(nextJobs.map((job) => [job.id, job]));
  const entries = [];

  for (const job of nextJobs) {
    const previous = state.previousJobs.get(job.id);
    if (!previous) {
      entries.push({
        id: `${job.id}-new-${job.updated_at}`,
        kind: 'new',
        text: `New ${fmtPlatform(job.platform)} job ${job.id.slice(0, 8)} entered ${job.status}.`,
        updatedAt: job.updated_at,
      });
      continue;
    }
    if (previous.status !== job.status) {
      entries.push({
        id: `${job.id}-${job.status}-${job.updated_at}`,
        kind: job.status,
        text: `${fmtPlatform(job.platform)} job ${job.id.slice(0, 8)} moved ${previous.status} -> ${job.status}.`,
        updatedAt: job.updated_at,
      });
      continue;
    }
    const previousScore = previous.result?.credibility_score;
    const nextScore = job.result?.credibility_score;
    if (previousScore !== nextScore && typeof nextScore === 'number') {
      entries.push({
        id: `${job.id}-score-${job.updated_at}`,
        kind: 'score',
        text: `${fmtPlatform(job.platform)} job ${job.id.slice(0, 8)} updated to ${nextScore.toFixed(2)}.`,
        updatedAt: job.updated_at,
      });
    }
  }

  state.previousJobs = nextMap;
  if (!entries.length) return;
  state.activity = [...entries, ...state.activity]
    .sort((a, b) => new Date(b.updatedAt) - new Date(a.updatedAt))
    .slice(0, 12);
}

function renderActivity() {
  if (!state.activity.length) {
    activityFeed.innerHTML = '<div class="insight-empty">Waiting for job activity...</div>';
    return;
  }
  activityFeed.innerHTML = state.activity
    .map(
      (entry) => `
        <article class="activity-item ${esc(entry.kind)}">
          <div class="activity-copy">${esc(entry.text)}</div>
          <div class="activity-time">${esc(fmtRelative(entry.updatedAt))}</div>
        </article>
      `,
    )
    .join('');
}

function recordStatHistory(data) {
  state.statHistory.push({
    at: Date.now(),
    queued: data.counts?.queued ?? 0,
    processing: data.counts?.processing ?? 0,
    completed: data.counts?.completed ?? 0,
    failed: data.counts?.failed ?? 0,
  });
  state.statHistory = state.statHistory.slice(-24);
}

function sparklineCard(label, key, className) {
  const samples = state.statHistory.map((sample) => sample[key]);
  const max = Math.max(...samples, 1);
  const current = samples[samples.length - 1] ?? 0;
  const previous = samples[samples.length - 2] ?? current;
  const delta = current - previous;
  const direction = delta > 0 ? `+${delta}` : `${delta}`;
  const bars = samples.length
    ? samples
        .map((value) => {
          const height = Math.max(12, Math.round((value / max) * 64));
          return `<span class="spark-bar ${esc(className)}" style="height:${height}px" title="${esc(value)}"></span>`;
        })
        .join('')
    : '<span class="spark-empty">-</span>';

  return `
    <article class="spark-card">
      <div class="spark-card-head">
        <h3>${esc(label)}</h3>
        <span class="spark-delta ${delta > 0 ? 'up' : delta < 0 ? 'down' : 'flat'}">${esc(direction)}</span>
      </div>
      <div class="spark-bars">${bars}</div>
      <div class="spark-foot">
        <strong>${esc(current)}</strong>
        <span>latest</span>
      </div>
    </article>
  `;
}

function renderQueuePulse() {
  if (!state.statHistory.length) {
    queueSparklines.innerHTML = '<div class="insight-empty">Collecting queue samples...</div>';
    return;
  }
  queueSparklines.innerHTML = [
    sparklineCard('Queued', 'queued', 'queued'),
    sparklineCard('Processing', 'processing', 'processing'),
    sparklineCard('Completed', 'completed', 'completed'),
    sparklineCard('Failed', 'failed', 'failed'),
  ].join('');
}

function renderSystemSummary() {
  if (!state.stats) {
    snapshotStatus.textContent = 'warming up';
    systemSummary.innerHTML = '<div class="insight-empty">Waiting for live queue data...</div>';
    return;
  }

  const jobs = state.jobs;
  const activeWorkers = new Set(jobs.map((job) => job.worker_id).filter(Boolean)).size;
  const recentWindowMs = 30 * 60 * 1000;
  const recentCompleted = jobs.filter((job) => job.status === 'completed' && Date.now() - new Date(job.updated_at).getTime() <= recentWindowMs).length;
  const recentFailed = jobs.filter((job) => job.status === 'failed' && Date.now() - new Date(job.updated_at).getTime() <= recentWindowMs).length;
  const queuePressure = (state.stats.counts?.queued ?? 0) + (state.stats.counts?.processing ?? 0);
  const healthLabel = recentFailed > 0 ? 'watching failures' : queuePressure > 0 ? 'draining cleanly' : 'steady';

  snapshotStatus.textContent = healthLabel;
  systemSummary.innerHTML = `
    <article class="summary-card">
      <span>Active workers</span>
      <strong>${esc(activeWorkers || 0)}</strong>
      <p>${esc(activeWorkers > 1 ? 'parallel lanes live' : 'single lane active')}</p>
    </article>
    <article class="summary-card">
      <span>Recent completions</span>
      <strong>${esc(recentCompleted)}</strong>
      <p>last 30 minutes</p>
    </article>
    <article class="summary-card">
      <span>Failure pressure</span>
      <strong>${esc(recentFailed)}</strong>
      <p>recent failed jobs</p>
    </article>
    <article class="summary-card">
      <span>Queue pressure</span>
      <strong>${esc(queuePressure)}</strong>
      <p>queued + processing now</p>
    </article>
  `;
}

function renderOpsReadout() {
  if (!state.stats) {
    opsReadout.innerHTML = '<div class="insight-empty">Waiting for live queue data...</div>';
    return;
  }

  const jobs = state.jobs;
  const recentCompleted = jobs.filter((job) => job.status === 'completed');
  const recentFailed = jobs.filter((job) => job.status === 'failed');
  const recentHighEvidence = recentCompleted.filter((job) => job.result?.evidence_coverage?.level === 'high').length;
  const openaiCount = recentCompleted.filter((job) => detectModelMode(job) === 'openai').length;
  const heuristicCount = recentCompleted.filter((job) => detectModelMode(job) === 'heuristic').length;
  const youtubeProcessing = jobs.some((job) => job.platform === 'youtube_shorts' && job.status === 'processing');

  const items = [
    { label: 'Queue state', value: state.stats.counts?.processing ? 'active' : 'clear', tone: state.stats.counts?.processing ? 'processing' : 'completed' },
    { label: 'Recent high-evidence jobs', value: String(recentHighEvidence), tone: recentHighEvidence > 0 ? 'completed' : 'unknown' },
    { label: 'OpenAI-assisted jobs', value: String(openaiCount), tone: openaiCount > 0 ? 'openai' : 'unknown' },
    { label: 'Heuristic jobs', value: String(heuristicCount), tone: heuristicCount > 0 ? 'heuristic' : 'unknown' },
    { label: 'Recent failures', value: String(recentFailed.length), tone: recentFailed.length > 0 ? 'failed' : 'completed' },
    { label: 'YouTube lane', value: youtubeProcessing ? 'busy' : 'idle', tone: youtubeProcessing ? 'processing' : 'unknown' },
  ];

  opsReadout.innerHTML = items
    .map(
      (item) => `
        <article class="ops-chip ${esc(item.tone)}">
          <span>${esc(item.label)}</span>
          <strong>${esc(item.value)}</strong>
        </article>
      `,
    )
    .join('');
}

function renderJobDetail() {
  const fallback = '<div class="insight-empty">Select a job to inspect notes, errors, and evidence summary.</div>';
  if (!selectedJobId) {
    jobDetail.className = 'job-detail empty';
    jobDetail.innerHTML = fallback;
    return;
  }

  const job = state.jobs.find((item) => item.id === selectedJobId);
  if (!job) {
    jobDetail.className = 'job-detail empty';
    jobDetail.innerHTML = fallback;
    return;
  }

  const result = job.result || {};
  const evidence = result.evidence_coverage || {};
  const notes = Array.isArray(result.notes) ? result.notes : [];
  const ingestNotes = Array.isArray(job.ingest_notes) ? job.ingest_notes : [];
  const debugNotes = Array.isArray(job.debug_notes) ? job.debug_notes : [];
  const claim = Array.isArray(result.claim_assessments) && result.claim_assessments.length
    ? result.claim_assessments[0]
    : null;

  jobDetail.className = 'job-detail';
  jobDetail.innerHTML = `
    <div class="job-detail-head">
      <div>
        <h3>${esc(fmtPlatform(job.platform))} <span class="pill ${esc(job.status)}">${esc(job.status)}</span></h3>
        <p class="subtitle">${esc(job.id)} · ${modeBadge(detectModelMode(job))} ${ingestBadge(job)}</p>
      </div>
      <button id="clear-job-detail" class="ghost-btn" type="button">Clear</button>
    </div>
    <div class="detail-metrics">
      <div><span>Worker</span><strong>${esc(job.worker_id || '-')}</strong></div>
      <div><span>Score</span><strong>${typeof result.credibility_score === 'number' ? result.credibility_score.toFixed(2) : '-'}</strong></div>
      <div><span>Evidence</span><strong>${esc(evidence.level || '-')} / ${esc(evidence.total_tokens ?? 0)}</strong></div>
      <div><span>Updated</span><strong>${esc(fmtRelative(job.updated_at))}</strong></div>
    </div>
    <div class="detail-block">
      <h4>URL</h4>
      <a href="${esc(job.url)}" target="_blank" rel="noopener noreferrer">${esc(job.url)}</a>
    </div>
    ${job.error ? `<div class="detail-block"><h4>Error</h4><pre>${esc(job.error)}</pre></div>` : ''}
    ${
      claim
        ? `<div class="detail-block">
            <h4>Top Claim</h4>
            <p>${esc(claim.status)} · confidence ${esc(claim.confidence ?? '-')}</p>
            <pre>${esc(claim.claim || '')}</pre>
          </div>`
        : ''
    }
    <div class="detail-columns">
      <div class="detail-block">
        <h4>Analysis Notes</h4>
        ${notes.length ? `<ul>${notes.map((note) => `<li>${esc(note)}</li>`).join('')}</ul>` : '<p>-</p>'}
      </div>
      <div class="detail-block">
        <h4>Worker Notes</h4>
        ${ingestNotes.length ? `<ul>${ingestNotes.map((note) => `<li>${esc(note)}</li>`).join('')}</ul>` : '<p>-</p>'}
      </div>
    </div>
    <div class="detail-block">
      <h4>Debug Notes</h4>
      ${debugNotes.length ? `<ul>${debugNotes.map((note) => `<li>${esc(note)}</li>`).join('')}</ul>` : '<p>-</p>'}
    </div>
  `;

  const clearButton = document.getElementById('clear-job-detail');
  if (clearButton) {
    clearButton.addEventListener('click', () => {
      selectedJobId = null;
      renderJobDetail();
      renderJobsTable();
    });
  }
}

function jobRow(job) {
  const shortId = esc((job.id || '').slice(0, 12));
  const url = esc(job.url || '');
  const score = typeof job.result?.credibility_score === 'number' ? job.result.credibility_score.toFixed(2) : '-';
  const selectedClass = job.id === selectedJobId ? 'is-selected' : '';
  return `
    <tr class="${selectedClass}" data-job-id="${esc(job.id)}">
      <td title="${esc(job.id)}">${shortId}</td>
      <td><span class="pill platform ${esc(job.platform)}">${esc(fmtPlatform(job.platform))}</span> ${ingestBadge(job)}</td>
      <td>${modeBadge(detectModelMode(job))}</td>
      <td><span class="pill ${esc(job.status)}">${esc(job.status)}</span></td>
      <td>${esc(job.worker_id || '-')}</td>
      <td>${esc(job.caption_chars ?? 0)}</td>
      <td>${esc(job.transcript_chars ?? 0)}</td>
      <td>${esc(score)}</td>
      <td title="${esc(fmtDate(job.updated_at))}">${esc(fmtRelative(job.updated_at))}</td>
      <td><a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a></td>
    </tr>
  `;
}

function laneCard(lane) {
  return `
    <article class="lane-card ${esc(lane.badgeClass)}" data-lane-worker="${esc(lane.workerId)}">
      <div class="lane-card-head">
        <div>
          <h3>${esc(lane.label)}</h3>
          <p>${esc(lane.description)}</p>
        </div>
        <span class="pill ${lane.processing > 0 ? 'processing' : 'completed'}">${lane.processing > 0 ? 'active' : 'idle'}</span>
      </div>
      <dl class="lane-metrics">
        <div>
          <dt>Processing</dt>
          <dd>${esc(lane.processing)}</dd>
        </div>
        <div>
          <dt>Queued for lane</dt>
          <dd>${esc(lane.queued)}</dd>
        </div>
        <div>
          <dt>Recent done</dt>
          <dd>${esc(lane.completed)}</dd>
        </div>
      </dl>
      <p class="lane-foot">Latest: ${esc(lane.latestSummary)}</p>
    </article>
  `;
}

function platformCard(platform) {
  return `
    <article class="platform-card" data-platform-name="${esc(platform.name)}">
      <h3>${esc(fmtPlatform(platform.name))}</h3>
      <dl class="lane-metrics compact">
        <div>
          <dt>Recent</dt>
          <dd>${esc(platform.total)}</dd>
        </div>
        <div>
          <dt>Processing</dt>
          <dd>${esc(platform.processing)}</dd>
        </div>
        <div>
          <dt>Queued</dt>
          <dd>${esc(platform.queued)}</dd>
        </div>
        <div>
          <dt>Failed</dt>
          <dd>${esc(platform.failed)}</dd>
        </div>
      </dl>
    </article>
  `;
}

function modeCard(mode) {
  return `
    <article class="mode-card ${esc(mode.modeClass)}">
      <div class="mode-card-head">
        <div>
          <h3>${esc(fmtPlatform(mode.platform))}</h3>
          <p>${esc(mode.summary)}</p>
        </div>
        <span class="pill mode ${esc(mode.modeClass)}">${esc(mode.modeLabel)}</span>
      </div>
      <dl class="lane-metrics compact">
        <div>
          <dt>OpenAI jobs</dt>
          <dd>${esc(mode.openai)}</dd>
        </div>
        <div>
          <dt>Heuristic jobs</dt>
          <dd>${esc(mode.heuristic)}</dd>
        </div>
        <div>
          <dt>Unknown</dt>
          <dd>${esc(mode.unknown)}</dd>
        </div>
        <div>
          <dt>Recent</dt>
          <dd>${esc(mode.total)}</dd>
        </div>
      </dl>
    </article>
  `;
}

function bindInsightInteractions() {
  laneGrid.querySelectorAll('[data-lane-worker]').forEach((card) => {
    card.addEventListener('click', () => {
      workerFilter.value = card.dataset.laneWorker || '';
      renderJobsTable();
    });
  });
  platformGrid.querySelectorAll('[data-platform-name]').forEach((card) => {
    card.addEventListener('click', () => {
      platformFilter.value = card.dataset.platformName || '';
      renderJobsTable();
    });
  });
}

function renderInsights() {
  const jobs = state.jobs;
  const laneMap = new Map(
    KNOWN_LANES.map((lane) => [
      lane.workerId,
      {
        ...lane,
        processing: 0,
        queued: 0,
        completed: 0,
        latestSummary: 'No recent jobs',
      },
    ]),
  );

  for (const job of jobs) {
    if (job.worker_id && !laneMap.has(job.worker_id)) {
      laneMap.set(job.worker_id, {
        workerId: job.worker_id,
        label: job.worker_id,
        description: 'Detected worker',
        platforms: new Set([job.platform]),
        badgeClass: 'lane-generic',
        processing: 0,
        queued: 0,
        completed: 0,
        latestSummary: 'No recent jobs',
      });
    }
  }

  for (const lane of laneMap.values()) {
    const laneJobs = jobs.filter((job) => job.worker_id === lane.workerId);
    lane.processing = laneJobs.filter((job) => job.status === 'processing').length;
    lane.completed = laneJobs.filter((job) => job.status === 'completed').length;
    lane.queued = jobs.filter((job) => job.status === 'queued' && lane.platforms.has(job.platform)).length;
    const latestJob = [...laneJobs].sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at))[0];
    if (latestJob) lane.latestSummary = `${summarizeJob(latestJob)} · ${fmtRelative(latestJob.updated_at)}`;
  }

  const platforms = ['instagram', 'tiktok', 'youtube_shorts', 'unknown'].map((name) => {
    const platformJobs = jobs.filter((job) => job.platform === name);
    return {
      name,
      total: platformJobs.length,
      processing: platformJobs.filter((job) => job.status === 'processing').length,
      queued: platformJobs.filter((job) => job.status === 'queued').length,
      failed: platformJobs.filter((job) => job.status === 'failed').length,
    };
  });

  const platformModes = ['instagram', 'tiktok', 'youtube_shorts', 'unknown'].map((platform) => {
    const platformJobs = jobs.filter((job) => job.platform === platform);
    const counts = { openai: 0, heuristic: 0, unknown: 0 };
    for (const job of platformJobs) {
      counts[detectModelMode(job)] += 1;
    }
    const dominantMode = counts.openai >= counts.heuristic && counts.openai > 0
      ? 'openai'
      : counts.heuristic > 0
        ? 'heuristic'
        : 'unknown';
    const modeLabel = dominantMode === 'openai' ? 'OpenAI' : dominantMode === 'heuristic' ? 'Heuristic' : 'Unknown';
    const summary = dominantMode === 'openai'
      ? 'Hosted claim reasoning active on recent jobs.'
      : dominantMode === 'heuristic'
        ? 'Fallback rules are carrying recent jobs.'
        : 'No clear recent mode signal.';
    return {
      platform,
      total: platformJobs.length,
      openai: counts.openai,
      heuristic: counts.heuristic,
      unknown: counts.unknown,
      modeClass: dominantMode,
      modeLabel,
      summary,
    };
  });

  laneGrid.innerHTML = Array.from(laneMap.values()).map(laneCard).join('');
  platformGrid.innerHTML = platforms.map(platformCard).join('');
  modeGrid.innerHTML = platformModes.map(modeCard).join('');
  bindInsightInteractions();

  const recentFailure = [...jobs]
    .filter((job) => job.status === 'failed')
    .sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at))[0];
  latestFailure.textContent = recentFailure
    ? `Latest failure: ${fmtPlatform(recentFailure.platform)} on ${recentFailure.worker_id || 'unassigned'} (${recentFailure.error || 'unknown error'})`
    : 'Latest failure: none';
}

function filteredJobs() {
  const query = searchFilter.value.trim().toLowerCase();
  let jobs = [...state.jobs];

  if (statusFilter.value) jobs = jobs.filter((job) => job.status === statusFilter.value);
  if (platformFilter.value) jobs = jobs.filter((job) => job.platform === platformFilter.value);
  if (workerFilter.value) jobs = jobs.filter((job) => (job.worker_id || '') === workerFilter.value);
  if (query) {
    jobs = jobs.filter((job) => {
      const haystack = [
        job.id,
        job.url,
        job.worker_id,
        job.error,
        ...(job.ingest_notes || []),
        ...(job.debug_notes || []),
      ]
        .join(' ')
        .toLowerCase();
      return haystack.includes(query);
    });
  }

  jobs.sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at));
  return jobs.slice(0, Number(limitFilter.value || 50));
}

function renderJobsTable() {
  const jobs = filteredJobs();
  if (!jobs.length) {
    jobsBody.innerHTML = '<tr><td colspan="10">No jobs found for the current filters.</td></tr>';
    renderJobDetail();
    return;
  }

  if (selectedJobId && !jobs.some((job) => job.id === selectedJobId) && !state.jobs.some((job) => job.id === selectedJobId)) {
    selectedJobId = null;
  }

  jobsBody.innerHTML = jobs.map(jobRow).join('');
  jobsBody.querySelectorAll('tr[data-job-id]').forEach((row) => {
    row.addEventListener('click', () => {
      selectedJobId = row.dataset.jobId;
      renderJobsTable();
      renderJobDetail();
    });
  });
  renderJobDetail();
}

function updateWorkerFilterOptions() {
  const workers = [...new Set(state.jobs.map((job) => job.worker_id).filter(Boolean))].sort();
  const current = workerFilter.value;
  const options = ['<option value="">all</option>', ...workers.map((worker) => `<option value="${esc(worker)}">${esc(worker)}</option>`)];
  workerFilter.innerHTML = options.join('');
  if (workers.includes(current)) workerFilter.value = current;
}

async function loadStats() {
  const res = await fetch('/api/queue/stats');
  if (!res.ok) throw new Error('Failed to load queue stats');
  const data = await res.json();
  state.stats = data;
  recordStatHistory(data);
  flashMetric(statTotal, data.total ?? 0);
  flashMetric(statQueued, data.counts?.queued ?? 0);
  flashMetric(statProcessing, data.counts?.processing ?? 0);
  flashMetric(statCompleted, data.counts?.completed ?? 0);
  flashMetric(statFailed, data.counts?.failed ?? 0);

  if (data.oldest_queued_job_id) {
    oldestQueued.textContent = `Oldest queued: ${data.oldest_queued_job_id.slice(0, 10)}... (${fmtRelative(data.oldest_queued_created_at)})`;
  } else {
    oldestQueued.textContent = 'Oldest queued: none';
  }
  renderQueuePulse();
  renderSystemSummary();
}

async function loadJobs() {
  const res = await fetch('/api/jobs?limit=100');
  if (!res.ok) throw new Error('Failed to load jobs list');
  const data = await res.json();
  const jobs = (data.jobs || []).map((job) => ({
    ...job,
    platform: detectPlatform(job.url),
  }));
  collectActivity(jobs);
  state.jobs = jobs;
  updateWorkerFilterOptions();
  renderInsights();
  renderActivity();
  renderJobsTable();
  renderSystemSummary();
  renderOpsReadout();
}

async function refreshAll() {
  refreshBtn.disabled = true;
  setLiveState('loading', 'Refreshing live data…');
  try {
    await Promise.all([loadStats(), loadJobs()]);
    lastRefresh.textContent = `Last refresh: ${new Date().toLocaleTimeString()}`;
    setLiveState(autoRefreshToggle.checked ? 'live' : 'idle', autoRefreshToggle.checked ? 'Live updates on' : 'Auto refresh paused');
  } catch (err) {
    lastRefresh.textContent = `Last refresh failed: ${err.message}`;
    setLiveState('error', 'Refresh failed');
  } finally {
    refreshBtn.disabled = false;
  }
}

function resetTimer() {
  if (timer) clearInterval(timer);
  timer = null;
  if (autoRefreshToggle.checked) {
    timer = setInterval(refreshAll, Number(intervalFilter.value || 5000));
  }
}

statusFilter.addEventListener('change', renderJobsTable);
platformFilter.addEventListener('change', renderJobsTable);
workerFilter.addEventListener('change', renderJobsTable);
limitFilter.addEventListener('change', renderJobsTable);
searchFilter.addEventListener('input', renderJobsTable);
intervalFilter.addEventListener('change', () => {
  resetTimer();
  if (autoRefreshToggle.checked) refreshAll();
});
autoRefreshToggle.addEventListener('change', () => {
  resetTimer();
  setLiveState(autoRefreshToggle.checked ? 'live' : 'idle', autoRefreshToggle.checked ? 'Live updates on' : 'Auto refresh paused');
});
refreshBtn.addEventListener('click', refreshAll);

resetTimer();
refreshAll();

window.addEventListener('beforeunload', () => {
  if (timer) clearInterval(timer);
});
