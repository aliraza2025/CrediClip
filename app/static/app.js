const form = document.getElementById('analyze-form');
const resultsCard = document.getElementById('results-card');
const scoreDiv = document.getElementById('score');
const flagsDiv = document.getElementById('flags');
const claimsDiv = document.getElementById('claims');
const notesList = document.getElementById('notes');

function escapeHtml(str) {
  return str
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();

  const payload = {
    url: document.getElementById('url').value,
    caption: document.getElementById('caption').value,
    transcript: document.getElementById('transcript').value,
  };

  scoreDiv.textContent = 'Analyzing...';
  flagsDiv.innerHTML = '';
  claimsDiv.innerHTML = '';
  notesList.innerHTML = '';
  resultsCard.hidden = false;

  try {
    const response = await fetch('/api/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.detail || 'Analysis failed');
    }

    scoreDiv.innerHTML = `<strong>Credibility Score:</strong> ${result.credibility_score} / 100 (${escapeHtml(result.platform)})`;

    flagsDiv.innerHTML = result.flags
      .map(
        (flag) => `
        <article class="flag ${escapeHtml(flag.level)}">
          <strong>${escapeHtml(flag.type)}:</strong> ${escapeHtml(flag.level)} (${flag.score})<br/>
          <small>${escapeHtml(flag.rationale)}</small>
        </article>
      `,
      )
      .join('');

    claimsDiv.innerHTML = result.claim_assessments.length
      ? result.claim_assessments
          .map(
            (claim) => `
            <article class="claim">
              <strong>${escapeHtml(claim.status)}</strong> (${claim.confidence})
              <p>${escapeHtml(claim.claim)}</p>
              <small>${escapeHtml(claim.rationale)}</small>
            </article>
          `,
          )
          .join('')
      : '<p>No claims extracted from provided text.</p>';

    result.notes.forEach((note) => {
      const li = document.createElement('li');
      li.textContent = note;
      notesList.appendChild(li);
    });
  } catch (error) {
    scoreDiv.textContent = `Error: ${error.message}`;
  }
});
