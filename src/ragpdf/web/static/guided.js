/* Progetto guidato: dalla scheda di un PDF a un progetto, con l'aiuto di Gemini.
 *
 * Schermate (indirizzo dopo il #):
 *   #/guidato/nuovo/<doc>   l'indice che vuoi (riga, colonne, CSV d'esempio, a parole), dove cercare, indicazioni
 *   #/guidato/<id>          report: tabella modificabile, catalogo di partenza, candidati, crea il progetto
 * Nella scheda del catalogo c'è il pannello con il pulsante e le proposte precedenti (renderGuidedPanel).
 *
 * Usa le utilità di app.js, library.js e settings.js; viene caricato prima, le funzioni si chiamano dopo l'avvio.
 */
"use strict";

const guidedDate = iso => String(iso || "").replace("T", " ").slice(0, 16);

/** Editor di colonne (nome, descrizione, ordine). onChange viene chiamato a ogni modifica. */
function columnsEditor(container, columns, { onChange = () => {}, badge = () => "" } = {}) {
  const draw = () => {
    container.innerHTML = columns.length ? columns.map((c, i) => `
      <div class="col-row">
        <input type="text" data-gcname="${i}" value="${esc(c.name)}" placeholder="nome (es. CODICE_FIN)">
        <input type="text" data-gcdesc="${i}" value="${esc(c.description || "")}" placeholder="descrizione (cosa contiene, formato)">
        ${badge(c) || "<span></span>"}
        <button class="secondary small" data-gcup="${i}" type="button" title="Sposta su" ${i ? "" : "disabled"}>&uarr;</button>
        <button class="secondary small" data-gcdel="${i}" type="button" title="Togli">&times;</button></div>`).join("")
      : `<p class="muted small">Nessuna colonna.</p>`;
    $$("[data-gcname]", container).forEach(el => {
      el.addEventListener("input", () => { columns[+el.dataset.gcname].name = el.value; onChange(); });
      el.addEventListener("change", draw);
    });
    $$("[data-gcdesc]", container).forEach(el => el.addEventListener("input", () => {
      columns[+el.dataset.gcdesc].description = el.value; onChange();
    }));
    $$("[data-gcup]", container).forEach(b => b.addEventListener("click", () => {
      const i = +b.dataset.gcup; [columns[i - 1], columns[i]] = [columns[i], columns[i - 1]]; draw(); onChange();
    }));
    $$("[data-gcdel]", container).forEach(b => b.addEventListener("click", () => {
      columns.splice(+b.dataset.gcdel, 1); draw(); onChange();
    }));
  };
  draw();
  return {
    add(name = "", description = "") {
      if (name && columns.some(c => normName(c.name) === normName(name))) { toast("Questa colonna c'è già."); return; }
      columns.push({ name, description }); draw(); onChange();
      if (!name) $(`[data-gcname="${columns.length - 1}"]`, container).focus();
    },
    set(list) { columns.splice(0, columns.length, ...list); draw(); onChange(); },
    redraw: draw,
    get: () => columns.filter(c => c.name.trim()).map(c => ({ name: c.name.trim(), description: (c.description || "").trim() })),
  };
}

/** Caselle per scegliere le sezioni (collezioni) di un PDF da indicizzare. */
function sectionsPicker(docId, collections, checked) {
  if (!collections.length) return `<p class="muted small">Nessuna collezione nella scheda: di default si indicizzano le pagine della specifica.</p>`;
  return `<div class="coll-pick">${[...collections].sort((x, y) => (y.tag === "dati") - (x.tag === "dati") || x.start - y.start).map(c => `
    <label class="row" style="font-weight:normal;margin:.1rem 0">
      <input type="checkbox" data-sec="${esc(docId)}" value="${esc(c.id)}" ${checked.includes(c.id) ? "checked" : ""}>
      ${pageTagChip(c.tag, c.label)} <span>pagine ${c.start === c.end ? c.start : `${c.start}-${c.end}`}</span>
      ${c.comment ? `<span class="muted small">— ${esc(c.comment)}</span>` : ""}</label>`).join("")}</div>`;
}

// ------------------------------------------------------------------ pannello nella scheda del catalogo
async function renderGuidedPanel(c) {
  const box = $("#guided-panel");
  if (!box) return;
  const runs = await api("GET", `/api/library/catalogs/${enc(c.id)}/guided`);
  if (!$("#guided-panel")) return;
  box.innerHTML = `
    <div class="row"><h3 style="margin:0">✨ Crea progetto guidato</h3><span class="spacer"></span>
      <a class="linkbtn" href="#/guidato/nuovo/${enc(c.id)}">Crea progetto guidato…</a></div>
    <p class="hint">Descrivi l'indice che vuoi ottenere da questo catalogo (una tabella di colonne, un CSV d'esempio o a
      parole): Gemini cerca tra i cataloghi elaborati della biblioteca quelli da unire e ti propone il progetto.
      ${c.description || c.fields.length ? "" : " <strong>Questo catalogo non è ancora elaborato</strong>: una descrizione o i dati presenti aiutano molto."}</p>
    ${runs.length ? `<details><summary class="small">Proposte precedenti (${runs.length})</summary><ul class="small">
      ${runs.map(r => `<li><a href="#/guidato/${enc(r.id)}">${esc(guidedDate(r.created_at))}</a> ·
        ${r.status === "valida" ? `${r.candidates} candidat${r.candidates === 1 ? "o" : "i"}` : `<span class="badge error">non valida</span>`}
        ${r.project ? ` · progetto <a href="#/p/${enc(r.project.id)}/docs">${esc(r.project.name)}</a>` : ""}</li>`).join("")}
      </ul></details>` : ""}`;
}

// ------------------------------------------------------------------ richiesta
async function renderGuidedNew(docId) {
  const [c, info, overview] = await Promise.all([api("GET", `/api/library/catalogs/${enc(docId)}`),
    api("GET", "/api/library/guided-prompt"), api("GET", "/api/library/catalogs")]);
  const draftKey = `guided:${docId}`, instrKey = "guided-instructions";
  let draft = {};
  try { draft = JSON.parse(drafts.get(draftKey) || "{}"); } catch { draft = {}; }
  const columns = draft.columns || [];
  let reference = draft.reference || null;

  view.innerHTML = `
    <p class="small"><a href="#/biblioteca/c/${enc(docId)}">&larr; ${esc(c.title)}</a></p>
    <h1>✨ Crea progetto guidato</h1>
    <p class="muted">Parti da <strong>${esc(c.title)}</strong> e descrivi l'indice che vuoi ottenere. Gemini lo trasforma
      in una tabella precisa, dice quali colonne si ricavano da questo catalogo e cerca tra i cataloghi elaborati della
      biblioteca quelli che possono produrre righe della stessa tabella. Poi decidi tu cosa mettere nel progetto.</p>
    <div id="job"></div>
    <div class="panel">
      <h3>L'indice che vuoi</h3>
      <p class="hint">Usa uno o più modi, come preferisci: Gemini li mette insieme.</p>
      <label for="g-row">Cosa rappresenta una riga</label>
      <input type="text" id="g-row" placeholder="es. una riga per ogni codice ricambio di ogni motore">
      <label>Colonne <span class="muted small">(se le scrivi, Gemini le tiene così)</span></label>
      <div id="g-cols"></div>
      <div class="row" style="margin-top:.35rem"><button class="secondary small" id="g-add" type="button">+ Aggiungi colonna</button>
        ${c.fields.length ? `<span class="small muted">dai dati presenti:</span> ${c.fields.map(f =>
          `<button type="button" class="chip" data-gadd="${esc(f.name)}">+ ${esc(f.name)}</button>`).join(" ")}` : ""}</div>
      <label for="g-csv">CSV d'esempio <span class="muted small">(facoltativo: diventa anche il riferimento del progetto)</span></label>
      <div class="row"><input type="file" id="g-csv" accept=".csv,text/csv"><span class="small muted" id="g-csv-info"></span>
        <button class="secondary small" id="g-csv-cols" type="button" hidden>Usa le sue colonne</button>
        <button class="secondary small" id="g-csv-drop" type="button" hidden>Togli</button></div>
      <label for="g-text">A parole</label>
      <textarea id="g-text" rows="3" placeholder="es. voglio cercare un ricambio partendo dal motore: per ogni motore tutti i codici FIN con il codice OEM"></textarea>
    </div>
    <div class="panel">
      <h3>Dove cercare</h3>
      <div class="row"><select id="g-folder" style="width:auto">${["", ...overview.folders].map(f =>
        `<option value="${esc(f)}">${esc(f || "Tutta la biblioteca")}</option>`).join("")}</select>
        <span class="hint" id="g-scope"></span></div>
      <p class="hint">Solo i cataloghi <strong>elaborati</strong> (con descrizione o dati presenti): sono le loro schede
        che Gemini confronta. Una sola richiesta, niente PDF.</p>
    </div>
    <div class="panel">
      <h3>Indicazioni per Gemini</h3>
      <p class="hint">Come valutare la richiesta e i cataloghi. Il formato della risposta è fisso: lo aggiunge il programma
        dopo le tue indicazioni e prevale su qualsiasi richiesta diversa; se la risposta non lo rispetta è scartata come
        <em>non valida</em>.</p>
      <textarea id="g-instr" rows="8" maxlength="8000"></textarea>
      <div class="row">
        <button class="secondary small" id="g-save-default" type="button">Salva come mio predefinito</button>
        <button class="secondary small" id="g-reset" type="button">Ripristina quello di fabbrica</button>
        <span class="hint" id="g-state"></span>
      </div>
    </div>
    <div class="row"><span class="spacer"></span>
      <button class="secondary" id="g-preview" type="button">Vedi il prompt</button>
      <button class="gemini" id="g-send" type="button">Cerca i cataloghi e proponi il progetto</button></div>`;

  const row = $("#g-row"), text = $("#g-text"), folder = $("#g-folder"), instr = $("#g-instr");
  row.value = draft.row || ""; text.value = draft.text || ""; folder.value = draft.folder || "";
  instr.value = drafts.get(instrKey) || info.effective;
  let timer = null;
  const body = () => ({ row: row.value, text: text.value, folder: folder.value, columns: editor.get(),
                        reference: reference ? reference.path : null, instructions: instr.value });
  const save = () => {
    drafts.set(draftKey, JSON.stringify({ row: row.value, text: text.value, folder: folder.value, columns, reference }));
    clearTimeout(timer); timer = setTimeout(scope, 500);
  };
  const editor = columnsEditor($("#g-cols"), columns, { onChange: save });
  const state = () => {
    $("#g-state").textContent = instr.value.trim() === (info.custom || info.factory).trim()
      ? (info.custom ? "Indicazioni: il tuo predefinito." : "Indicazioni: quelle di fabbrica.")
      : "Indicazioni modificate (valgono per questo invio).";
  };
  const showReference = () => {
    $("#g-csv-info").textContent = reference ? `${reference.name}: ${reference.columns.join(", ")}` : "";
    $("#g-csv-cols").hidden = $("#g-csv-drop").hidden = !reference;
  };
  async function scope() {
    const b = body();
    if (!b.row.trim() && !b.columns.length && !b.text.trim() && !b.reference) {
      $("#g-scope").textContent = "Descrivi l'indice per vedere quanti cataloghi verranno confrontati."; return;
    }
    try {
      const p = await api("POST", `/api/library/catalogs/${enc(docId)}/guided/preview`, b);
      $("#g-scope").textContent = `${p.candidates} catalog${p.candidates === 1 ? "o elaborato" : "hi elaborati"} da confrontare`
        + (p.not_elaborated ? ` (${p.not_elaborated} non elaborat${p.not_elaborated === 1 ? "o escluso" : "i esclusi"}).` : ".");
    } catch (e) { $("#g-scope").textContent = `⚠ ${e.message}`; }
  }
  [row, text].forEach(el => el.addEventListener("input", save));
  folder.addEventListener("change", save);
  instr.addEventListener("input", () => { drafts.set(instrKey, instr.value); state(); });
  $("#g-add").addEventListener("click", () => editor.add());
  $$("[data-gadd]").forEach(b => b.addEventListener("click", () => editor.add(b.dataset.gadd)));
  $("#g-csv").addEventListener("change", async e => {
    const file = e.target.files[0]; if (!file) return;
    const form = new FormData(); form.append("file", file);
    try { reference = await api("POST", "/api/references", form, true); }
    catch (err) { reference = null; showError(err); }
    e.target.value = ""; showReference(); save();
  });
  $("#g-csv-cols").addEventListener("click", () => {
    editor.set(reference.columns.map(name => ({ name, description: "" }))); toast("Colonne prese dal CSV.");
  });
  $("#g-csv-drop").addEventListener("click", () => { reference = null; showReference(); save(); });
  $("#g-save-default").addEventListener("click", e => busy(e.target, async () => {
    Object.assign(info, await api("PUT", "/api/library/guided-prompt", { text: instr.value }));
    toast("Indicazioni salvate come tuo predefinito."); state();
  }));
  $("#g-reset").addEventListener("click", async e => {
    const ok = await confirmDialog({ title: "Ripristinare le indicazioni di fabbrica?", ok: "Ripristina",
      html: "<p>Il tuo predefinito personale (se c'è) viene cancellato e la casella torna al testo originale.</p>" });
    if (!ok) return;
    await busy(e.target, async () => {
      Object.assign(info, await api("PUT", "/api/library/guided-prompt", { text: "" }));
      instr.value = info.factory; drafts.clear(instrKey); state();
    });
  });
  $("#g-preview").addEventListener("click", e => busy(e.target, async () => {
    const p = await api("POST", `/api/library/catalogs/${enc(docId)}/guided/preview`, body());
    showText("Prompt per Gemini", p.prompt, `${p.candidates} cataloghi elaborati confrontati.`);
  }));
  const done = j => { if (jobSucceeded(j)) location.hash = `#/guidato/${enc(j.result.run_id)}`; };
  $("#g-send").addEventListener("click", async e => {
    const p = await busy(e.target, () => api("POST", `/api/library/catalogs/${enc(docId)}/guided/preview`, body()));
    if (!p) return;
    const ok = await confirmDialog({ title: "Cercare i cataloghi con Gemini?", ok: "Invia a Gemini", html: `
      <p><strong>1 richiesta</strong> a Gemini con la tua richiesta, la scheda di <strong>${esc(c.title)}</strong> e
        quelle di <strong>${p.candidates} cataloghi elaborati</strong> (solo testo).</p>
      <p class="hint">La risposta diventa un report: niente viene creato finché non lo decidi tu.</p>` });
    if (!ok) return;
    await busy(e.target, async () => {
      const job = await api("POST", `/api/library/catalogs/${enc(docId)}/guided`, body());
      watchJob($("#job"), job, done);
      $("#job").scrollIntoView({ block: "start", behavior: "smooth" });
    });
  });

  showReference(); state(); scope();
  const running = await api("GET", `/api/library/catalogs/${enc(docId)}/guided-job`);
  if (running && running.status === "in_corso") watchJob($("#job"), running, done);
}

// ------------------------------------------------------------------ report
async function renderGuidedReport(runId) {
  const run = await api("GET", `/api/library/guided/${enc(runId)}`);
  const cat = id => run.catalogs[id] || { id, title: id, missing: true, collections: [], data: [] };
  const seed = cat(run.seed);
  const head = `
    <p class="small"><a href="#/biblioteca/c/${enc(run.seed)}">&larr; ${esc(seed.title)}</a></p>
    <h1>Proposta di progetto da ${esc(seed.title)}</h1>
    <p class="muted small">${esc(guidedDate(run.created_at))} · ${run.candidates.length} cataloghi elaborati confrontati
      ${run.reference_name ? ` · CSV d'esempio: ${esc(run.reference_name)}` : ""}</p>`;
  if (run.status !== "valida") {
    view.innerHTML = `${head}
      <div class="panel note error"><strong>Risposta non valida</strong>: Gemini non ha rispettato il formato richiesto,
        quindi la proposta non è stata usata. Puoi <a href="#/guidato/nuovo/${enc(run.seed)}">riprovare</a>.
        <ul class="small">${run.problems.map(p => `<li>${esc(p)}</li>`).join("")}</ul></div>
      <details class="panel"><summary>Risposta ricevuta</summary><pre>${esc(JSON.stringify(run.raw, null, 2))}</pre></details>`;
    return;
  }
  const r = run.result;
  const created = !!run.project;
  const dataIds = id => cat(id).collections.filter(x => x.tag === "dati").map(x => x.id);
  const covered = { [run.seed]: r.seed_covered };
  r.candidates.forEach(x => { covered[x.document] = x.covered; });

  view.innerHTML = `${head}
    ${created ? `<div class="note"><strong>Progetto creato:</strong> <a href="#/p/${enc(run.project.id)}/docs">${esc(run.project.name)} →</a></div>` : ""}
    ${run.problems.length ? `<details class="note warn small"><summary>${run.problems.length} correzion${run.problems.length === 1 ? "e" : "i"} alla
      risposta di Gemini</summary><ul>${run.problems.map(p => `<li>${esc(p)}</li>`).join("")}</ul></details>` : ""}
    ${r.notes ? `<div class="panel proposal"><h4 style="margin-top:0">Osservazioni di Gemini</h4><blockquote>${esc(r.notes)}</blockquote></div>` : ""}
    <div class="panel">
      <h3>La tabella</h3>
      <p class="hint">Diventerà l'impostazione globale del progetto (modificabile anche dopo). Accanto a ogni colonna: in
        quanti PDF scelti si ricava, secondo Gemini.</p>
      <label for="r-name">Nome del progetto</label><input type="text" id="r-name">
      <label for="r-desc">Descrizione</label><textarea id="r-desc" rows="2"></textarea>
      <label for="r-row">Cosa rappresenta una riga</label><input type="text" id="r-row">
      <label>Colonne</label><div id="r-cols"></div>
      <div class="row" style="margin-top:.35rem"><button class="secondary small" id="r-add" type="button">+ Aggiungi colonna</button></div>
      <label for="r-instr">Altre indicazioni <span class="muted small">(facoltative)</span></label>
      <textarea id="r-instr" rows="2"></textarea>
    </div>
    <div class="panel">
      <h3>Catalogo di partenza: <a href="#/biblioteca/c/${enc(run.seed)}">${esc(seed.title)}</a></h3>
      <p class="small">Colonne che si ricavano: ${r.seed_covered.length ? tagBadges(r.seed_covered) : "—"}
        ${r.seed_missing.length ? `<br>Non si ricavano: ${r.seed_missing.map(n => `<span class="badge warn">${esc(n)}</span>`).join(" ")}` : ""}</p>
      ${r.seed_notes ? `<p class="small muted">${esc(r.seed_notes)}</p>` : ""}
      <label>Pagine da indicizzare</label>
      ${sectionsPicker(run.seed, seed.collections, r.seed_collections.length ? r.seed_collections : dataIds(run.seed))}
    </div>
    <div class="panel">
      <h3>Cataloghi candidati (${r.candidates.length})</h3>
      ${r.candidates.length ? `<p class="hint">Spunta i cataloghi da mettere nel progetto (i <em>compatibili</em> sono già
        spuntati) e, per ciascuno, le sezioni da indicizzare.</p>` + r.candidates.map(x => {
        const k = cat(x.document);
        return `<div class="panel cand" style="margin-bottom:8px">
          <label class="row" style="margin:0">
            <input type="checkbox" data-cand="${esc(x.document)}" ${x.verdict === "compatibile" ? "checked" : ""} ${k.missing ? "disabled" : ""}>
            ${k.missing ? `${esc(x.document)} <span class="badge error">eliminato</span>`
              : `<a href="#/biblioteca/c/${enc(x.document)}">${esc(k.title)}</a>`}
            <span class="badge ${x.verdict === "compatibile" ? "ok" : "warn"}">${esc(x.verdict)}</span>
            <span class="small muted">${esc(k.folder || "Biblioteca")}</span></label>
          <p class="small" style="margin:.35rem 0">${esc(x.reason)}</p>
          <p class="small" style="margin:.35rem 0">Colonne che si ricavano: ${x.covered.length ? tagBadges(x.covered) : "—"}</p>
          ${k.missing ? "" : `<details ${x.verdict === "compatibile" ? "open" : ""}><summary class="small">Pagine da indicizzare</summary>
            ${sectionsPicker(x.document, k.collections, x.collections.length ? x.collections : dataIds(x.document))}</details>`}
        </div>`;
      }).join("") : `<p class="muted">Gemini non ha trovato altri cataloghi adatti: puoi creare il progetto con il solo catalogo
        di partenza, e aggiungere PDF più avanti.</p>`}
    </div>
    <div class="row"><span class="spacer"></span>
      <button id="r-create" type="button" ${created ? "disabled" : ""}>Crea progetto</button></div>`;

  $("#r-name").value = r.project_name || seed.title;
  $("#r-desc").value = r.description; $("#r-row").value = r.row;
  const included = () => [run.seed, ...$$("[data-cand]:checked").map(b => b.dataset.cand)];
  const badge = col => {
    if (!col.name.trim()) return "";
    const ids = included();
    const n = ids.filter(id => (covered[id] || []).some(x => normName(x) === normName(col.name))).length;
    const cls = n === ids.length ? "ok" : n ? "warn" : "";
    return `<span class="badge ${cls}" title="PDF scelti in cui si ricava, secondo Gemini">${n}/${ids.length}</span>`;
  };
  const editor = columnsEditor($("#r-cols"), r.columns.map(c => ({ ...c })), { badge });
  $("#r-add").addEventListener("click", () => editor.add());
  const updateButton = () => {
    $("#r-create").textContent = created ? "Progetto già creato" : `Crea progetto (${included().length} PDF)`;
    editor.redraw();
  };
  $$("[data-cand]").forEach(b => b.addEventListener("change", updateButton));
  updateButton();
  if (created) $$("#view input, #view textarea, #view select").forEach(el => { el.disabled = true; });

  $("#r-create").addEventListener("click", async e => {
    const ids = included();
    const columns = editor.get();
    if (!$("#r-name").value.trim()) { toast("Dai un nome al progetto."); return; }
    const ok = await confirmDialog({ title: "Creare il progetto?", ok: "Crea progetto", html: `
      <p>Progetto <strong>${esc($("#r-name").value.trim())}</strong> con ${ids.length} PDF:
        ${ids.map(id => esc(cat(id).title)).join(", ")}.</p>
      <p class="hint">${columns.length} colonne diventano la Tabella del progetto (passo ①, vincolante per le regole);
        le sezioni spuntate diventano le pagine da indicizzare${run.reference_name ? "; il CSV d'esempio diventa il riferimento" : ""}.</p>` });
    if (!ok) return;
    await busy(e.target, async () => {
      const r2 = await api("POST", `/api/library/guided/${enc(runId)}/project`, {
        name: $("#r-name").value, description: $("#r-desc").value, row: $("#r-row").value,
        instructions: $("#r-instr").value, columns,
        documents: ids.map(id => ({ id, collections: $$(`[data-sec="${CSS.escape(id)}"]:checked`).map(b => b.value) })),
      });
      toast("Progetto creato: controlla la tabella e prosegui con il prossimo passo.");
      location.hash = `#/p/${enc(r2.project.id)}/settings`;
    });
  });
}
