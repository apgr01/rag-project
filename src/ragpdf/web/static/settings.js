/* Impostazioni globali del progetto: la tabella che il progetto deve produrre.
 *
 * Schermata #/p/<id>/settings: descrizione, cosa è una riga, colonne (nell'ordine del CSV),
 * altre indicazioni. Accanto alle colonne la copertura nei PDF del progetto, ricavata dai
 * loro "dati presenti" nella biblioteca (letti in diretta: sono descrittivi).
 *
 * Usa le utilità di app.js e library.js; viene caricato prima, le funzioni si chiamano dopo l'avvio.
 */
"use strict";

const normName = s => String(s || "").trim().replace(/\s+/g, " ").toLowerCase();

/** Avviso quando una specifica non usa esattamente le colonne del progetto. */
function columnsCheckNote(check, subject) {
  if (!check || check.ok) return "";
  const parts = [];
  if (check.missing.length) parts.push(`mancano <strong>${check.missing.map(esc).join(", ")}</strong>`);
  if (check.extra.length) parts.push(`in più <strong>${check.extra.map(esc).join(", ")}</strong>`);
  if (!check.order_ok) parts.push("l'ordine è diverso");
  return `<div class="note warn"><strong>${esc(subject)} non usa esattamente le colonne del progetto</strong>:
    ${parts.join("; ")}.</div>`;
}

async function renderSettings(ctx) {
  const { project, pid, box } = ctx;
  const docs = project.documents;
  const index = new Map();               // dato (normalizzato) -> { name, docs: Set }
  docs.forEach(d => (d.library ? d.library.data : []).forEach(name => {
    const k = normName(name);
    if (!index.has(k)) index.set(k, { name, docs: new Set() });
    index.get(k).docs.add(d.id);
  }));
  const withoutData = docs.filter(d => !d.library || !d.library.data.length);
  let columns = project.fields.map(f => ({ name: f.name, description: f.description || "" }));

  box.innerHTML = `
    ${columnsCheckNote(project.columns_check, "La specifica salvata")}
    ${project.columns_check && !project.columns_check.ok ? `<p class="hint">Rifai la configurazione (scheda 1) per
      allinearla alle impostazioni, poi reindicizza.</p>` : ""}
    <div class="grid2 wide-left">
      <div class="panel">
        <h3>La tabella del progetto</h3>
        <p class="hint">Queste impostazioni decidono il CSV. La configurazione guidata le passa a Gemini come vincolo e
          controlla che la specifica usi <strong>esattamente</strong> queste colonne, in quest'ordine.</p>
        <label for="s-name">Nome del progetto</label>
        <input type="text" id="s-name">
        <label for="s-desc">Descrizione</label>
        <textarea id="s-desc" rows="3" placeholder="A cosa serve la tabella, quali cataloghi raccoglie"></textarea>
        <label for="s-row">Cosa rappresenta una riga</label>
        <input type="text" id="s-row" placeholder="es. una riga per ogni codice ricambio di ogni motore">
        <label>Colonne <span class="muted small">(nell'ordine del CSV; pagina e file d'origine li aggiunge il programma)</span></label>
        <div id="s-cols"></div>
        <div class="row" style="margin-top:.5rem"><button class="secondary small" id="s-add" type="button">+ Aggiungi colonna</button></div>
        <label for="s-instr">Altre indicazioni <span class="muted small">(facoltative)</span></label>
        <textarea id="s-instr" rows="3" placeholder="es. se manca il codice OEM lascia vuoto; codici senza spazi"></textarea>
        <div class="row" style="margin-top:.75rem"><span class="spacer"></span><button id="s-save" type="button">Salva impostazioni</button></div>
      </div>
      <div class="panel">
        <h3>Dati presenti nei PDF</h3>
        <p class="hint">Dalle schede della biblioteca. Clic su un dato per aggiungerlo come colonna; il numero dice in
          quanti PDF del progetto compare (il confronto è per nome).</p>
        ${index.size ? `<div class="chips">${[...index.values()].sort((a, b) => b.docs.size - a.docs.size || a.name.localeCompare(b.name))
          .map(x => `<button type="button" class="chip" data-addcol="${esc(x.name)}" title="${esc(docs.filter(d => x.docs.has(d.id)).map(d => d.title).join(", "))}">
            + ${esc(x.name)} <span class="small">${x.docs.size}/${docs.length}</span></button>`).join("")}</div>`
          : `<p class="muted small">Nessun dato presente nelle schede dei PDF.</p>`}
        ${withoutData.length ? `<p class="hint">Senza dati presenti: ${withoutData.map(d =>
          `<a href="#/biblioteca/c/${enc(d.id)}">${esc(d.title)}</a>`).join(", ")}. Puoi elencarli nella loro scheda,
          anche con l'analisi di Gemini.</p>` : ""}
      </div>
    </div>`;

  $("#s-name").value = project.name;
  $("#s-row").value = project.row || "";
  const desc = $("#s-desc"), instr = $("#s-instr");
  desc.value = project.description || ""; desc.dataset.original = desc.value;
  instr.value = project.instructions || ""; instr.dataset.original = instr.value;

  const coverage = name => { const x = index.get(normName(name)); return x ? x.docs.size : 0; };
  const drawCols = () => {
    $("#s-cols").innerHTML = columns.length ? columns.map((c, i) => {
      const n = coverage(c.name);
      const badge = !c.name.trim() ? "" : n === docs.length ? `<span class="badge ok" title="Presente in tutti i PDF">${n}/${docs.length}</span>`
        : n ? `<span class="badge warn" title="Presente solo in alcuni PDF: negli altri resterà vuota (o va spiegata nelle note)">${n}/${docs.length}</span>`
        : `<span class="badge" title="Nessun PDF lo elenca tra i dati presenti (forse con un altro nome)">0/${docs.length}</span>`;
      return `<div class="col-row">
        <input type="text" data-cname="${i}" value="${esc(c.name)}" placeholder="nome (es. CODICE_FIN)">
        <input type="text" data-cdesc="${i}" value="${esc(c.description)}" placeholder="descrizione (cosa contiene, formato)">
        ${badge}
        <button class="secondary small" data-cup="${i}" type="button" title="Sposta su" ${i ? "" : "disabled"}>&uarr;</button>
        <button class="secondary small" data-cdel="${i}" type="button" title="Togli">&times;</button></div>`;
    }).join("") : `<p class="muted small">Nessuna colonna: aggiungile qui sotto o dai dati presenti a destra.
      Senza colonne la configurazione guidata le proporrà a partire dall'obiettivo scritto a parole.</p>`;
    $$("[data-cname]").forEach(el => el.addEventListener("input", () => { columns[+el.dataset.cname].name = el.value; }));
    $$("[data-cname]").forEach(el => el.addEventListener("change", drawCols));
    $$("[data-cdesc]").forEach(el => el.addEventListener("input", () => { columns[+el.dataset.cdesc].description = el.value; }));
    $$("[data-cup]").forEach(b => b.addEventListener("click", () => {
      const i = +b.dataset.cup; [columns[i - 1], columns[i]] = [columns[i], columns[i - 1]]; drawCols();
    }));
    $$("[data-cdel]").forEach(b => b.addEventListener("click", () => { columns.splice(+b.dataset.cdel, 1); drawCols(); }));
  };
  drawCols();
  $("#s-add").addEventListener("click", () => {
    columns.push({ name: "", description: "" }); drawCols(); $(`[data-cname="${columns.length - 1}"]`).focus();
  });
  $$("[data-addcol]").forEach(b => b.addEventListener("click", () => {
    if (columns.some(c => normName(c.name) === normName(b.dataset.addcol))) { toast("Questa colonna c'è già."); return; }
    columns.push({ name: b.dataset.addcol, description: "" }); drawCols();
  }));
  $("#s-save").addEventListener("click", e => busy(e.target, async () => {
    const saved = await api("PUT", `/api/projects/${enc(pid)}/settings`, {
      name: $("#s-name").value, description: desc.value, row: $("#s-row").value, instructions: instr.value,
      columns: columns.filter(c => c.name.trim()),
    });
    desc.dataset.original = desc.value; instr.dataset.original = instr.value;
    toast(saved.columns_check && !saved.columns_check.ok
      ? "Salvato. Le colonne non coincidono con la specifica salvata: rifai la configurazione e reindicizza."
      : "Impostazioni salvate.", 6000);
    ctx.refresh();
  }));
}
