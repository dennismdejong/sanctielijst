const form = document.getElementById("search-form");
const resultsEl = document.getElementById("results");
const emptyEl = document.getElementById("empty-state");
const warningsEl = document.getElementById("warnings");
const statusLine = document.getElementById("status-line");
const searchPanel = document.getElementById("search-panel");
const loginPanel = document.getElementById("login-panel");
const authBar = document.getElementById("auth-bar");
const watchBtn = document.getElementById("watch-btn");
const watchlistList = document.getElementById("watchlist-list");
const watchlistNotices = document.getElementById("watchlist-notices");
let currentUser = null;
let authRequired = false;
let authMethods = [];
let lastDataVersion = null;
let rescanning = false;

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
  if (sources.includes("sanctie")) parts.push('<span class="badge badge-sanctie">Sancties (int.)</span>');
  if (sources.includes("opensanctions")) parts.push('<span class="badge badge-os">OpenSanctions</span>');
  return parts.join(" ");
}

function riskFlagsHtml(item) {
  const riskFlags = (item.risk_countries || []).map((f) =>
    chip(`Risicoland ${f.code} · ${f.lists.map((l) => l.replaceAll("_", " ")).join(", ")}`, "bad")
  ).join("");
  return riskFlags ? `<p class="muted">${riskFlags}</p>` : "";
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
  const riskLine = riskFlagsHtml(item);
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
      ${riskLine}
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
  const riskLine = riskFlagsHtml(item);
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
      ${riskLine}
      ${political}
      ${functiesLine}
      ${topics ? `<p class="muted">Risico-tags: ${topics}</p>` : ""}
      ${dsChips ? `<p class="muted">Bronnen: ${dsChips}</p>` : ""}
      <p class="muted"><a href="${escapeHtml(pep.url)}" target="_blank" rel="noopener">Open op opensanctions.org</a></p>
    </article>`;
}

function sanctCard(item) {
  const sanc = item.sanctie;
  const entity = item.entity;
  const chips = (sanc.details || []).map((d) => {
    const tone = d.score >= 85 ? "ok" : d.score >= 50 ? "warn" : "bad";
    return chip(d.label, tone);
  }).join("");
  const dsChips = (sanc.datasets || []).slice(0, 5).map((d) =>
    `<a class="chip chip-sanctie" href="${escapeHtml(d.url)}" target="_blank" rel="noopener">${escapeHtml(d.title)}${d.country ? " · " + escapeHtml(d.country.toUpperCase()) : ""}</a>`
  ).join("");
  const topics = (entity.topics || []).slice(0, 4).map((t) => chip(t, "warn")).join("");
  const riskLine = riskFlagsHtml(item);
  const births = (entity.birth_dates || []).slice(0, 2).map(escapeHtml).join(", ");
  const birthLine = births ? `<p class="muted">Geboren: ${births}</p>` : "";
  const natLine = (entity.citizenships || []).length
    ? `<p class="muted">Nationaliteit: ${entity.citizenships.map((c) => escapeHtml(c.toUpperCase())).join(", ")}</p>` : "";
  return `
    <article class="card card-sanctie">
      <div class="card-head">
        <h2>${escapeHtml(entity.name)}</h2>
        <span class="badge badge-sanctie">Sancties (int.)</span>
      </div>
      <p class="ref">Schema: ${escapeHtml(entity.schema || "-")}</p>
      <p class="score-line">Totaalscore: <strong>${item.score}</strong>/100 ${chips}</p>
      ${birthLine}
      ${natLine}
      ${riskLine}
      ${topics ? `<p class="muted">Risico-tags: ${topics}</p>` : ""}
      ${dsChips ? `<p class="muted">Bronnen: ${dsChips}</p>` : ""}
      <p class="muted"><a href="${escapeHtml(sanc.url)}" target="_blank" rel="noopener">Open op opensanctions.org</a></p>
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
    else if (item.source === "sanctie") html = sanctCard(item);
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
    if (typeof s.data_version === "number" && (!s.index || s.index.status !== "building")) {
      handleDataVersion(s.data_version);
    }
    if (s.auth) {
      authRequired = !!s.auth.required;
      authMethods = s.auth.methods || [];
    }
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
        if (s.index.sanctions_count) {
          parts.push(`${s.index.sanctions_count.toLocaleString("nl-NL")} sanctie-records`);
        }
      }
    }
    if (s.risk && s.risk.version) {
      parts.push(`Risicolanden v${s.risk.version}`);
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
  authBar.textContent = "";
  if (!authRequired) {
    authBar.hidden = true;
    return;
  }
  authBar.hidden = false;
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

function showWatchNotice(message) {
  const notice = document.createElement("p");
  notice.textContent = message;
  watchlistNotices.appendChild(notice);
  watchlistNotices.hidden = false;
  window.setTimeout(() => {
    notice.remove();
    if (!watchlistNotices.childElementCount) watchlistNotices.hidden = true;
  }, 12000);
}

function readWatchEntry(watchId) {
  try {
    const raw = localStorage.getItem("watchlist." + watchId);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function watchCriteria(entry) {
  const payload = { name: entry.name };
  ["birth_year", "nationality", "birth_place", "entity_type"].forEach((key) => {
    if (entry[key]) payload[key] = entry[key];
  });
  return payload;
}

async function addWatch() {
  const name = document.getElementById("name").value.trim();
  if (!name) {
    showWatchNotice("Vul eerst een naam in om te bewaken.");
    return;
  }
  const entry = {
    name,
    birth_year: document.getElementById("birth_year").value,
    nationality: document.getElementById("nationality").value.trim(),
    birth_place: document.getElementById("birth_place").value.trim(),
    entity_type: document.getElementById("entity_type").value,
    known: {},
  };
  let res;
  try {
    res = await fetch("/api/watchlists", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
  } catch {
    showWatchNotice("Bewaking toevoegen mislukt: server niet bereikbaar.");
    return;
  }
  if (res.status === 401) {
    requireLogin("Log in om een naam te bewaken.");
    return;
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    showWatchNotice(`Bewaking toevoegen mislukt: ${body.detail || "fout"}`);
    return;
  }
  const watch = (await res.json()).watchlist;
  localStorage.setItem("watchlist." + watch.id, JSON.stringify(entry));
  showWatchNotice(`Bewaking toegevoegd voor ${name}.`);
  await loadWatchlists();
}

async function deleteWatch(watchId) {
  let res;
  try {
    res = await fetch(`/api/watchlists/${encodeURIComponent(watchId)}`, {
      method: "DELETE",
      credentials: "same-origin",
    });
  } catch {
    showWatchNotice("Bewaking verwijderen mislukt: server niet bereikbaar.");
    return;
  }
  if (res.status === 401) {
    requireLogin("Log in om een bewaking te verwijderen.");
    return;
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    showWatchNotice(`Bewaking verwijderen mislukt: ${body.detail || "fout"}`);
    return;
  }
  localStorage.removeItem("watchlist." + watchId);
  await loadWatchlists();
}

async function loadWatchlists() {
  let res;
  try {
    res = await fetch("/api/watchlists", { credentials: "same-origin" });
  } catch {
    return;
  }
  if (!res.ok) return;
  const watchlists = (await res.json()).watchlists || [];
  if (!watchlists.length) {
    watchlistList.innerHTML = '<p class="muted">Nog geen bewaakte namen.</p>';
    return;
  }
  let allHits = [];
  let hitsRes;
  try {
    hitsRes = await fetch("/api/watchlists/hits", { credentials: "same-origin" });
  } catch {
    hitsRes = null;
  }
  if (hitsRes && hitsRes.ok) {
    allHits = (await hitsRes.json()).hits || [];
  }
  const byWatch = {};
  allHits.forEach((hit) => {
    (byWatch[hit.watchlist_id] = byWatch[hit.watchlist_id] || []).push(hit);
  });
  watchlistList.innerHTML = watchlists.map((w) => {
    const entry = readWatchEntry(w.id);
    const name = entry && entry.name ? entry.name : "";
    const nameHtml = name ? `<strong>${escapeHtml(name)}</strong>` : '<span class="muted">Naam onbekend</span>';
    const hits = byWatch[w.id] || [];
    const newCount = hits.filter((hit) => {
      const match = hit.match || {};
      return !(entry && entry.known && (match.id in entry.known));
    }).length;
    const badge = newCount ? `<span class="badge watchlist-badge">${newCount} nieuw</span>` : "";
    return `
      <div class="watchlist-item">
        <div class="watchlist-name">
          ${nameHtml}
          <span class="muted">${hits.length} hit(s)</span>
          ${badge}
        </div>
        <button type="button" class="watchlist-delete" data-id="${escapeHtml(w.id)}">Verwijder</button>
      </div>`;
  }).join("");
}

async function rescanWatchlists() {
  const keys = [];
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i);
    if (key && key.startsWith("watchlist.")) keys.push(key);
  }
  for (const key of keys) {
    const watchId = key.slice("watchlist.".length);
    const entry = readWatchEntry(watchId);
    if (!entry || !entry.name) continue;
    let res;
    try {
      res = await fetch(`/api/watchlists/${encodeURIComponent(watchId)}/rescan`, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(watchCriteria(entry)),
      });
    } catch {
      continue;
    }
    if (res.status === 404) {
      localStorage.removeItem(key);
      continue;
    }
    if (!res.ok) continue;
    const data = await res.json().catch(() => ({}));
    (data.hits || []).forEach((hit) => {
      const match = hit.match || {};
      if (!match.id || match.id in (entry.known || {})) return;
      entry.known[match.id] = match.score ?? 0;
      showWatchNotice(`Nieuwe match voor ${entry.name}: ${match.naam || match.id}`);
    });
    localStorage.setItem(key, JSON.stringify(entry));
  }
  await loadWatchlists();
}

async function handleDataVersion(version) {
  if (lastDataVersion === null) {
    lastDataVersion = version;
    return;
  }
  if (version === lastDataVersion || rescanning) return;
  lastDataVersion = version;
  rescanning = true;
  try {
    await rescanWatchlists();
  } finally {
    rescanning = false;
  }
}

async function init() {
  await loadStatus();
  await loadAuth();
  await maybeShowAuditLink();
  await loadWatchlists();
}

init();
window.setInterval(loadStatus, 60000);

watchBtn.addEventListener("click", addWatch);

watchlistList.addEventListener("click", (e) => {
  const btn = e.target.closest(".watchlist-delete");
  if (btn) deleteWatch(btn.dataset.id);
});

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
