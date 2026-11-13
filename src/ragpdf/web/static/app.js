/* Interfaccia di ragpdf: JavaScript semplice, senza librerie.
 *
 * Tutto è organizzato per PROGETTO: uno o più PDF con la stessa struttura, che condividono
 * primer e specifica e producono un CSV unico.
 *
 * Schermate (indirizzo dopo il #):
 *   #/                       elenco dei progetti, creazione di un nuovo progetto
 *   #/p/<id>/docs            PDF del progetto, note e pagine proprie di ciascun PDF
 *   #/p/<id>/setup           1. configurazione guidata (primer + specifica con Gemini)
 *   #/p/<id>/index           2. indicizzazione di tutti i PDF
 *   #/p/<id>/results         3. CSV unico, anomalie, pagina di ricerca
 *
 * Regole di sicurezza per l'utente:
 *   - ogni azione che usa Gemini (pulsanti con la stellina) chiede conferma e dice quante
 *     richieste partiranno; il tasto Invio non invia mai nulla;
 *   - i testi che scrivi (obiettivo, correzioni) restano bozze salvate nel browser finché
 *     non premi il pulsante di invio;
 *   - prima di inviare puoi vedere il prompt esatto;
 *   - modifiche manuali non salvate: conferma prima di cambiare pagina.
 */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const view = $("#view");
let pollTimer = null;
const MAX_CONTEXT_PAGES = 40;

// ------------------------------------------------------------------ utilità
function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
const enc = encodeURIComponent;

async function api(method, url, body, isForm = false) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    if (isForm) options.body = body;
    else { options.body = JSON.stringify(body); options.headers["Content-Type"] = "application/json"; }
  }
  const response = await fetch(url, options);
  const text = await response.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }
  if (!response.ok) {
    const error = new Error((data && data.detail) || `Errore ${response.status}`);
    error.status = response.status;
    error.data = data;
    throw error;
  }
  return data;
}

function toast(message, ms = 3500) {
  const t = $("#toast");
  t.textContent = message;
  t.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.remove("show"), ms);
}

function showError(error) {
  console.error(error);
  toast(error.message || String(error), 6000);
}

/** Dialogo di conferma: restituisce true se l'utente conferma. */
function confirmDialog({ title, html, ok = "Conferma", danger = false }) {
  const dialog = $("#confirm-dialog");
  $("#confirm-title").textContent = title;
  $("#confirm-body").innerHTML = html;
  const okButton = $("#confirm-ok");
  okButton.textContent = ok;
  okButton.className = danger ? "danger" : "";
  dialog.returnValue = "cancel";
  dialog.showModal();
  okButton.focus();
  return new Promise(resolve => dialog.addEventListener("close", () => resolve(dialog.returnValue === "ok"), { once: true }));
}

function showText(title, text, note = "") {
  $("#text-title").textContent = title;
  $("#text-note").textContent = note;
  $("#text-body").textContent = text;
  $("#text-dialog").showModal();
}

function showPage(docId, page, title = "") {
  $("#image-title").textContent = `${title ? title + " — " : ""}pagina ${page}`;
  $("#image-body").src = `/api/documents/${enc(docId)}/page/${page}`;
  $("#image-pdf").href = `/api/documents/${enc(docId)}/pdf#page=${page}`;
  $("#image-dialog").showModal();
}

/** Esegue un'azione disabilitando il pulsante, per evitare doppi clic. */
async function busy(button, action) {
  if (button) button.disabled = true;
  try { return await action(); }
  catch (e) { showError(e); }
  finally { if (button) button.disabled = false; }
}

// Bozze salvate nel browser (obiettivo, correzioni): non si perdono ricaricando la pagina.
const drafts = {
  get: key => { try { return localStorage.getItem("ragpdf:" + key) || ""; } catch { return ""; } },
  set: (key, value) => { try { localStorage.setItem("ragpdf:" + key, value); } catch { /* ignorato */ } },
  clear: key => { try { localStorage.removeItem("ragpdf:" + key); } catch { /* ignorato */ } },
};

/** "1-3, 5" -> [1,2,3,5]; lancia un errore con messaggio leggibile. */
function parsePages(text, total) {
  const result = new Set();
  for (const raw of String(text || "").split(",")) {
    const part = raw.trim();
    if (!part) continue;
    const m = part.match(/^(\d*)\s*-\s*(\d*)$/);
    let start, end;
    if (m) { start = m[1] ? +m[1] : 1; end = m[2] ? +m[2] : total; }
    else if (/^\d+$/.test(part)) { start = end = +part; }
    else throw new Error(`"${part}" non è valido (esempio: 1-4, 10, 38-41)`);
    if (start < 1 || end > total || start > end) throw new Error(`"${part}" è fuori dal documento (1-${total})`);
    for (let p = start; p <= end; p++) result.add(p);
  }
  return [...result].sort((a, b) => a - b);
}

function formatPages(pages) {
  const sorted = [...new Set(pages)].sort((a, b) => a - b);
  const parts = [];
  for (let i = 0; i < sorted.length; i++) {
    let j = i;
    while (j + 1 < sorted.length && sorted[j + 1] === sorted[j] + 1) j++;
    parts.push(i === j ? `${sorted[i]}` : `${sorted[i]}-${sorted[j]}`);
    i = j;
  }
  return parts.join(", ");
}

/** Tabella; con pageColumn/fileColumn le righe aprono la pagina del PDF corrispondente. */
function table(columns, rows, { hide = [], pageColumn = null, fileColumn = null, files = {}, docId = null } = {}) {
  const cols = columns.filter(c => !hide.includes(c));
  const head = cols.map(c => `<th>${esc(c)}</th>`).join("");
  const body = rows.map(r => {
    const page = pageColumn ? r[pageColumn] : null;
    const doc = fileColumn ? files[r[fileColumn]] : docId;
    const attrs = page && doc ? ` class="clickable" data-doc="${esc(doc)}" data-page="${esc(page)}" title="Clic: mostra la pagina"` : "";
    return `<tr${attrs}>${cols.map(c => `<td>${esc(r[c])}</td>`).join("")}</tr>`;
  }).join("");
  return `<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function bindPageClicks(root, titles = {}) {
  $$("[data-page][data-doc]", root).forEach(el => el.addEventListener("click", e => {
    e.stopPropagation();
    showPage(el.dataset.doc, el.dataset.page, titles[el.dataset.doc] || "");
  }));
}

// ------------------------------------------------------------------ barra di stato
async function refreshStatus() {
  try {
    const s = await api("GET", "/api/status");
    const parts = [`modello ${esc(s.model)}`];
    if (s.simulated) parts.unshift(`<span class="sim">MODALITÀ SIMULATA</span>`);
    else if (!s.keys) parts.unshift(`<span class="bad">nessuna chiave API nel file .env</span>`);
    else parts.unshift(`${s.keys} chiav${s.keys === 1 ? "e" : "i"} API`);
    if (s.usage && s.usage.requests) parts.push(esc(s.usage.text));
    $("#status").innerHTML = parts.join(" · ");
  } catch { $("#status").textContent = "server non raggiungibile"; }
}

// ------------------------------------------------------------------ lavori in corso
function stopPolling() { if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; } }

function elapsed(startedAt) {
  const seconds = Math.max(0, Math.round((Date.now() - Date.parse(startedAt)) / 1000));
  return seconds < 60 ? `${seconds} s` : `${Math.floor(seconds / 60)} min ${seconds % 60} s`;
}

/** Messaggi di servizio del lavoro (sovraccarico di Gemini, quote, rallentamenti), dal più recente. */
function jobEvents(events) {
  if (!events.length) return "";
  const latest = events[events.length - 1];
  const waiting = /sovraccarico|quota|rallento|errore temporaneo/i.test(latest.text);
  return `
    ${waiting ? `<div class="note warn small" style="margin-top:.75rem"><strong>${esc(latest.time)}</strong> ${esc(latest.text)}<br>
      <span class="muted">Il programma riprova da solo; puoi aspettare oppure premere <em>Ferma</em>.</span></div>` : ""}
    <details style="margin-top:.5rem"><summary class="small">Messaggi (${events.length})</summary>
      <ul class="small muted" style="margin:.25rem 0">${events.slice().reverse().map(e =>
        `<li><strong>${esc(e.time)}</strong> ${esc(e.text)}</li>`).join("")}</ul></details>`;
}

// ------------------------------------------------------------------ mini gioco durante l'attesa
const SNAKE_HIDDEN_KEY = "ragpdf:snake-hidden";
let activeGame = null;

function snakeHidden() { try { return localStorage.getItem(SNAKE_HIDDEN_KEY) === "1"; } catch { return false; } }
function setSnakeHidden(v) { try { localStorage.setItem(SNAKE_HIDDEN_KEY, v ? "1" : "0"); } catch { /* ignorato */ } }
function stopGame() { if (activeGame) { activeGame.destroy(); activeGame = null; } }
function jobSucceeded(j) { return j.status === "completato" && !(j.result && j.result.stopped_reason); }

/** Testi dell'animazione finale secondo il tipo di lavoro. */
function finaleTexts(j) {
  if (j.kind === "index") return {};                                   // quelli predefiniti
  if (j.kind === "grouping") return { title: "Raggruppamento pronto!", text: "Gruppi e progetti proposti ✦",
                                      labels: ["gruppo", "gruppo", "report"] };
  if (j.kind === "analysis") return { title: "Analisi pronta!", text: "La proposta di Gemini ti aspetta ✦",
                                      labels: ["descrizione", "campi", "pagine"] };
  return { title: "Gemini ha finito!", text: "Primer, specifica e anteprima appena sfornati ✦",
           labels: ["primer", "specifica", "anteprima"] };
}

/** Carica snake.js se la pagina non l'ha già fatto (es. index.html vecchia nella cache). */
function loadSnake() {
  if (window.GeminiSnake) return Promise.resolve(true);
  return new Promise(resolve => {
    const script = document.createElement("script");
    script.src = "/static/snake.js";
    script.onload = () => resolve(!!window.GeminiSnake);
    script.onerror = () => resolve(false);
    document.head.appendChild(script);
  });
}

/** Mostra un lavoro in corso e lo segue fino alla fine; poi chiama onDone(job).
 *  Durante l'attesa c'è Gemini Snake (si può nascondere). Se il lavoro finisce bene e il
 *  gioco è in corso (o finale=true, per l'indicizzazione), il serpente "produce" i risultati
 *  ed esplode: onDone viene chiamato solo alla fine dell'animazione. */
function watchJob(container, job, onDone, { finale = false } = {}) {
  stopPolling();
  stopGame();
  // struttura fissa: lo stato si aggiorna ogni secondo, il gioco resta com'è
  container.innerHTML = `
    <div class="panel">
      <div class="row"><strong id="job-label"></strong><span class="spacer"></span>
        <button class="secondary" id="job-game-toggle" type="button"></button>
        <button class="secondary" id="job-cancel" type="button">Ferma</button></div>
      <div id="job-status"></div>
      <div id="job-game"></div>
    </div>`;
  const status = $("#job-status", container), gameBox = $("#job-game", container);
  const toggle = $("#job-game-toggle", container);
  let current = job;

  const showGame = visible => {
    gameBox.hidden = !visible;
    toggle.textContent = visible ? "Nascondi il gioco" : "Gioca mentre aspetti";
    if (visible && !activeGame) {
      loadSnake().then(ok => {
        if (!ok || activeGame || gameBox.hidden || !gameBox.isConnected) return;
        activeGame = GeminiSnake.mount(gameBox);
        activeGame.setProgress(current.done, current.total);
      });
    }
  };
  toggle.addEventListener("click", () => { setSnakeHidden(!gameBox.hidden); showGame(gameBox.hidden); });
  showGame(!snakeHidden());

  $("#job-cancel", container).addEventListener("click", async () => {
    const ok = await confirmDialog({ title: "Fermare l'operazione?",
      html: `<p>Le attese in corso (sovraccarico di Gemini, quote) si interrompono subito; le richieste già
             inviate terminano, le altre non partono. Il lavoro fatto resta salvato e si può riprendere.</p>`,
      ok: "Ferma", danger: true });
    if (ok) await busy(null, () => api("POST", `/api/jobs/${current.id}/cancel`));
  });

  const render = j => {
    current = j;
    const pct = j.total ? Math.round(100 * j.done / j.total) : 0;
    const messagesOpen = !!(status.querySelector("details") || {}).open;   // si aggiorna ogni secondo
    $("#job-label", container).textContent = j.label;
    $("#job-cancel", container).disabled = !!j.cancel_requested;
    status.innerHTML = `
      <p class="muted small">${esc(j.message || "")}${j.total ? ` — ${j.done}/${j.total} pagine` : ""}
        · ${elapsed(j.started_at)}</p>
      <div class="progress ${j.total ? "" : "indeterminate"}"><div style="width:${pct}%"></div></div>
      ${jobEvents(j.events || [])}`;
    const details = status.querySelector("details");
    if (details) details.open = messagesOpen;
    if (activeGame) activeGame.setProgress(j.done, j.total);
  };
  const end = j => {
    container.innerHTML = "";
    stopGame();
    refreshStatus();
    if (j.status === "errore") toast(`Operazione non riuscita: ${j.error}`, 8000);
    else if (j.status === "annullato") toast("Operazione fermata. Il lavoro fatto è salvato.");
    onDone(j);
  };
  const tick = async () => {
    try {
      const j = await api("GET", `/api/jobs/${job.id}`);
      if (j.status === "in_corso") { render(j); pollTimer = setTimeout(tick, 1000); return; }
      const game = activeGame;
      if (jobSucceeded(j) && game && !gameBox.hidden && game.state !== "done" && (finale || game.playing)) {
        render(j);
        $("#job-cancel", container).disabled = true;
        toggle.disabled = true;
        game.finish(() => { if (activeGame === game) end(j); }, finaleTexts(j));
      } else end(j);
    } catch (e) { showError(e); }
  };
  render(job);
  pollTimer = setTimeout(tick, 700);
}

// ------------------------------------------------------------------ modifiche non salvate
function hasUnsavedEdits() {
  return $$("textarea[data-original]").some(t => t.value !== t.dataset.original);
}
window.addEventListener("beforeunload", e => { if (hasUnsavedEdits()) { e.preventDefault(); e.returnValue = ""; } });
let lastHash = location.hash;

// ------------------------------------------------------------------ router
async function route() {
  if (hasUnsavedEdits() && !window.confirm("Ci sono modifiche non salvate (primer, specifica o note). Uscire e perderle?")) {
    history.replaceState(null, "", lastHash);
    return;
  }
  lastHash = location.hash;
  stopPolling();
  stopGame();
  const parts = location.hash.replace(/^#\/?/, "").split("/").map(decodeURIComponent);
  try {
    $$(".nav a").forEach(a => a.classList.toggle("active",
      a.dataset.nav === (["biblioteca", "raggruppamenti"].includes(parts[0]) ? "library" : "projects")));
    if (parts[0] === "p" && parts[1]) await renderProject(parts[1], parts[2] || "docs");
    else if (parts[0] === "biblioteca" && parts[1] === "c" && parts[2]) await renderCatalog(parts[2]);
    else if (parts[0] === "biblioteca") await renderLibrary(parts.slice(1).join("/"));
    else if (parts[0] === "raggruppamenti" && parts[1] === "nuovo") await renderGroupingNew();
    else if (parts[0] === "raggruppamenti" && parts[1]) await renderGrouping(parts[1]);
    else if (parts[0] === "raggruppamenti") await renderGroupings();
    else await renderHome();
  } catch (e) {
    view.innerHTML = `<div class="panel note error">${esc(e.message)}</div><p><a href="#/">Torna all'elenco</a></p>`;
  }
}
window.addEventListener("hashchange", route);

/** Zona di caricamento: trascina o clicca; chiama onFiles(FileList). */
function dropZone(el, input, onFiles) {
  el.addEventListener("click", () => input.click());
  el.addEventListener("dragover", e => { e.preventDefault(); el.classList.add("over"); });
  el.addEventListener("dragleave", () => el.classList.remove("over"));
  el.addEventListener("drop", e => { e.preventDefault(); el.classList.remove("over"); if (e.dataTransfer.files.length) onFiles(e.dataTransfer.files); });
  input.addEventListener("change", () => input.files.length && onFiles(input.files));
}

/** Carica dei PDF in un progetto, uno alla volta, aggiornando un messaggio. */
async function uploadPdfs(projectId, files, statusEl) {
  const pdfs = [...files].filter(f => /\.pdf$/i.test(f.name));
  if (!pdfs.length) { toast("Scegli dei file PDF."); return 0; }
  let ok = 0;
  for (const [i, file] of pdfs.entries()) {
    statusEl.textContent = `Caricamento ${i + 1} di ${pdfs.length}: ${file.name}…`;
    const form = new FormData();
    form.append("file", file);
    try {
      const doc = await api("POST", `/api/projects/${enc(projectId)}/documents`, form, true);
      if (doc.already_present) toast(`${file.name} era già nel workspace: aggiunto al progetto.`);
      if (doc.scanned) toast(`${file.name} non contiene testo selezionabile (scansione): Gemini leggerà solo le immagini.`, 6000);
      ok++;
    } catch (e) { showError(e); }
  }
  return ok;
}

// ------------------------------------------------------------------ HOME
async function renderHome() {
  const projects = await api("GET", "/api/projects");
  view.innerHTML = `
    <h1>Progetti di indicizzazione</h1>
    <p class="muted">Un progetto raggruppa uno o più PDF con la stessa struttura: si configura una volta con Gemini
      e produce un unico CSV per tutti.</p>
    <div class="panel">
      <h3>Nuovo progetto</h3>
      <label for="new-name">Nome</label>
      <input type="text" id="new-name" placeholder="es. Cataloghi Finimpianti motori">
      <label>PDF del progetto <span class="muted small">(puoi aggiungerne altri anche dopo)</span></label>
      <div class="drop" id="drop">Trascina qui uno o più PDF, oppure <u>clicca per sceglierli</u>
        <input type="file" id="file" accept=".pdf,application/pdf" multiple hidden></div>
      <p class="hint" id="chosen"></p>
      <div class="row"><span class="spacer"></span><button id="create" type="button">Crea progetto</button></div>
    </div>
    <div class="panel">
      <h3>Progetti</h3>
      ${projects.length ? `<div class="table-wrap"><table>
        <thead><tr><th>Progetto</th><th>PDF</th><th>Pagine</th><th>Configurazione</th><th>Indicizzate</th><th></th></tr></thead>
        <tbody>${projects.map(p => {
          const conf = p.spec ? `<span class="badge ok">pronta</span>`
            : p.setup ? `<span class="badge warn">in corso (v${p.setup.round})</span>` : `<span class="badge">da fare</span>`;
          return `<tr class="clickable" data-open="${esc(p.id)}">
            <td><strong>${esc(p.name)}</strong>${p.job ? `<br><span class="badge warn">${esc(p.job.label)}…</span>` : ""}</td>
            <td>${p.documents.length}</td><td>${p.page_count}</td><td>${conf}</td>
            <td>${p.spec ? `${p.done}/${p.page_count}` : "—"}</td>
            <td><button class="secondary small" data-delete="${esc(p.id)}" type="button">Elimina</button></td></tr>`;
        }).join("")}</tbody></table></div>` : `<p class="muted">Nessun progetto: creane uno qui sopra.</p>`}
    </div>`;

  let chosen = [];
  dropZone($("#drop"), $("#file"), files => {
    chosen = [...files].filter(f => /\.pdf$/i.test(f.name));
    $("#chosen").textContent = chosen.length ? `${chosen.length} PDF: ${chosen.map(f => f.name).join(", ")}` : "";
    if (!$("#new-name").value && chosen.length === 1) $("#new-name").value = chosen[0].name.replace(/\.pdf$/i, "");
  });
  $("#create").addEventListener("click", e => busy(e.target, async () => {
    const name = $("#new-name").value.trim();
    if (!name) throw new Error("Dai un nome al progetto.");
    const project = await api("POST", "/api/projects", { name });
    if (chosen.length) await uploadPdfs(project.id, chosen, $("#chosen"));
    location.hash = `#/p/${enc(project.id)}/${chosen.length ? "setup" : "docs"}`;
  }));
  $$("[data-open]").forEach(tr => tr.addEventListener("click", e => {
    if (e.target.closest("button")) return;
    location.hash = `#/p/${enc(tr.dataset.open)}/docs`;
  }));
  $$("[data-delete]").forEach(b => b.addEventListener("click", async () => {
    const p = projects.find(x => x.id === b.dataset.delete);
    const ok = await confirmDialog({ title: "Eliminare il progetto?", danger: true, ok: "Elimina",
      html: `<p>Verrà eliminato il progetto <strong>${esc(p.name)}</strong>. I suoi ${p.documents.length} PDF
             restano nella biblioteca, insieme a primer, specifiche e CSV esportati.</p>` });
    if (ok) await busy(b, async () => { await api("DELETE", `/api/projects/${enc(p.id)}`); route(); });
  }));
}

// ------------------------------------------------------------------ PROGETTO
async function renderProject(pid, tab) {
  const project = await api("GET", `/api/projects/${enc(pid)}`);
  const tabs = [["docs", "Documenti"], ["settings", "Impostazioni"], ["setup", "1 · Configurazione"],
                ["index", "2 · Indicizzazione"], ["results", "3 · Risultati"]];
  view.innerHTML = `
    <p class="small"><a href="#/">&larr; Progetti</a></p>
    <h1>${esc(project.name)}</h1>
    <p class="muted small">${project.documents.length} PDF · ${project.page_count} pagine ·
      primer: ${esc(project.primer || "—")} · specifica: ${esc(project.spec || "—")}</p>
    <nav class="tabs">${tabs.map(([k, label]) =>
      `<a href="#/p/${enc(pid)}/${k}" class="${k === tab ? "active" : ""}">${label}</a>`).join("")}</nav>
    <div id="job"></div>
    <div id="tab"></div>`;
  const ctx = {
    project, pid, jobBox: $("#job"), box: $("#tab"),
    titles: Object.fromEntries(project.documents.map(d => [d.id, d.title])),
    files: Object.fromEntries(project.documents.map(d => [d.source_filename, d.id])),
  };
  ctx.refresh = () => renderProject(pid, tab);

  const job = await api("GET", `/api/projects/${enc(pid)}/job`);
  if (job && job.status === "in_corso") {
    ctx.box.innerHTML = `<p class="muted">Attendi la fine dell'operazione in corso…</p>`;
    // pagina riaperta durante un lavoro: a indicizzazione finita si va ai risultati
    watchJob(ctx.jobBox, job, j => {
      if (j.kind === "index" && jobSucceeded(j)) location.hash = `#/p/${enc(pid)}/results`;
      else ctx.refresh();
    }, { finale: job.kind === "index" });
    return;
  }
  if (!project.documents.length && !["docs", "settings"].includes(tab)) {
    ctx.box.innerHTML = `<div class="panel note">Il progetto non contiene PDF: <a href="#/p/${enc(pid)}/docs">aggiungili</a>.</div>`;
    return;
  }
  if (tab === "setup") await renderSetup(ctx);
  else if (tab === "index") await renderIndex(ctx);
  else if (tab === "results") await renderResults(ctx);
  else if (tab === "settings") await renderSettings(ctx);
  else renderDocs(ctx);
}

// ------------------------------------------------------------------ DOCUMENTI
function renderDocs(ctx) {
  const { project, pid, box } = ctx;
  const changes = project.library_changes || [];
  box.innerHTML = `
    ${changes.length ? `<div class="panel note warn">
      <strong>La biblioteca è cambiata</strong> per ${changes.length} PDF: le collezioni scelte come pagine da indicizzare
      non coincidono più con quelle copiate nel progetto.
      <ul class="small">${changes.map(c => `<li><strong>${esc(c.title)}</strong>: nel progetto pagine ${esc(c.pages || "—")},
        nella biblioteca ${c.library_pages ? `pagine ${esc(c.library_pages)}` : "<em>nessuna</em>"}
        ${c.missing ? ` (${c.missing} collezion${c.missing === 1 ? "e eliminata" : "i eliminate"})` : ""}</li>`).join("")}</ul>
      <div class="row"><span class="hint">Le pagine fanno parte delle regole: aggiornandole, i PDF già indicizzati andranno rifatti.</span>
        <span class="spacer"></span><button id="lib-update" type="button">Aggiorna dalla biblioteca</button></div>
    </div>` : ""}
    <div class="panel">
      <h3>Aggiungi PDF</h3>
      <div class="drop" id="drop">Trascina qui uno o più PDF con la stessa struttura degli altri, oppure <u>clicca</u>
        <input type="file" id="file" accept=".pdf,application/pdf" multiple hidden></div>
      <div class="row" style="margin-top:.5rem"><button class="secondary" id="from-library" type="button">Scegli dalla biblioteca…</button>
        <span class="hint">I PDF caricati qui finiscono anche nella cartella principale della biblioteca.</span></div>
      <p class="hint" id="upload-status">${project.spec ? "I nuovi PDF useranno primer e specifica del progetto: poi indicizzali dalla scheda 2." : ""}</p>
    </div>
    <div class="panel">
      <h3>PDF del progetto</h3>
      ${project.documents.length ? `<p class="hint">Le <strong>note</strong> e le <strong>pagine proprie</strong> valgono solo per quel PDF
        (es. "in questo catalogo la versione è sotto il nome del motore", oppure pagine introduttive diverse).
        Le colonne del CSV restano le stesse per tutti. Cambiarle richiede di reindicizzare solo quel PDF.</p>
      ${project.documents.map(d => `
        <div class="panel" style="margin-bottom:8px">
          <div class="row">
            <img src="/api/documents/${enc(d.id)}/thumb/1" alt="" style="width:46px;border:1px solid var(--border);border-radius:4px" data-doc="${esc(d.id)}" data-page="1">
            <div style="flex:1;min-width:14rem"><a href="#/biblioteca/c/${enc(d.id)}" title="Apri la scheda nella biblioteca"><strong>${esc(d.title)}</strong></a>
              <br><span class="small muted">${esc(d.source_filename)} · ${d.page_count} pagine
              ${project.spec ? ` · indicizzate ${d.done}/${d.page_count}` : ""}${d.failed ? ` · <span class="badge error">${d.failed} fallite</span>` : ""}</span>
              ${d.library && d.library.description ? `<br><span class="small">${esc(d.library.description.length > 180 ? d.library.description.slice(0, 180) + "…" : d.library.description)}</span>` : ""}
              ${d.library && d.library.data.length ? `<br><span class="small muted">Dati presenti:</span> ${tagBadges(d.library.data)}` : ""}</div>
            <span class="spacer"></span>
            ${d.settings.notes ? '<span class="badge">note</span>' : ""}${d.settings.pages ? `<span class="badge" title="${d.settings.collections.length ? "Dalle collezioni della biblioteca" : "Scritte a mano"}">pagine ${esc(d.settings.pages)}${d.settings.collections.length ? " 📚" : ""}</span>` : ""}
            <button class="secondary" data-edit="${esc(d.id)}" type="button">Note e pagine</button>
            <button class="secondary" data-remove="${esc(d.id)}" type="button">Togli</button>
          </div>
          <div id="edit-${esc(d.id)}" hidden>
            <label>Note per questo PDF</label>
            <textarea data-notes="${esc(d.id)}" rows="3" placeholder="Istruzioni in più valide solo per questo PDF"></textarea>
            <label>Pagine da indicizzare</label>
            ${d.library && d.library.collections.length ? `<p class="hint">Spunta le collezioni della
              <a href="#/biblioteca/c/${enc(d.id)}">scheda</a> da indicizzare (di solito quelle <em>Dati</em>): le loro pagine vengono
              copiate nel progetto.</p>
            <div class="coll-pick">${[...d.library.collections].sort((x, y) => (y.tag === "dati") - (x.tag === "dati") || x.start - y.start).map(c => `
              <label class="row" style="font-weight:normal;margin:.1rem 0">
                <input type="checkbox" data-coll="${esc(d.id)}" value="${esc(c.id)}" data-start="${c.start}" data-end="${c.end}"
                  ${d.settings.collections.includes(c.id) ? "checked" : ""}>
                ${pageTagChip(c.tag, c.label)} <span>pagine ${c.start === c.end ? c.start : `${c.start}-${c.end}`}</span>
                ${c.comment ? `<span class="muted small">— ${esc(c.comment)}</span>` : ""}</label>`).join("")}</div>
            <label style="font-weight:normal" class="small">…oppure scrivile a mano <span class="muted">(vuoto = quelle della specifica)</span></label>`
            : `<p class="hint">La <a href="#/biblioteca/c/${enc(d.id)}">scheda</a> non ha collezioni: scrivi le pagine a mano (vuoto = quelle della specifica).</p>`}
            <input type="text" data-pages="${esc(d.id)}" placeholder="es. 5-">
            <div class="row" style="margin-top:.5rem"><span class="spacer"></span>
              <button data-save="${esc(d.id)}" type="button">Salva</button></div>
          </div>
        </div>`).join("")}` : `<p class="muted">Nessun PDF: aggiungine qui sopra.</p>`}
      ${project.documents.length ? `<div class="row"><span class="spacer"></span>
        <a href="#/p/${enc(pid)}/setup"><button type="button">${project.spec ? "Rivedi la configurazione" : "Avanti: configurazione"}</button></a></div>` : ""}
    </div>`;
  bindPageClicks(box, ctx.titles);
  if ($("#lib-update")) $("#lib-update").addEventListener("click", async e => {
    const indexed = changes.filter(c => (project.documents.find(d => d.id === c.doc_id) || {}).done);
    const ok = await confirmDialog({ title: "Aggiornare le pagine dalla biblioteca?", ok: "Aggiorna", html: `
      <p>Le pagine da indicizzare di ${changes.map(c => `<strong>${esc(c.title)}</strong>`).join(", ")} verranno ricopiate
        dalle collezioni scelte (quelle eliminate vengono tolte).</p>
      ${indexed.length ? `<p class="note warn">Già indicizzati, andranno rifatti: ${indexed.map(c => esc(c.title)).join(", ")}
        (solo questi PDF, dalla scheda 2).</p>` : ""}` });
    if (!ok) return;
    await busy(e.target, async () => {
      const r = await api("POST", `/api/projects/${enc(pid)}/library-update`, { docs: changes.map(c => c.doc_id) });
      toast(`Pagine aggiornate per ${r.updated.length} PDF.`);
      ctx.refresh();
    });
  });
  $("#from-library").addEventListener("click", async () => {
    const ids = await pickFromLibrary(project.documents.map(d => d.id));
    if (!ids.length) return;
    await busy(null, async () => {
      const r = await api("POST", `/api/projects/${enc(pid)}/documents/from-library`, { ids });
      toast(`${r.added.length} PDF aggiunti dalla biblioteca.`);
      ctx.refresh();
    });
  });
  dropZone($("#drop"), $("#file"), async files => {
    const n = await uploadPdfs(pid, files, $("#upload-status"));
    if (n) { toast(`${n} PDF aggiunti.`); ctx.refresh(); }
  });
  for (const d of project.documents) {
    const area = $(`[data-notes="${CSS.escape(d.id)}"]`), pages = $(`[data-pages="${CSS.escape(d.id)}"]`);
    area.value = d.settings.notes || "";
    area.dataset.original = area.value;
    pages.value = d.settings.pages || "";
    const boxes = $$(`[data-coll="${CSS.escape(d.id)}"]`);
    const chosen = () => boxes.filter(b => b.checked);
    // spuntare le collezioni scrive le pagine; scriverle a mano toglie le spunte
    boxes.forEach(b => b.addEventListener("change", () => {
      const list = [];
      chosen().forEach(x => { for (let p = +x.dataset.start; p <= +x.dataset.end; p++) list.push(p); });
      pages.value = formatPages(list);
    }));
    pages.addEventListener("input", () => boxes.forEach(b => { b.checked = false; }));
    $(`[data-edit="${CSS.escape(d.id)}"]`).addEventListener("click", () => {
      const panel = $(`#edit-${CSS.escape(d.id)}`);
      panel.hidden = !panel.hidden;
    });
    $(`[data-save="${CSS.escape(d.id)}"]`).addEventListener("click", e => busy(e.target, async () => {
      const collections = chosen().map(b => b.value);
      if (!collections.length && pages.value.trim()) parsePages(pages.value, d.page_count);
      await api("PUT", `/api/projects/${enc(pid)}/documents/${enc(d.id)}/settings`,
        { notes: area.value, pages: pages.value, collections });
      area.dataset.original = area.value;
      toast("Salvato. Se il PDF era già indicizzato, andrà rifatto (scheda 2).");
      ctx.refresh();
    }));
    $(`[data-remove="${CSS.escape(d.id)}"]`).addEventListener("click", async e => {
      const ok = await confirmDialog({ title: "Togliere il PDF dal progetto?", danger: true, ok: "Togli",
        html: `<p><strong>${esc(d.title)}</strong> verrà tolto dal progetto. Il PDF e i risultati già ottenuti
               restano nella biblioteca.</p>` });
      if (ok) await busy(e.target, async () => { await api("DELETE", `/api/projects/${enc(pid)}/documents/${enc(d.id)}`); ctx.refresh(); });
    });
  }
}

// ------------------------------------------------------------------ 1. CONFIGURAZIONE
async function renderSetup(ctx, { forceForm = false } = {}) {
  const data = await api("GET", `/api/projects/${enc(ctx.pid)}/setup`);
  const session = data.session;
  if (!session || forceForm) return renderSetupForm(ctx, data);
  if (!session.round) return renderSetupReview(ctx, session);
  return renderSetupRound(ctx, session);
}

function steps(current) {
  const names = ["Pagine e obiettivo", "Invio a Gemini", "Prova e correzioni", "Salvataggio"];
  return `<div class="steps">${names.map((n, i) =>
    `<span class="${i + 1 === current ? "current" : i + 1 < current ? "done" : ""}">${i + 1}. ${n}</span>`).join("")}</div>`;
}

/** Selezione di pagine su più PDF: Map(docId -> Set(pagine)). */
function selectionFrom(pairs) {
  const sel = new Map();
  for (const [d, p] of pairs) { if (!sel.has(d)) sel.set(d, new Set()); sel.get(d).add(+p); }
  return sel;
}
const selectionSize = sel => [...sel.values()].reduce((n, s) => n + s.size, 0);
const selectionPairs = (sel, docs) => docs.flatMap(d => [...(sel.get(d.id) || [])].sort((a, b) => a - b).map(p => [d.id, p]));
function selectionText(sel, docs) {
  return docs.filter(d => sel.get(d.id) && sel.get(d.id).size)
    .map(d => docs.length > 1 ? `${d.title}: ${formatPages([...sel.get(d.id)])}` : formatPages([...sel.get(d.id)])).join(" · ");
}

async function renderSetupForm(ctx, data) {
  const { project, pid, box } = ctx;
  const docs = project.documents;
  const library = await api("GET", "/api/library");
  const session = data.session;
  const canReuse = library.primers.length && library.specs.length;
  const goalKey = `goal:${pid}`;
  let selection = selectionFrom(session ? session.state.context_pages : data.suggested_pages);
  let active = docs[0].id;
  let reference = null, lastClicked = null;

  box.innerHTML = `
    ${steps(1)}
    ${session && session.round ? `<div class="note warn">Esiste già una configurazione (versione ${session.state.current_round}).
       Continuando la sostituirai. <a href="#" id="back-to-session">Torna a quella esistente</a></div>` : ""}
    ${canReuse ? `<div class="panel">
      <h3>Da dove partire</h3>
      <label class="row" style="font-weight:normal"><input type="radio" name="mode" value="new" checked> Da zero: Gemini analizza i PDF del progetto</label>
      <label class="row" style="font-weight:normal"><input type="radio" name="mode" value="existing"> Da primer e specifica esistenti (cataloghi dello stesso tipo/editore)</label>
      <div id="existing" hidden class="row">
        <div style="flex:1"><label>Primer</label><select id="from-primer">${library.primers.map(p => `<option>${esc(p)}</option>`).join("")}</select></div>
        <div style="flex:1"><label>Specifica</label><select id="from-spec">${library.specs.map(p => `<option>${esc(p)}</option>`).join("")}</select></div>
      </div></div>` : ""}
    <div class="panel">
      <h3>Pagine da mostrare a Gemini</h3>
      <p class="hint">Scegli le pagine che servono a <strong>capire</strong> i cataloghi: indice, legende o "come leggere",
        e 2-3 pagine tipo per ogni layout diverso${docs.length > 1 ? ", <strong>anche da PDF diversi</strong>" : ""}.
        Clic per selezionare, <kbd>Maiusc</kbd>+clic per un intervallo, doppio clic per ingrandire. Massimo ${MAX_CONTEXT_PAGES}.</p>
      ${docs.length > 1 ? `<div class="tabs" id="doc-tabs" style="margin:.5rem 0">${docs.map(d =>
        `<a href="#" data-doc-tab="${esc(d.id)}">${esc(d.title)} <span class="badge" data-count="${esc(d.id)}"></span></a>`).join("")}</div>` : ""}
      <div class="row"><input type="text" id="pages-text" style="flex:1" placeholder="pagine di questo PDF, es. 1-4, 38-41">
        <button class="secondary" id="pages-suggest" type="button">Proposta automatica</button>
        <button class="secondary" id="pages-clear" type="button">Nessuna</button></div>
      <p class="hint" id="pages-count"></p>
      <div class="thumbs" id="thumbs"></div>
    </div>
    <div class="panel" id="goal-panel">
      ${project.has_table ? `
      <h3>La tabella del progetto</h3>
      <p class="hint">Decisa nelle <a href="#/p/${enc(pid)}/settings">Impostazioni</a>: Gemini dovrà usare esattamente queste colonne.</p>
      ${project.row ? `<p><strong>Una riga =</strong> ${esc(project.row)}</p>` : ""}
      ${project.fields.length ? `<p class="small">${project.fields.map((f, i) => `${i + 1}. <strong>${esc(f.name)}</strong>${f.description ? ` — ${esc(f.description)}` : ""}`).join("<br>")}</p>` : ""}
      <label for="goal">Altre indicazioni per questa configurazione <span class="muted small">(facoltative)</span></label>`
      : `
      <h3>Cosa vuoi ottenere</h3>
      <p class="hint">Descrivi il CSV a parole: cosa è una riga, quali colonne, regole particolari.
        Puoi lasciarlo vuoto: Gemini proporrà l'indicizzazione più utile. Per decidere tu le colonne usa le
        <a href="#/p/${enc(pid)}/settings">Impostazioni</a> del progetto.</p>`}
      <textarea id="goal" rows="5" placeholder="Es. Una riga per ogni coppia codice FIN / codice OEM di ogni motore, con costruttore, motore, cilindri, alesaggio, corsa e versione dall'intestazione. Se manca il codice OEM lascia vuoto."></textarea>
      <label>CSV di riferimento <span class="muted small">(facoltativo)</span></label>
      <p class="hint">Un indice già fatto: Gemini ne copia colonne e stile. Se contiene righe di questi PDF
        (stesso nome file nella colonna del file d'origine), ogni prova viene confrontata con esso.</p>
      <div class="row"><input type="file" id="reference" accept=".csv,text/csv"><span id="reference-info" class="small muted"></span></div>
    </div>
    <details class="panel"><summary>Opzioni avanzate</summary>
      <label>Nome di primer e specifica</label>
      <input type="text" id="name" placeholder="${esc(pid)}">
      <p class="hint">Di default l'identificativo del progetto (partendo da file esistenti: i loro nomi).</p>
    </details>
    <div class="row"><span class="spacer"></span><button id="next" type="button">Avanti: rivedi prima dell'invio</button></div>`;

  const thumbs = $("#thumbs"), text = $("#pages-text"), count = $("#pages-count");
  const activeDoc = () => docs.find(d => d.id === active);
  const pagesOf = id => { if (!selection.has(id)) selection.set(id, new Set()); return selection.get(id); };

  function drawThumbs() {
    const d = activeDoc();
    thumbs.innerHTML = Array.from({ length: d.page_count }, (_, i) => i + 1).map(p =>
      `<div class="thumb" data-p="${p}"><img loading="lazy" src="/api/documents/${enc(d.id)}/thumb/${p}" alt="pagina ${p}">${p}</div>`).join("");
    $$("[data-doc-tab]").forEach(a => a.classList.toggle("active", a.dataset.docTab === active));
    sync();
  }
  function sync(fromText = false) {
    const own = pagesOf(active);
    if (!fromText) text.value = formatPages([...own]);
    $$(".thumb", thumbs).forEach(t => t.classList.toggle("selected", own.has(+t.dataset.p)));
    $$("[data-count]").forEach(b => { const n = (selection.get(b.dataset.count) || new Set()).size; b.textContent = n || ""; });
    const n = selectionSize(selection);
    count.innerHTML = (n > MAX_CONTEXT_PAGES ? `<span class="badge error">${n} pagine: troppe (massimo ${MAX_CONTEXT_PAGES})</span>`
      : `${n} pagine selezionate`) + (n ? ` — ${esc(selectionText(selection, docs))}` : "");
  }
  thumbs.addEventListener("click", e => {
    const t = e.target.closest(".thumb"); if (!t) return;
    const p = +t.dataset.p, own = pagesOf(active);
    if (e.shiftKey && lastClicked) {
      const [a, b] = [Math.min(lastClicked, p), Math.max(lastClicked, p)];
      for (let q = a; q <= b; q++) own.add(q);
    } else own.has(p) ? own.delete(p) : own.add(p);
    lastClicked = p; sync();
  });
  thumbs.addEventListener("dblclick", e => { const t = e.target.closest(".thumb"); if (t) showPage(active, t.dataset.p, activeDoc().title); });
  text.addEventListener("change", () => {
    try { selection.set(active, new Set(parsePages(text.value, activeDoc().page_count))); sync(true); }
    catch (err) { toast(err.message); }
  });
  $$("[data-doc-tab]").forEach(a => a.addEventListener("click", e => { e.preventDefault(); active = a.dataset.docTab; lastClicked = null; drawThumbs(); }));
  $("#pages-suggest").addEventListener("click", () => { selection = selectionFrom(data.suggested_pages); sync(); });
  $("#pages-clear").addEventListener("click", () => { selection = new Map(); sync(); });
  drawThumbs();

  const goal = $("#goal");
  goal.value = drafts.get(goalKey) || (session ? session.state.goal : "");
  goal.addEventListener("input", () => drafts.set(goalKey, goal.value));

  $("#reference").addEventListener("change", async e => {
    const file = e.target.files[0]; if (!file) { reference = null; return; }
    const form = new FormData(); form.append("file", file);
    try {
      reference = await api("POST", "/api/references", form, true);
      $("#reference-info").textContent = `${reference.rows} righe, colonne: ${reference.columns.join(", ")}`;
    } catch (err) { reference = null; e.target.value = ""; showError(err); }
  });

  const modeExisting = () => canReuse && $("input[name=mode]:checked").value === "existing";
  $$("input[name=mode]").forEach(r => r.addEventListener("change", () => {
    $("#existing").hidden = !modeExisting();
    $("#goal-panel").hidden = modeExisting();
  }));
  const back = $("#back-to-session");
  if (back) back.addEventListener("click", e => { e.preventDefault(); renderSetup(ctx); });

  $("#next").addEventListener("click", e => busy(e.target, async () => {
    const n = selectionSize(selection);
    if (!n && !modeExisting()) throw new Error("Seleziona almeno una pagina.");
    if (n > MAX_CONTEXT_PAGES) throw new Error(`Hai selezionato più di ${MAX_CONTEXT_PAGES} pagine.`);
    if (session && session.round) {
      const ok = await confirmDialog({ title: "Sostituire la configurazione esistente?", danger: true, ok: "Sostituisci",
        html: `<p>La configurazione attuale (${session.state.last_round} versioni) verrà sostituita da una nuova.
               I primer e le specifiche già <em>salvati</em> nel workspace non vengono toccati.</p>` });
      if (!ok) return;
    }
    const body = { context_pages: selectionPairs(selection, docs), goal: goal.value,
                   reference: reference ? reference.path : null, name: $("#name").value.trim() || null };
    if (modeExisting()) { body.from_primer = $("#from-primer").value; body.from_spec = $("#from-spec").value; }
    const created = await api("POST", `/api/projects/${enc(pid)}/setup`, body);
    if (created.round) toast("Partenza da primer e specifica esistenti: esegui una prova per verificarli su questi PDF.", 6000);
    renderSetup(ctx);
  }));
}

function renderSetupReview(ctx, session) {
  const { pid, box } = ctx;
  const st = session.state;
  box.innerHTML = `
    ${steps(2)}
    <div class="panel">
      <h3>Riepilogo prima dell'invio</h3>
      <p><strong>Pagine mostrate a Gemini:</strong> ${esc(st.context_pages_text)} (${st.context_pages.length} pagine)</p>
      <div class="thumbs" style="max-height:10rem">${st.context_pages.map(([d, p]) =>
        `<div class="thumb" data-doc="${esc(d)}" data-page="${p}"><img loading="lazy" src="/api/documents/${enc(d)}/thumb/${p}" alt="">${ctx.project.documents.length > 1 ? esc(ctx.titles[d]) + " " : ""}p.${p}</div>`).join("")}</div>
      <p><strong>Obiettivo:</strong> ${st.goal ? esc(st.goal) : "<em>non indicato: Gemini proporrà l'indicizzazione</em>"}</p>
      <p><strong>CSV di riferimento:</strong> ${esc(st.reference || "nessuno")}</p>
      <div class="note">Verrà inviata <strong>1 richiesta</strong> a Gemini con l'estratto PDF delle pagine scelte; poi una
        prova di estrazione su alcune pagine, almeno una per PDF (<strong>circa ${Math.max(2, ctx.project.documents.length)}-${Math.max(4, ctx.project.documents.length)} richieste</strong>).</div>
      <div class="row">
        <button class="secondary" id="edit" type="button">&larr; Modifica</button>
        <button class="secondary" id="prompt" type="button">Mostra il prompt</button>
        <span class="spacer"></span>
        <button class="gemini" id="send" type="button">Invia a Gemini</button>
      </div>
    </div>`;
  bindPageClicks(box, ctx.titles);
  $("#edit").addEventListener("click", () => renderSetup(ctx, { forceForm: true }));
  $("#prompt").addEventListener("click", e => busy(e.target, async () => {
    const p = await api("GET", `/api/projects/${enc(pid)}/setup/prompt?kind=initial`);
    showText("Prompt che verrà inviato a Gemini", p.prompt, `Allegato: ${p.attachment}`);
  }));
  $("#send").addEventListener("click", async e => {
    const ok = await confirmDialog({ title: "Inviare a Gemini?", ok: "Invia",
      html: `<p>1 richiesta di analisi con ${st.context_pages.length} pagine, poi la prova di estrazione.</p>` });
    if (!ok) return;
    await busy(e.target, async () => {
      const job = await api("POST", `/api/projects/${enc(pid)}/setup/start`);
      box.innerHTML = "";
      watchJob(ctx.jobBox, job, () => renderSetup(ctx));
    });
  });
}

function renderSetupRound(ctx, session) {
  const { pid, box, project } = ctx;
  const st = session.state, r = session.round, spec = r.spec, preview = r.preview;
  const feedbackKey = `feedback:${pid}`;
  const multi = project.documents.length > 1;

  box.innerHTML = `
    ${steps(st.saved_at ? 4 : 3)}
    <div class="panel row">
      <div><strong>Versione ${r.number}</strong> <span class="muted small">di ${st.last_round}
        ${st.saved_at ? " · configurazione salvata" : " · non ancora salvata"}</span></div>
      <span class="spacer"></span>
      <button class="secondary" id="new-setup" type="button">Ricomincia</button>
      <button id="save" type="button">Salva configurazione</button>
    </div>
    <div class="grid2">
      <div>
        <div class="panel">
          <h3>Proposta di Gemini</h3>
          ${r.summary ? `<p style="white-space:pre-wrap">${esc(r.summary)}</p>` : ""}
          ${r.questions.length ? `<div class="note"><strong>Domande di Gemini</strong> (rispondi nella correzione):
            <ul>${r.questions.map(q => `<li>${esc(q)}</li>`).join("")}</ul></div>` : ""}
          ${spec.valid ? `
            <p><strong>Una riga del CSV =</strong> ${esc(spec.row)}</p>
            ${spec.pages ? `<p class="small">Pagine che verranno indicizzate: <strong>${esc(spec.pages)}</strong></p>` : ""}
            <div class="table-wrap" style="max-height:18rem"><table><thead><tr><th>Colonna</th><th>Descrizione</th></tr></thead><tbody>
              ${spec.columns.map(c => `<tr><td><strong>${esc(c.name)}</strong>
                ${c.required ? ' <span class="badge">obbligatoria</span>' : ""}
                ${c.context ? ' <span class="badge">intestazione</span>' : ""}</td><td>${esc(c.description)}</td></tr>`).join("")}
            </tbody></table></div>` : `<div class="note error">Specifica non valida: ${esc(spec.error)}</div>`}
          ${r.columns_check && !r.columns_check.ok ? `${columnsCheckNote(r.columns_check, "Questa versione della specifica")}
            <div class="row"><button class="secondary small" id="fix-columns" type="button">Chiedi a Gemini di correggerle</button></div>` : ""}
          ${multi && Object.keys(r.document_notes).length ? `<div class="note"><strong>Note per singoli PDF</strong><ul>
            ${Object.entries(r.document_notes).map(([d, n]) => `<li><strong>${esc(ctx.titles[d] || d)}</strong>: ${esc(n)}</li>`).join("")}</ul></div>` : ""}
        </div>
        <div class="panel">
          <h3>Correzione</h3>
          <p class="hint">Scrivi a parole cosa non va (es. "TIPO_COMPONENTE in maiuscolo", "a pagina 39 manca l'ENGINE KIT"${multi ? ', "nel PDF Kubota la versione è sotto il nome"' : ""}).
            Il testo resta una bozza finché non premi <em>Invia correzione</em>.</p>
          <textarea id="feedback" rows="5"></textarea>
          <div class="row" style="margin-top:.5rem">
            <button class="secondary" id="feedback-prompt" type="button">Mostra il prompt</button>
            <span class="spacer"></span>
            <button class="gemini" id="feedback-send" type="button">Invia correzione</button>
          </div>
        </div>
        <div class="panel">
          <h3>Versioni</h3>
          <ul class="versions">${session.rounds.map(v => `<li data-round="${v.round}" class="${v.round === r.number ? "current" : ""}">
            <strong>v${v.round}</strong> · ${esc(v.origin)}${v.feedback ? ` — <span class="muted">${esc(v.feedback.slice(0, 90))}</span>` : ""}</li>`).join("")}</ul>
          <p class="hint">Clic su una versione per tornarci (nessuna richiesta a Gemini).</p>
        </div>
        ${multi ? `<details class="panel"><summary>Note per singoli PDF (modificabili)</summary>
          <p class="hint">Istruzioni in più valide solo per quel PDF; le colonne restano le stesse.</p>
          ${project.documents.map(d => `<label>${esc(d.title)}</label><textarea data-doc-note="${esc(d.id)}" rows="2"></textarea>`).join("")}
          <div class="row"><span class="spacer"></span><button id="notes-save" type="button" disabled>Salva come nuova versione</button></div>
        </details>` : ""}
        <details class="panel"><summary>Primer (modificabile)</summary>
          <textarea id="primer" class="mono tall"></textarea>
          <div class="row"><span class="spacer"></span>
            <button class="secondary" id="primer-reset" type="button">Annulla modifiche</button>
            <button id="primer-save" type="button" disabled>Salva come nuova versione</button></div>
        </details>
        <details class="panel"><summary>Specifica YAML (modificabile)</summary>
          <textarea id="spec" class="mono tall"></textarea>
          <div class="row"><span class="spacer"></span>
            <button class="secondary" id="spec-reset" type="button">Annulla modifiche</button>
            <button id="spec-save" type="button" disabled>Salva come nuova versione</button></div>
        </details>
      </div>
      <div>
        <div class="panel">
          <h3>Prova di estrazione</h3>
          <div class="row">
            <label for="preview-pages" style="margin:0">Pagine</label>
            <input type="text" id="preview-pages" style="flex:1" value="${esc(st.preview_pages_text)}">
            <button class="gemini" id="preview-run" type="button">Rifai la prova</button>
          </div>
          <p class="hint">${multi ? 'Formato: <code>Alfa: 3-4; Beta: 12</code> (basta una parte del nome). Almeno una pagina per PDF viene sempre provata. '
            : ""}La prova usa le stesse regole dell'indicizzazione vera.</p>
          ${preview ? renderPreview(preview, spec, ctx) : `<div class="note">Nessuna prova per questa versione: premi <em>Rifai la prova</em>.</div>`}
        </div>
      </div>
    </div>`;

  bindPageClicks(box, ctx.titles);

  const feedback = $("#feedback");
  feedback.value = drafts.get(feedbackKey);
  feedback.addEventListener("input", () => drafts.set(feedbackKey, feedback.value));
  $("#feedback-prompt").addEventListener("click", e => busy(e.target, async () => {
    const p = await api("GET", `/api/projects/${enc(pid)}/setup/prompt?kind=revise&feedback=${enc(feedback.value)}`);
    showText("Prompt di revisione che verrà inviato", p.prompt, `Allegato: ${p.attachment}`);
  }));
  $("#feedback-send").addEventListener("click", async e => {
    if (!feedback.value.trim()) return toast("Scrivi prima cosa correggere.");
    const ok = await confirmDialog({ title: "Inviare la correzione a Gemini?", ok: "Invia",
      html: `<p>1 richiesta di revisione, poi una prova su ${Math.max(st.preview_pages.length, project.documents.length)} pagine.</p>
             <pre>${esc(feedback.value)}</pre>` });
    if (!ok) return;
    await busy(e.target, async () => {
      const job = await api("POST", `/api/projects/${enc(pid)}/setup/revise`, { feedback: feedback.value });
      drafts.clear(feedbackKey);
      box.innerHTML = "";
      watchJob(ctx.jobBox, job, () => renderSetup(ctx));
    });
  });

  $("#preview-run").addEventListener("click", async e => {
    const text = $("#preview-pages").value.trim();
    if (!text) return toast("Indica le pagine da provare.");
    const ok = await confirmDialog({ title: "Rifare la prova?", ok: "Esegui",
      html: `<p>Una richiesta a Gemini per ogni pagina indicata (${esc(text)}), più una pagina per ogni PDF eventualmente non incluso.</p>` });
    if (!ok) return;
    await busy(e.target, async () => {
      const job = await api("POST", `/api/projects/${enc(pid)}/setup/preview`, { pages: text });
      box.innerHTML = "";
      watchJob(ctx.jobBox, job, () => renderSetup(ctx));
    });
  });

  $$("[data-round]").forEach(li => li.addEventListener("click", async () => {
    const n = +li.dataset.round;
    if (n === r.number) return;
    const ok = await confirmDialog({ title: `Tornare alla versione ${n}?`, ok: "Torna",
      html: "<p>Nessuna richiesta a Gemini. Le altre versioni restano disponibili.</p>" });
    if (ok) await busy(null, async () => { await api("POST", `/api/projects/${enc(pid)}/setup/round/${n}`); renderSetup(ctx); });
  }));

  for (const [kind, original] of [["primer", r.primer], ["spec", r.spec_text]]) {
    const area = $(`#${kind}`), save = $(`#${kind}-save`);
    area.value = original;
    area.dataset.original = original;
    area.addEventListener("input", () => { save.disabled = area.value === original; });
    $(`#${kind}-reset`).addEventListener("click", () => { area.value = original; save.disabled = true; });
    save.addEventListener("click", e => busy(e.target, async () => {
      await api("PUT", `/api/projects/${enc(pid)}/setup/${kind}`, { text: area.value });
      area.dataset.original = area.value;
      toast("Modifica salvata come nuova versione. Esegui una prova per verificarla.");
      renderSetup(ctx);
    }));
  }
  if (multi) {
    const areas = $$("[data-doc-note]"), save = $("#notes-save");
    areas.forEach(a => { a.value = r.document_notes[a.dataset.docNote] || ""; a.dataset.original = a.value;
      a.addEventListener("input", () => { save.disabled = areas.every(x => x.value === x.dataset.original); }); });
    save.addEventListener("click", e => busy(e.target, async () => {
      const notes = Object.fromEntries(areas.map(a => [a.dataset.docNote, a.value]));
      await api("PUT", `/api/projects/${enc(pid)}/setup/notes`, { notes });
      areas.forEach(a => { a.dataset.original = a.value; });
      toast("Note salvate come nuova versione. Esegui una prova per verificarle.");
      renderSetup(ctx);
    }));
  }

  $("#new-setup").addEventListener("click", () => renderSetup(ctx, { forceForm: true }));
  $("#save").addEventListener("click", async e => {
    if (r.columns_check && !r.columns_check.ok) {
      const ok = await confirmDialog({ title: "Le colonne non coincidono", ok: "Salva comunque", danger: true,
        html: `${columnsCheckNote(r.columns_check, "Questa versione della specifica")}
               <p>Puoi chiedere a Gemini di correggerle, oppure cambiare le colonne nelle Impostazioni del progetto.</p>` });
      if (!ok) return;
    }
    saveSetup(ctx, session, e.target);
  });
  if ($("#fix-columns")) $("#fix-columns").addEventListener("click", () => {
    const fb = $("#feedback");
    fb.value = `Usa esattamente le colonne del progetto, con questi nomi e in quest'ordine, senza aggiungerne né toglierne: ${project.fields.map(f => f.name).join(", ")}.`
      + (fb.value.trim() ? `\n\n${fb.value.trim()}` : "");
    fb.dispatchEvent(new Event("input"));
    fb.focus();
  });
}

function renderPreview(p, spec, ctx) {
  const pageCol = spec.valid ? spec.page_column : null;
  const sourceCol = spec.valid ? spec.source_column : null;
  const perPage = p.rows_per_page.map(x =>
    `<span class="badge ${x.rows ? "ok" : "warn"}" data-doc="${esc(x.document)}" data-page="${esc(x.page)}" style="cursor:pointer"
       title="Mostra la pagina">${esc(x.label)}: ${x.rows} ${x.rows === 1 ? "riga" : "righe"}</span>`).join(" ");
  const c = p.comparison;
  const multi = ctx.project.documents.length > 1;
  return `
    <p>${perPage}</p>
    ${Object.keys(p.failed).length ? `<div class="note error">Pagine fallite: ${Object.entries(p.failed).map(([k, v]) => `${esc(k)} (${esc(v)})`).join("; ")}</div>` : ""}
    ${c ? `<div class="note"><strong>Confronto con il riferimento:</strong> copertura ${Math.round(c.recall * 100)}%,
      precisione ${Math.round(c.precision * 100)}%
      ${c.missing.length ? `<details><summary>${c.missing.length} righe mancanti</summary><ul class="small">${c.missing.map(x => `<li>${esc(x)}</li>`).join("")}</ul></details>` : ""}
      ${c.extra.length ? `<details><summary>${c.extra.length} righe in più</summary><ul class="small">${c.extra.map(x => `<li>${esc(x)}</li>`).join("")}</ul></details>` : ""}
      </div>` : p.comparison_note ? `<p class="hint">${esc(p.comparison_note)}</p>` : ""}
    ${p.rows.length ? table(p.columns, p.rows, { hide: sourceCol && !multi ? [sourceCol] : [], pageColumn: pageCol,
                                                 fileColumn: sourceCol, files: ctx.files, docId: ctx.project.documents[0].id })
                    : `<div class="note warn">Nessuna riga estratta.</div>`}
    <p class="hint">${p.total_rows} righe${pageCol ? " · clic su una riga per vedere la pagina" : ""} ·
      <a href="/api/projects/${enc(ctx.pid)}/setup/preview.csv">scarica il CSV di prova</a></p>
    ${p.anomalies.length ? `<details><summary>${p.anomalies.length} segnalazioni dei controlli</summary>
      <ul class="small">${p.anomalies.map(a => `<li>${esc(a)}</li>`).join("")}</ul></details>` : ""}`;
}

async function saveSetup(ctx, session, button, extra = {}) {
  const pid = enc(ctx.pid);
  if (!extra.overwrite && !extra.name) {
    const ok = await confirmDialog({ title: "Salvare la configurazione?", ok: "Salva",
      html: `<p>Primer <code>${esc(session.state.primer_name)}</code>, specifica <code>${esc(session.state.spec_name)}</code>
             ${ctx.project.documents.length > 1 ? "e note per i singoli PDF " : ""}(versione ${session.round.number})
             diventano le regole usate per indicizzare tutti i PDF del progetto.</p>` });
    if (!ok) return;
  }
  await busy(button, async () => {
    try {
      const res = await api("POST", `/api/projects/${pid}/setup/save`, extra);
      toast(res.reused_pages ? `Salvato. ${res.reused_pages} pagine della prova sono già contate come indicizzate.` : "Configurazione salvata.", 6000);
      location.hash = `#/p/${pid}/index`;
    } catch (e) {
      if (e.status !== 409 || !e.data || !e.data.conflicts) throw e;
      const choice = await conflictDialog(e.data);
      if (choice === "overwrite") await saveSetup(ctx, session, button, { overwrite: true });
      else if (choice) await saveSetup(ctx, session, button, { name: choice });
    }
  });
}

async function conflictDialog(info) {
  const overwrite = await confirmDialog({ title: "File già esistenti", ok: "Sovrascrivi", danger: true,
    html: `<p>Nel workspace esistono già, con contenuto diverso: <strong>${info.conflicts.map(esc).join(", ")}</strong>.</p>
           <p>Sovrascriverli? Scegli <em>Annulla</em> per salvare con un altro nome.</p>` });
  if (overwrite) return "overwrite";
  const name = window.prompt("Nome da usare per primer e specifica:", `${info.name}-2`);
  return name && name.trim() ? name.trim() : null;
}

// ------------------------------------------------------------------ 2. INDICIZZAZIONE
async function renderIndex(ctx) {
  const { pid, box } = ctx;
  let plan;
  try { plan = await api("GET", `/api/projects/${enc(pid)}/index/plan`); }
  catch (e) {
    box.innerHTML = `<div class="panel note warn">${esc(e.message)}</div>
      <p><a href="#/p/${enc(pid)}/setup">Vai alla configurazione</a></p>`;
    return;
  }
  const changed = plan.spec_changed;
  box.innerHTML = `
    ${columnsCheckNote(ctx.project.columns_check, "La specifica salvata")}
    <div class="panel">
      <h3>Indicizzazione di tutti i PDF</h3>
      <p>Specifica <code>${esc(plan.spec)}</code> · primer <code>${esc(plan.primer || "nessuno")}</code></p>
      <div class="table-wrap"><table><thead><tr><th>PDF</th><th>Pagine</th><th>Fatte</th><th>Da fare</th><th></th></tr></thead><tbody>
        ${plan.documents.map(d => `<tr><td><strong>${esc(d.title)}</strong>${d.has_notes ? ' <span class="badge">note</span>' : ""}</td>
          <td>${esc(d.pages_text)} (${d.pages})</td><td>${d.done}</td><td>${d.todo}</td>
          <td>${d.spec_changed ? '<span class="badge warn">regole cambiate: da rifare</span>' : d.failed ? `<span class="badge error">${d.failed} fallite</span>` : d.todo ? "" : '<span class="badge ok">completo</span>'}</td></tr>`).join("")}
      </tbody></table></div>
      <p style="margin-top:.75rem"><span class="badge ok">${plan.done} pagine fatte</span> <span class="badge">${plan.todo} da fare</span></p>
      ${changed.length ? `<div class="note warn">Per ${changed.length} PDF primer, specifica o note sono cambiati dopo l'indicizzazione:
        per non mescolare risultati ottenuti con regole diverse, quelle pagine vanno rifatte.</div>` : ""}
      ${plan.todo ? `<p>Tempo stimato: circa <strong>${plan.estimate_minutes} minuti</strong> (si adatta alle quote API).
          Puoi chiudere la pagina: il lavoro continua finché il server è acceso, e si riprende se lo fermi.</p>
        <div class="row"><span class="spacer"></span>
          <button class="gemini" id="start" type="button">Avvia l'indicizzazione (${plan.todo} pagine)</button></div>`
      : `<div class="note">Tutte le pagine sono indicizzate. <a href="#/p/${enc(pid)}/results">Vai ai risultati</a></div>`}
    </div>`;
  const start = $("#start");
  if (start) start.addEventListener("click", async () => {
    const ok = await confirmDialog({ title: "Avviare l'indicizzazione?", ok: "Avvia",
      html: `<p>${plan.todo} richieste a Gemini su ${plan.documents.filter(d => d.todo).length} PDF, circa ${plan.estimate_minutes} minuti.</p>
             ${changed.length ? `<p><strong>I risultati precedenti di ${changed.map(id => esc(ctx.titles[id] || id)).join(", ")} verranno sostituiti.</strong></p>` : ""}` });
    if (!ok) return;
    await busy(start, async () => {
      const job = await api("POST", `/api/projects/${enc(pid)}/index`, { force: changed.length > 0 });
      box.innerHTML = "";
      watchJob(ctx.jobBox, job, j => {
        if (jobSucceeded(j)) location.hash = `#/p/${enc(pid)}/results`;
        else { if (j.result && j.result.stopped_reason) toast(j.result.stopped_reason, 8000); renderIndex(ctx); }
      }, { finale: true });
    });
  });
}

// ------------------------------------------------------------------ 3. RISULTATI
async function renderResults(ctx) {
  const { pid, box } = ctx;
  let res;
  try { res = await api("GET", `/api/projects/${enc(pid)}/results`); }
  catch (e) { box.innerHTML = `<div class="panel note warn">${esc(e.message)}</div>`; return; }
  if (!res.available) {
    box.innerHTML = `<div class="panel note">Nessun risultato: <a href="#/p/${enc(pid)}/index">avvia l'indicizzazione</a>.</div>`;
    return;
  }
  const s = res.stats;
  box.innerHTML = `
    <div class="panel">
      <h3>CSV unico del progetto</h3>
      <p><span class="badge ok">${s.righe_finali} righe</span>
        <span class="badge">${s.documenti_indicizzati}/${s.documenti_totali} PDF</span>
        ${s.pagine_da_elaborare ? `<span class="badge warn">${s.pagine_da_elaborare} pagine non ancora elaborate</span>` : ""}
        ${s.errori ? `<span class="badge error">${s.errori} errori</span>` : ""}
        ${s.avvisi ? `<span class="badge warn">${s.avvisi} avvisi</span>` : ""}</p>
      ${res.not_indexed.length ? `<div class="note warn">Non ancora indicizzati: ${res.not_indexed.map(id => esc(ctx.titles[id] || id)).join(", ")}.
        <a href="#/p/${enc(pid)}/index">Indicizzali</a>.</div>` : ""}
      ${res.stale.length ? `<div class="note warn">Regole cambiate dopo l'indicizzazione per: ${res.stale.map(id => esc(ctx.titles[id] || id)).join(", ")}.
        <a href="#/p/${enc(pid)}/index">Rifalli</a>.</div>` : ""}
      <div class="row">
        <a href="/api/projects/${enc(pid)}/download/csv"><button type="button">Scarica il CSV unico</button></a>
        ${res.anomalies_total ? `<a href="/api/projects/${enc(pid)}/download/anomalie"><button class="secondary" type="button">Scarica le anomalie</button></a>` : ""}
        <a href="/api/projects/${enc(pid)}/search" target="_blank" rel="noopener"><button class="secondary" type="button">Apri la pagina di ricerca</button></a>
        <span class="spacer"></span>
        <button class="secondary" id="regen" type="button">Rigenera (senza Gemini)</button>
      </div>
      <p class="hint">La pagina di ricerca è anche salvata come file autonomo in <code>data/exports/${esc(pid)}/ricerca.html</code>:
        si apre con un doppio clic, senza bisogno del server.</p>
    </div>
    <div class="panel"><h3>Per PDF</h3>
      <div class="table-wrap"><table><thead><tr><th>PDF</th><th>Righe</th><th>Errori</th><th>Avvisi</th><th></th></tr></thead><tbody>
        ${res.documents.map(d => `<tr><td><strong>${esc(d.title)}</strong>${d.stale ? ' <span class="badge warn">regole cambiate</span>' : ""}</td>
          <td>${d.rows}</td><td>${d.errors}</td><td>${d.warnings}</td>
          <td><a href="/api/projects/${enc(pid)}/download/documento?doc=${enc(d.id)}">CSV di questo PDF</a></td></tr>`).join("")}
      </tbody></table></div></div>
    ${res.anomalies_total ? `<div class="panel"><h3>Da controllare</h3>
      <p><span class="badge error">${res.anomalies_by_level.errore} errori</span>
        <span class="badge warn">${res.anomalies_by_level.avviso} avvisi</span>
        <span class="badge">${res.anomalies_by_level.info} note</span></p>
      <p class="hint">Prima gli errori, poi gli avvisi, infine le note di Gemini. Clic su una riga per vedere la pagina.</p>
      ${table(["file", "livello", "pagina", "riga", "colonna", "valore", "problema"],
        res.anomalies.map(a => ({ file: a.file, livello: a.level, pagina: a.page ?? "", riga: a.row ?? "", colonna: a.column ?? "", valore: a.value, problema: a.problem })),
        { pageColumn: "pagina", fileColumn: "file", files: ctx.files })}</div>` : ""}
    <div class="panel"><h3>Prime righe</h3>
      ${table(res.columns, res.rows, { pageColumn: res.page_column, fileColumn: res.source_column, files: ctx.files })}</div>`;
  bindPageClicks(box, ctx.titles);
  $("#regen").addEventListener("click", e => busy(e.target, async () => { await renderResults(ctx); toast("CSV e pagina di ricerca rigenerati."); }));
}

// ------------------------------------------------------------------ avvio
refreshStatus();
setInterval(refreshStatus, 20000);
route();
