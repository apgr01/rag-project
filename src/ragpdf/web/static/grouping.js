/* Raggruppamento dei cataloghi proposto da Gemini.
 *
 * Schermate (indirizzo dopo il #):
 *   #/raggruppamenti            archivio dei raggruppamenti proposti
 *   #/raggruppamenti/nuovo      cataloghi scelti nella biblioteca, opzioni, indicazioni, invio
 *   #/raggruppamenti/<id>       report: gruppi (sposta, tag, crea progetto) e non accorpabili
 *
 * Usa le utilità di app.js e library.js; viene caricato prima, le funzioni si chiamano dopo l'avvio.
 */
"use strict";

const fmtDate = iso => String(iso || "").replace("T", " ").slice(0, 16);
const splitTags = text => String(text || "").split(",").map(t => t.trim()).filter(Boolean);

function studiedBadge(studied) {
  return studied ? `<span class="badge ok">studiato</span>`
    : `<span class="badge warn" title="Né descrizione né campi: Gemini ha poco da confrontare">da studiare</span>`;
}

// ------------------------------------------------------------------ nuovo raggruppamento
async function renderGroupingNew() {
  let ids = [];
  try { ids = JSON.parse(drafts.get("grouping-ids") || "[]"); } catch { ids = []; }
  const [data, info] = await Promise.all([api("GET", "/api/library/catalogs"), api("GET", "/api/library/grouping-prompt")]);
  const byId = Object.fromEntries(data.catalogs.map(c => [c.id, c]));
  ids = ids.filter(id => byId[id]);
  const instrKey = "grouping-instructions";

  view.innerHTML = `
    <p class="small"><a href="#/biblioteca">&larr; Biblioteca</a> · <a href="#/raggruppamenti">Raggruppamenti proposti</a></p>
    <h1>✨ Proponi un raggruppamento</h1>
    <p class="muted">Gemini confronta le schede dei cataloghi scelti (descrizione, tag, campi, pagine segnate) e propone
      quali accorpare in un unico progetto, cioè in un'unica tabella. È <strong>una sola richiesta</strong>: niente PDF,
      al massimo una pagina d'esempio per catalogo se lo chiedi. Poi decidi tu, gruppo per gruppo.</p>
    <div id="job"></div>
    <div class="panel">
      <h3>Cataloghi da confrontare <span class="muted small" id="g-count"></span></h3>
      <div id="g-list"></div>
      <div id="g-check"></div>
      <label class="row" style="font-weight:normal"><input type="checkbox" id="g-images"> Allega una pagina d'esempio per
        catalogo (immagine)</label>
      <p class="hint">La prima <em>pagina d'esempio</em> segnata nel catalogo, altrimenti la prima <em>da indicizzare</em>.
        Aiuta Gemini a capire se i layout si somigliano davvero, ma costa più token. Al massimo ${info.max_with_images}
        cataloghi con le immagini, ${info.max} senza.</p>
    </div>
    <div class="panel">
      <h3>Indicazioni per Gemini</h3>
      <p class="hint">Qui decidi <strong>come</strong> valutare i cataloghi. Il <strong>formato</strong> della risposta è
        fisso: lo aggiunge il programma dopo le tue indicazioni e prevale su qualsiasi richiesta diversa; ogni risposta
        viene controllata e, se non lo rispetta, è scartata come <em>non valida</em>.</p>
      <textarea id="g-instr" rows="10" maxlength="8000"></textarea>
      <div class="row">
        <button class="secondary small" id="g-save-default" type="button">Salva come mio predefinito</button>
        <button class="secondary small" id="g-reset" type="button">Ripristina quello di fabbrica</button>
        <span class="hint" id="g-state"></span>
      </div>
    </div>
    <div class="row"><span class="spacer"></span>
      <button class="secondary" id="g-preview" type="button">Vedi il prompt</button>
      <button class="gemini" id="g-send" type="button">Proponi raggruppamento</button></div>`;

  const instr = $("#g-instr");
  instr.value = drafts.get(instrKey) || info.effective;
  const state = () => {
    $("#g-state").textContent = instr.value.trim() === (info.custom || info.factory).trim()
      ? (info.custom ? "Indicazioni: il tuo predefinito." : "Indicazioni: quelle di fabbrica.")
      : "Indicazioni modificate (valgono per questo invio).";
  };
  const body = () => ({ ids, instructions: instr.value, with_images: $("#g-images").checked });

  let preview = null, timer = null;
  const refresh = async () => {
    drafts.set("grouping-ids", JSON.stringify(ids));
    $("#g-count").textContent = `(${ids.length})`;
    let error = "";
    try { preview = ids.length >= 2 ? await api("POST", "/api/library/groupings/preview", body()) : null; }
    catch (e) { preview = null; error = e.message; }
    const pages = Object.fromEntries((preview ? preview.images : []).map(i => [i.id, i.page]));
    const images = $("#g-images").checked;
    $("#g-list").innerHTML = ids.length ? `<div class="table-wrap"><table>
      <thead><tr><th>Catalogo</th><th>Cartella</th><th>Pagine</th><th>Tag</th><th>Scheda</th>
        ${images ? "<th>Immagine</th>" : ""}<th></th></tr></thead>
      <tbody>${ids.map(id => { const c = byId[id]; return `<tr>
        <td><a href="#/biblioteca/c/${enc(c.id)}">${esc(c.title)}</a>
          ${c.projects.length ? `<br><span class="small muted">già in: ${c.projects.map(p => esc(p.name)).join(", ")}</span>` : ""}</td>
        <td class="small">${esc(c.folder || "Biblioteca")}</td><td>${c.page_count}</td><td>${tagBadges(c.tags)}</td>
        <td>${studiedBadge(!!(c.description || c.fields))}</td>
        ${images ? `<td class="small">${pages[id] ? `pag. ${pages[id]}` : `<span class="muted">nessuna segnata</span>`}</td>` : ""}
        <td><button class="secondary small" data-drop="${esc(id)}" type="button" title="Togli dal confronto">&times;</button></td>
      </tr>`; }).join("")}</tbody></table></div>`
      : `<p class="muted">Nessun catalogo: selezionali nella <a href="#/biblioteca">biblioteca</a>.</p>`;
    const notStudied = preview ? preview.not_studied.length : 0;
    $("#g-check").innerHTML = (error ? `<div class="note error">${esc(error)}</div>` : "")
      + (ids.length < 2 ? `<div class="note warn">Servono almeno 2 cataloghi.</div>` : "")
      + (notStudied ? `<div class="note warn">${notStudied} catalog${notStudied === 1 ? "o non è" : "hi non sono"} ancora
          studiat${notStudied === 1 ? "o" : "i"} (né descrizione né campi): Gemini avrà poco da confrontare. Puoi analizzarli
          prima dalla loro scheda (<em>✨ Analisi con Gemini</em>).</div>` : "");
    $$("[data-drop]").forEach(b => b.addEventListener("click", () => { ids = ids.filter(x => x !== b.dataset.drop); refresh(); }));
  };
  $("#g-images").addEventListener("change", refresh);
  instr.addEventListener("input", () => {
    drafts.set(instrKey, instr.value); state();
    clearTimeout(timer); timer = setTimeout(refresh, 600);
  });
  $("#g-save-default").addEventListener("click", e => busy(e.target, async () => {
    Object.assign(info, await api("PUT", "/api/library/grouping-prompt", { text: instr.value }));
    toast("Indicazioni salvate come tuo predefinito."); state();
  }));
  $("#g-reset").addEventListener("click", async e => {
    const ok = await confirmDialog({ title: "Ripristinare le indicazioni di fabbrica?", ok: "Ripristina",
      html: "<p>Il tuo predefinito personale (se c'è) viene cancellato e la casella torna al testo originale.</p>" });
    if (!ok) return;
    await busy(e.target, async () => {
      Object.assign(info, await api("PUT", "/api/library/grouping-prompt", { text: "" }));
      instr.value = info.factory; drafts.clear(instrKey); state(); refresh();
    });
  });
  $("#g-preview").addEventListener("click", e => busy(e.target, async () => {
    const p = await api("POST", "/api/library/groupings/preview", body());
    showText("Prompt per Gemini", p.prompt, `${p.catalogs} cataloghi · ${p.images.length} immagini allegate.`);
  }));
  $("#g-send").addEventListener("click", async e => {
    const p = await busy(e.target, () => api("POST", "/api/library/groupings/preview", body()));
    if (!p) return;
    const ok = await confirmDialog({ title: "Proporre un raggruppamento?", ok: "Invia a Gemini", html: `
      <p><strong>1 richiesta</strong> a Gemini con le schede di <strong>${p.catalogs} cataloghi</strong>
        ${p.images.length ? `e ${p.images.length} immagini di pagine d'esempio` : "(solo testo)"}.</p>
      ${p.not_studied.length ? `<p class="note warn">${p.not_studied.length} non ancora studiati: la proposta per loro sarà meno affidabile.</p>` : ""}
      <p class="hint">La risposta diventa un report: niente viene creato o modificato finché non lo decidi tu.</p>` });
    if (!ok) return;
    await busy(e.target, async () => {
      const job = await api("POST", "/api/library/groupings", body());
      watchJob($("#job"), job, j => { if (jobSucceeded(j)) location.hash = `#/raggruppamenti/${enc(j.result.grouping_id)}`; });
      $("#job").scrollIntoView({ block: "start", behavior: "smooth" });
    });
  });

  state();
  await refresh();
  const running = await api("GET", "/api/library/grouping-job");
  if (running && running.status === "in_corso") {
    watchJob($("#job"), running, j => { if (jobSucceeded(j)) location.hash = `#/raggruppamenti/${enc(j.result.grouping_id)}`; });
  }
}

// ------------------------------------------------------------------ report
async function renderGrouping(id) {
  const g = await api("GET", `/api/library/groupings/${enc(id)}`);
  const cat = d => g.catalogs[d] || { id: d, title: d, missing: true, tags: [], projects: [] };
  const head = `
    <p class="small"><a href="#/biblioteca">&larr; Biblioteca</a> · <a href="#/raggruppamenti">Raggruppamenti proposti</a></p>
    <h1>Raggruppamento del ${esc(fmtDate(g.created_at))}</h1>
    <p class="muted small">${g.documents.length} cataloghi confrontati · ${g.with_images ? "con pagine d'esempio" : "solo testo"}</p>`;

  if (g.status !== "valida") {
    view.innerHTML = `${head}
      <div class="panel note error"><strong>Risposta non valida</strong>: Gemini non ha rispettato il formato richiesto,
        quindi la proposta non è stata usata. Puoi riprovare (eventualmente con meno cataloghi o indicazioni più semplici).
        <ul class="small">${g.problems.map(p => `<li>${esc(p)}</li>`).join("")}</ul></div>
      <details class="panel"><summary>Risposta ricevuta</summary><pre>${esc(JSON.stringify(g.raw, null, 2))}</pre></details>`;
    return;
  }

  const moveOptions = (current, locked) => locked ? `<span class="muted small" title="Il progetto è già stato creato: gestisci i PDF dal progetto">nel progetto</span>` : `<select data-move style="width:auto">
      <option value="">Sposta in…</option>
      ${g.groups.map((x, i) => i === current || x.project_id ? "" : `<option value="${i}">${esc(x.name)}</option>`).join("")}
      ${current === "ungrouped" ? "" : `<option value="ungrouped">Non accorpabili</option>`}
      <option value="new">Nuovo gruppo</option></select>`;
  const catalogRow = (d, current, locked, extra = "") => {
    const c = cat(d);
    return `<tr data-doc="${esc(d)}">${extra}
      <td>${c.missing ? `${esc(d)} <span class="badge error">eliminato</span>` : `<a href="#/biblioteca/c/${enc(d)}">${esc(c.title)}</a>`}
        ${c.projects.length ? `<br><span class="small muted">in: ${c.projects.map(p => `<a href="#/p/${enc(p.id)}/docs">${esc(p.name)}</a>`).join(", ")}</span>` : ""}</td>
      <td class="small">${esc(c.folder || "Biblioteca")}</td>
      <td>${tagBadges(c.tags || [])}</td>
      <td>${c.missing ? "" : studiedBadge(c.studied)}</td>
      <td>${c.missing ? "" : moveOptions(current, locked)}</td></tr>`;
  };

  view.innerHTML = `${head}
    ${g.problems.length ? `<details class="note warn small"><summary>${g.problems.length} correzion${g.problems.length === 1 ? "e" : "i"} alla
      risposta di Gemini</summary><ul>${g.problems.map(p => `<li>${esc(p)}</li>`).join("")}</ul></details>` : ""}
    ${g.notes ? `<div class="panel proposal"><h4 style="margin-top:0">Osservazioni di Gemini</h4><blockquote>${esc(g.notes)}</blockquote></div>` : ""}
    <p class="hint">Per ogni gruppo puoi cambiare il nome, spostare i cataloghi, assegnare i tag comuni e creare il
      progetto: le <strong>colonne unificate</strong> diventano l'obiettivo proposto nella sua configurazione.</p>
    ${g.groups.map((x, i) => `
      <div class="panel group" data-group="${i}">
        <div class="row">
          <label style="margin:0">Gruppo ${i + 1}</label>
          <input type="text" data-gname value="${esc(x.name)}" style="flex:1;min-width:12rem" ${x.project_id ? "disabled" : ""}>
          ${x.project ? `<a class="linkbtn small" href="#/p/${enc(x.project.id)}/docs">Progetto creato: ${esc(x.project.name)} →</a>`
            : `<button data-gproject type="button">Crea progetto (${x.documents.length} PDF)</button>`}
        </div>
        ${x.comment ? `<blockquote class="small">${esc(x.comment)}</blockquote>` : ""}
        <div class="table-wrap"><table><thead><tr><th>Catalogo</th><th>Cartella</th><th>Tag</th><th>Scheda</th><th></th></tr></thead>
          <tbody>${x.documents.map(d => catalogRow(d, i, !!x.project_id)).join("")}</tbody></table></div>
        <div class="grid2" style="margin-top:.75rem">
          <div><h4 style="margin:0 0 .25rem">Colonne unificate</h4>
            ${x.fields.length ? `<ul class="small" style="margin:0">${x.fields.map(f => `<li><strong>${esc(f.name)}</strong>${f.description ? ` — ${esc(f.description)}` : ""}</li>`).join("")}</ul>`
              : `<p class="muted small">nessuna proposta</p>`}</div>
          <div><h4 style="margin:0 0 .25rem">Tag comuni <span class="muted small">(separati da virgola)</span></h4>
            <div class="row"><input type="text" data-gtags value="${esc(x.common_tags.join(", "))}" style="flex:1">
              <button class="secondary small" data-gapply type="button">Assegna ai ${x.documents.length} cataloghi</button></div>
            ${x.tags_applied.length ? `<p class="hint">Già assegnati: ${tagBadges(x.tags_applied)}</p>` : ""}</div>
        </div>
      </div>`).join("")}
    <div class="panel" id="ungrouped">
      <h3>Non accorpabili (${g.ungrouped.length})</h3>
      ${g.ungrouped.length ? `
        <div class="table-wrap"><table><thead><tr><th><input type="checkbox" id="u-all" title="Seleziona tutti"></th>
          <th>Catalogo</th><th>Cartella</th><th>Tag</th><th>Scheda</th><th></th><th>Motivo</th></tr></thead>
          <tbody>${g.ungrouped.map(u => catalogRow(u.document, "ungrouped", false,
            `<td>${cat(u.document).missing ? "" : `<input type="checkbox" data-usel="${esc(u.document)}">`}</td>`)
            .replace("</tr>", `<td class="small">${esc(u.reason)}</td></tr>`)).join("")}</tbody></table></div>
        <div class="row" style="margin-top:.5rem">
          <input type="text" id="u-tag" placeholder="tag" style="width:12rem" list="u-tags">
          <button class="secondary small" id="u-apply" type="button">Assegna ai selezionati</button>
          <span class="hint">Seleziona tutti, alcuni o uno solo.</span></div>`
        : `<p class="muted">Tutti i cataloghi sono in un gruppo.</p>`}
    </div>`;

  const keepScroll = async action => { const y = window.scrollY; await action(); window.scrollTo(0, y); };
  const reload = () => keepScroll(() => renderGrouping(id));
  const base = `/api/library/groupings/${enc(id)}`;

  $$("[data-move]").forEach(sel => sel.addEventListener("change", () => {
    if (!sel.value) return;
    const doc = sel.closest("tr").dataset.doc;
    busy(sel, async () => { await api("POST", `${base}/move`, { document: doc, to: sel.value }); await reload(); });
  }));
  $$("[data-group]").forEach(panel => {
    const i = +panel.dataset.group, name = $("[data-gname]", panel), tags = $("[data-gtags]", panel);
    const save = () => api("PUT", `${base}/groups/${i}`, { name: name.value, tags: splitTags(tags.value) });
    name.addEventListener("change", () => busy(null, save));
    tags.addEventListener("change", () => busy(null, save));
    $("[data-gapply]", panel).addEventListener("click", e => busy(e.target, async () => {
      const list = splitTags(tags.value);
      if (!list.length) throw new Error("Scrivi almeno un tag.");
      await api("POST", `${base}/groups/${i}/tags`, { tags: list });
      toast(`Tag ${list.join(", ")} assegnati ai cataloghi del gruppo.`); await reload();
    }));
    const create = $("[data-gproject]", panel);
    if (create) create.addEventListener("click", async e => {
      const group = g.groups[i];
      const ok = await confirmDialog({ title: "Creare il progetto?", ok: "Crea progetto", html: `
        <p>Progetto <strong>${esc(name.value.trim() || group.name)}</strong> con ${group.documents.length} PDF:
          ${group.documents.map(d => esc(cat(d).title)).join(", ")}.</p>
        <p class="hint">Le pagine segnate <em>da indicizzare</em> diventano le pagine dei PDF;
          ${group.fields.length ? `le ${group.fields.length} colonne unificate diventano l'obiettivo proposto nella configurazione` : "non ci sono colonne unificate"}.</p>` });
      if (!ok) return;
      await busy(e.target, async () => {
        await api("POST", `${base}/groups/${i}/project`, { name: name.value });
        toast("Progetto creato: lo trovi anche in Progetti."); await reload();
      });
    });
  });
  if ($("#u-all")) $("#u-all").addEventListener("change", e => $$("[data-usel]").forEach(cb => { cb.checked = e.target.checked; }));
  if ($("#u-apply")) $("#u-apply").addEventListener("click", e => busy(e.target, async () => {
    const chosen = $$("[data-usel]:checked").map(cb => cb.dataset.usel);
    const tag = $("#u-tag").value.trim();
    if (!chosen.length) throw new Error("Seleziona almeno un catalogo.");
    if (!tag) throw new Error("Scrivi il tag.");
    await api("POST", "/api/library/tags", { ids: chosen, add: [tag] });
    toast(`Tag "${tag}" assegnato a ${chosen.length} catalog${chosen.length === 1 ? "o" : "hi"}.`); await reload();
  }));
}

// ------------------------------------------------------------------ archivio
async function renderGroupings() {
  const list = await api("GET", "/api/library/groupings");
  view.innerHTML = `
    <p class="small"><a href="#/biblioteca">&larr; Biblioteca</a></p>
    <h1>Raggruppamenti proposti</h1>
    <p class="muted">Ogni proposta di Gemini resta qui: puoi riaprirla, spostare cataloghi, creare i progetti che
      mancano. Per una nuova proposta seleziona i cataloghi nella biblioteca e premi <em>✨ Proponi raggruppamento</em>.</p>
    <div class="panel">${list.length ? `<div class="table-wrap"><table>
      <thead><tr><th>Data</th><th>Cataloghi</th><th>Gruppi</th><th>Non accorpabili</th><th>Progetti creati</th><th>Stato</th></tr></thead>
      <tbody>${list.map(x => `<tr class="clickable" data-open="${esc(x.id)}">
        <td>${esc(fmtDate(x.created_at))}</td><td>${x.catalogs}</td><td>${x.groups}</td><td>${x.ungrouped}</td>
        <td>${x.projects}</td>
        <td>${x.status === "valida" ? `<span class="badge ok">valida</span>` : `<span class="badge error">non valida</span>`}</td></tr>`).join("")}
      </tbody></table></div>` : `<p class="muted">Nessun raggruppamento proposto finora.</p>`}</div>`;
  $$("[data-open]").forEach(tr => tr.addEventListener("click", () => { location.hash = `#/raggruppamenti/${enc(tr.dataset.open)}`; }));
}
