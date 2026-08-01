const form = document.getElementById("search-form");
const resultsEl = document.getElementById("results");
const emptyEl = document.getElementById("empty-state");
const warningsEl = document.getElementById("warnings");
const statusLine = document.getElementById("status-line");
const searchPanel = document.getElementById("search-panel");
const loginPanel = document.getElementById("login-panel");
const authBar = document.getElementById("auth-bar");
let currentUser = null;
let authRequired = false;

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

function euStatusLabel(source) {
  switch (source) {
    case "ok": return "data vers";
    case "missing": return "geen data";
    case "error": return "data fout";
    default: return "data status onbekend";
  }
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
  const natLine = (entity.citizenships || []).length ? `<p class="muted">Nationaliteit: ${entity.citizenships.map((c) => escapeHtml(c.description || c.iso2)).join(", ")}</p>` : "";
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
      <p class="score-line">Score: <strong>${Number(os.score).toFixed(2)}</strong> (${os.match ? "match" : "geen match"}) ${exp}</p>
      ${topics ? `<p class="muted">Risico-tags: ${topics}</p>` : ""}
      ${os.datasets ? `<p class="muted">Datasets: ${escapeHtml(datasets)}</p>` : ""}
      <p class="muted"><a href="${escapeHtml(os.url)}" target="_blank" rel="noopener">Open op opensanctions.org</a></p>
    </article>`;
}

function pepCard(item) {
  const pep = item.pep;
  const entity = item.entity;
  const chips = (pep.details || []).map((d) => {
    const tone = d.score >= 85 ? "ok" : d.score >= 50 ? "warn" : "bad";
    return chip(d.label, tone);
  }).join("");
  const dsChips = (pep.datasets || []).slice(0, 5).map((d) =>
    `<a class="chip chip-pep" href="${escapeHtml(d.url)}" target="_blank" rel="noopener">${escapeHtml(d.title)}${d.country ? " · " + escapeHtml(d.country.toUpperCase()) : ""}</a>`
  ).join("");
  const topics = (entity.topics || []).slice(0, 4).map((t) => chip(t, "warn")).join("");
  const political = (entity.political || []).length
    ? `<p class="muted">Partij/fractie: ${entity.political.map(escapeHtml).join(", ")}</p>` : "";
  const positions = (entity.positions || []).slice(0, 5).map((p) => {
    const bits = [p.status, [p.start, p.end].filter(Boolean).join("-")].filter(Boolean);
    const meta = bits.length ? ` (${bits.map(escapeHtml).join(", ")})` : "";
    return `<li>${escapeHtml(p.role || "")}${meta}</li>`;
  }).join("");
  const functiesLine = positions ? `<p class="muted">Functies:</p><ul class="aliases">${positions}</ul>` : "";
  const births = (entity.birth_dates || []).slice(0, 2).map(escapeHtml).join(", ");
  const birthLine = births ? `<p class="muted">Geboren: ${births}</p>` : "";
  const natLine = (entity.citizenships || []).length
    ? `<p class="muted">Nationaliteit: ${entity.citizenships.map((c) => escapeHtml(c.toUpperCase())).join(", ")}</p>` : "";
  return `
    <article class="card card-pep">
      <div class="card-head">
        <h2>${escapeHtml(entity.name)}</h2>
        <span class="badge badge-pep">PEP</span>
      </div>
      <p class="ref">Schema: ${escapeHtml(entity.schema || "-")}</p>
      <p class="score-line">Totaalscore: <strong>${item.score}</strong>/100 ${chips}</p>
      ${birthLine}
      ${natLine}
      ${political}
      ${functiesLine}
      ${topics ? `<p class="muted">Risico-tags: ${topics}</p>` : ""}
      ${dsChips ? `<p class="muted">Bronnen: ${dsChips}</p>` : ""}
      <p class="muted"><a href="${escapeHtml(pep.url)}" target="_blank" rel="noopener">Open op opensanctions.org</a></p>
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
    let html;
    if (item.source === "opensanctions") html = osCard(item);
    else if (item.source === "pep") html = pepCard(item);
    else html = euCard(item);
    resultsEl.insertAdjacentHTML("beforeend", html);
  });
}

async function loadStatus() {
  let res;
  try {
    res = await fetch("/api/status", { credentials: "same-origin" });
  } catch {
    statusLine.textContent = "Status niet beschikbaar";
    return;
  }
  if (!res.ok) {
    statusLine.textContent = "Status niet beschikbaar";
    return;
  }
  try {
    const s = await res.json();
    const parts = [
      `${s.entity_count.toLocaleString("nl-NL")} records`,
      euStatusLabel(s.source),
    ];
    if (s.opensanctions_active) {
      parts.push("OpenSanctions actief");
    }
    if (s.index) {
      if (s.index.status === "building") {
        parts.push("Index wordt opgebouwd…");
      } else if (s.index.status === "error") {
        parts.push("Index-fout");
      } else if (s.index.enabled) {
        parts.push(`${s.index.pep_count.toLocaleString("nl-NL")} PEP-records`);
      }
    }
    statusLine.textContent = parts.join(" · ");
    const footer = document.getElementById("footer");
    if (footer && s.version) {
      footer.textContent = `Versie ${s.version}`;
    }
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
    const res = await fetch(`/api/search?${params}`, { credentials: "same-origin" });
    if (res.status === 401) {
      authRequired = true;
      resultsEl.innerHTML = "";
      requireLogin("Log in om te kunnen zoeken.");
      return;
    }
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

function setLoggedIn(user) {
  currentUser = user;
  authBar.hidden = false;
  authBar.innerHTML = `
    <span>Ingelogd als <strong>${escapeHtml(user.username)}</strong> (${escapeHtml(user.role)})</span>
    <button type="button" id="logout-btn">Uitloggen</button>`;
  document.getElementById("logout-btn").addEventListener("click", async () => {
    try {
      await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" });
    } finally {
      window.location.reload();
    }
  });
}

function setLoggedOut() {
  currentUser = null;
  authBar.hidden = false;
  authBar.textContent = "";
  const link = document.createElement("button");
  link.type = "button";
  link.id = "login-link";
  link.textContent = "Inloggen";
  link.addEventListener("click", () => openLoginPanel());
  authBar.appendChild(link);
}

async function loadAuth() {
  let res;
  try {
    res = await fetch("/api/auth/me", { credentials: "same-origin" });
  } catch {
    return;
  }
  if (res.ok) {
    setLoggedIn(await res.json());
  } else {
    setLoggedOut();
  }
}

async function refreshLoginMethods() {
  const msBtn = document.getElementById("entra-login-btn");
  let res;
  try {
    res = await fetch("/api/auth/login", { credentials: "same-origin", redirect: "manual" });
  } catch {
    msBtn.hidden = true;
    return;
  }
  if (res.type === "opaqueredirect") {
    msBtn.hidden = false;
    return;
  }
  msBtn.hidden = true;
  if (res.ok) {
    const methods = (await res.json().catch(() => ({}))).methods || [];
    document.getElementById("login-form").hidden = !methods.includes("local");
  }
}

function openLoginPanel(message) {
  loginPanel.hidden = false;
  const errorEl = document.getElementById("login-error");
  errorEl.textContent = message || "";
  loginPanel.scrollIntoView({ block: "center" });
  refreshLoginMethods();
}

function requireLogin(message) {
  openLoginPanel(message);
  searchPanel.hidden = true;
}

const loginForm = document.getElementById("login-form");
loginForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const username = document.getElementById("login-username").value.trim();
  const password = document.getElementById("login-password").value;
  const errorEl = document.getElementById("login-error");
  errorEl.textContent = "";
  if (!username || !password) {
    errorEl.textContent = "Vul gebruikersnaam en wachtwoord in";
    return;
  }
  try {
    const res = await fetch("/api/auth/login", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || "Inloggen mislukt");
    }
    window.location.reload();
  } catch (err) {
    errorEl.textContent = err.message;
  }
});

const entraBtn = document.getElementById("entra-login-btn");
entraBtn.addEventListener("click", () => {
  window.location.href = "/api/auth/login";
});

async function maybeShowAuditLink() {
  const footer = document.getElementById("footer");
  if (!footer) return;
  let res;
  try {
    res = await fetch("/api/audit", { credentials: "same-origin" });
  } catch {
    return;
  }
  if (res.status !== 401) return;
  const link = document.createElement("a");
  link.href = "/audit";
  link.textContent = "Audit-log";
  link.style.marginLeft = "0.75rem";
  footer.appendChild(link);
}

async function init() {
  await loadStatus();
  await loadAuth();
  await maybeShowAuditLink();
}

init();

const exportBtn = document.getElementById("export-btn");
exportBtn.addEventListener("click", () => {
  if (authRequired && !currentUser) {
    requireLogin("Log in om te kunnen exporteren.");
    return;
  }
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
  const author = document.getElementById("author").value.trim();
  if (author) params.set("author", author);
  const format = document.getElementById("export-format").value;
  if (format) params.set("format", format);
  window.open(`/api/search/export?${params}`, "_blank");
});
