const tokenInput = document.getElementById("token");
const loadBtn = document.getElementById("load-btn");
const statusLine = document.getElementById("status-line");
const eventsEl = document.getElementById("events");

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function formatTs(ts) {
  if (!ts) return "";
  const parsed = new Date(ts);
  if (Number.isNaN(parsed.getTime())) return escapeHtml(ts);
  return escapeHtml(parsed.toLocaleString("nl-NL", { timeZone: "UTC" }));
}

function querySummary(query) {
  if (!query) return "";
  const parts = [];
  if (query.name) parts.push(escapeHtml(query.name));
  if (query.birth_year) parts.push(`geb. ${query.birth_year}`);
  if (query.nationality) parts.push(`nat. ${escapeHtml(query.nationality)}`);
  if (query.birth_place) parts.push(escapeHtml(query.birth_place));
  if (query.entity_type) parts.push(escapeHtml(query.entity_type));
  return parts.join(" · ");
}

function renderEvents(data) {
  eventsEl.innerHTML = "";
  if (!data.events.length) {
    eventsEl.innerHTML = '<p class="muted">Geen events gevonden.</p>';
    return;
  }
  const rows = data.events.map((e) => `
    <tr>
      <td>${formatTs(e.ts)}</td>
      <td>${escapeHtml(e.ip || "")}</td>
      <td>${escapeHtml(e.user || "—")}</td>
      <td>${escapeHtml(e.method || "")}</td>
      <td>${escapeHtml(e.path || "")}</td>
      <td class="query-cell">${querySummary(e.query)}</td>
      <td>${e.result_count ?? ""}</td>
      <td>${escapeHtml((e.sources || []).join(", "))}</td>
    </tr>`).join("");
  eventsEl.innerHTML = `
    <p class="muted">${data.total.toLocaleString("nl-NL")} events (getoond: ${data.events.length})</p>
    <table class="audit-table">
      <thead>
        <tr>
          <th>Datum</th>
          <th>IP</th>
          <th>Gebruiker</th>
          <th>Methode</th>
          <th>Pad</th>
          <th>Query</th>
          <th>Resultaten</th>
          <th>Bronnen</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>`;
}

async function loadEvents() {
  const token = tokenInput.value.trim();
  if (!token) {
    statusLine.textContent = "Vul het beheertoken in.";
    return;
  }
  statusLine.textContent = "Laden...";
  eventsEl.innerHTML = "";
  let res;
  try {
    res = await fetch("/api/audit", { headers: { Authorization: `Bearer ${token}` } });
  } catch {
    statusLine.textContent = "Audit-log niet beschikbaar.";
    return;
  }
  if (res.status === 401) {
    statusLine.textContent = "Niet geautoriseerd: controleer het token.";
    return;
  }
  if (res.status === 404) {
    statusLine.textContent = "Audit uitgeschakeld: AUDIT_ADMIN_TOKEN is niet ingesteld.";
    return;
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    statusLine.textContent = body.detail || "Fout bij laden van events.";
    return;
  }
  statusLine.textContent = "";
  renderEvents(await res.json());
}

loadBtn.addEventListener("click", loadEvents);
tokenInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") loadEvents();
});
