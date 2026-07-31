const form = document.getElementById("search-form");
const resultsEl = document.getElementById("results");
const emptyEl = document.getElementById("empty-state");
const warningsEl = document.getElementById("warnings");
const statusLine = document.getElementById("status-line");

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function chip(label, tone = "ok") {
  return `<span class="chip chip-${tone}">${escapeHtml(label)}</span>`;
}

function sourceBadge(sources) {
  const parts = [];
  if (sources.includes("eu")) parts.push('<span class="badge badge-eu">EU sanctielijst</span>');
  if (sources.includes("opensanctions")) parts.push('<span class="badge badge-os">OpenSanctions</span>');
  return parts.join(" ");
}

function euCard(item) {
  const eu = item.eu;
  const entity = item.entity;
  const chips = eu.details.map((d) => {
    const tone = d.score >= 85 ? "ok" : d.score >= 50 ? "warn" : "bad";
    return chip(d.label, tone);
  }).join("");
  const aliases = (entity.aliases || []).slice(0, 3).map((a) => `<li>${escapeHtml(a)}</li>`).join("");
  const regs = (entity.regulations || []).map((r) => {
    const title = escapeHtml(r.number_title || r.programme || "Reglement");
    if (r.publication_url) return `<a href="${escapeHtml(r.publication_url)}" target="_blank" rel="noopener">${title}</a>`;
    return title;
  }).join(", ");
  const births = (entity.birthdates || []).filter((b) => b.date || b.year).slice(0, 2)
    .map((b) => {
      const bits = [b.date || b.year, b.place || b.city].filter(Boolean);
      return bits.join(", ");
    });
  const birthLine = births.length ? `<p class="muted">Geboren: ${births.map(escapeHtml).join(" · ")}</p>` : "";
  const natLine = entity.citizenships.length ? `<p class="muted">Nationaliteit: ${entity.citizenships.map((c) => escapeHtml(c.description || c.iso2)).join(", ")}</p>` : "";
  return `
    <article class="card">
      <div class="card-head">
        <h2>${escapeHtml(entity.name)}</h2>
        ${sourceBadge(["eu"])}
      </div>
      <p class="ref">EU-ref: ${escapeHtml(entity.eu_reference_number || "-")}${entity.united_nations_id ? ` · VN-id: ${escapeHtml(entity.united_nations_id)}` : ""}</p>
      <p class="score-line">Totaalscore: <strong>${item.score}</strong>/100 ${chips}</p>
      ${birthLine}
      ${natLine}
      ${aliases ? `<ul class="aliases">${aliases}</ul>` : ""}
      ${regs ? `<p class="muted">Reglement(en): ${regs}</p>` : ""}
      ${entity.function ? `<p class="muted">Functie: ${escapeHtml(entity.function)}</p>` : ""}
    </article>`;
}

function osCard(item) {
  const os = item.opensanctions;
  const entity = item.entity;
  const exp = Object.keys(os.explanations || {})
    .filter((k) => (os.explanations[k] || {}).score > 0)
    .map((k) => chip(k, "warn")).join("");
  const datasets = (os.datasets || []).slice(0, 5).join(", ");
  const topics = (entity.topics || []).slice(0, 4).map((t) => chip(t, "warn")).join("");
  return `
    <article class="card card-os">
      <div class="card-head">
        <h2>${escapeHtml(entity.name)}</h2>
        ${sourceBadge(["opensanctions"])}
      </div>
      <p class="ref">Schema: ${escapeHtml(entity.schema || "-")}</p>
      <p class="score-line">Score: <strong>${os.score.toFixed(2)}</strong> (${os.match ? "match" : "geen match"}) ${exp}</p>
      ${topics ? `<p class="muted">Risico-tags: ${topics}</p>` : ""}
      ${os.datasets ? `<p class="muted">Datasets: ${escapeHtml(datasets)}</p>` : ""}
      <p class="muted"><a href="${escapeHtml(os.url)}" target="_blank" rel="noopener">Open op opensanctions.org</a></p>
    </article>`;
}

function renderResults(data) {
  resultsEl.innerHTML = "";
  warningsEl.hidden = true;
  warningsEl.textContent = "";
  if (data.warnings.length) {
    warningsEl.hidden = false;
    warningsEl.innerHTML = data.warnings.map((w) => `<p>${escapeHtml(w)}</p>`).join("");
  }
  if (!data.results.length) {
    emptyEl.hidden = false;
    return;
  }
  emptyEl.hidden = true;
  data.results.forEach((item) => {
    const html = item.source === "opensanctions" ? osCard(item) : euCard(item);
    resultsEl.insertAdjacentHTML("beforeend", html);
  });
}

async function loadStatus() {
  try {
    const res = await fetch("/api/status");
    if (!res.ok) return;
    const s = await res.json();
    const parts = [
      `${s.entity_count.toLocaleString("nl-NL")} records`,
      s.source === "fresh" ? "data vers" : "data gecachet",
      s.opensanctions_active ? "OpenSanctions actief" : "OpenSanctions niet actief",
    ];
    statusLine.textContent = parts.join(" · ");
  } catch {
    statusLine.textContent = "Status niet beschikbaar";
  }
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = document.getElementById("name").value.trim();
  if (!name) return;
  const params = new URLSearchParams();
  params.set("name", name);
  const birthYear = document.getElementById("birth_year").value;
  if (birthYear) params.set("birth_year", birthYear);
  const nationality = document.getElementById("nationality").value.trim();
  if (nationality) params.set("nationality", nationality);
  const birthPlace = document.getElementById("birth_place").value.trim();
  if (birthPlace) params.set("birth_place", birthPlace);
  const entityType = document.getElementById("entity_type").value;
  if (entityType) params.set("entity_type", entityType);
  resultsEl.innerHTML = '<p class="loading">Zoeken...</p>';
  emptyEl.hidden = true;
  warningsEl.hidden = true;
  try {
    const res = await fetch(`/api/search?${params}`);
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || "Fout bij zoeken");
    }
    renderResults(await res.json());
  } catch (err) {
    resultsEl.innerHTML = "";
    warningsEl.hidden = false;
    warningsEl.innerHTML = `<p>${escapeHtml(err.message)}</p>`;
  }
});

loadStatus();
