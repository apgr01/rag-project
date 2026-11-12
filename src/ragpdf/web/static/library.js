/* Biblioteca dei cataloghi: cartelle, tag, scheda del catalogo e collezioni di pagine.
 *
 * Schermate (indirizzo dopo il #):
 *   #/biblioteca[/<cartella>/<sottocartella>]   elenco per cartella, filtri per tag e ricerca
 *   #/biblioteca/c/<id>                         scheda del catalogo e visualizzatore delle pagine
 *
 * Usa le utilità di app.js ($, api, esc, busy, toast, confirmDialog, route...): questo file
 * viene caricato prima, ma le funzioni vengono chiamate solo dopo l'avvio.
 */
"use strict";

// filtri della biblioteca: restano finché la pagina è aperta
const libFilters = { search: "", tags: [] };

// colori delle collezioni di pagine (tag fissi)
const PAGE_TAG_COLORS = {
  "da-indicizzare": "#1e8e4e", "esempio": "#2c5fd6", "copertina": "#8a6d3b", "indice": "#7b68d9",
  "introduzione": "#3a8fb7", "legenda": "#b96db5", "tabelle-tecniche": "#d08a00", "disegni": "#c4312b",
  "avvertenze": "#a35d00", "vuota": "#8b93a1", "altro": "#4a5568",
};

const folderHash = path => "#/biblioteca" + (path ? "/" + path.split("/").map(enc).join("/") : "");
const parentFolder = path => path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "";
const folderName = path => path ? path.slice(path.lastIndexOf("/") + 1) : "Biblioteca";
const inFolder = (folder, path) => folder === path || folder.startsWith(path + "/");
const joinFolder = (a, b) => [a, b].filter(Boolean).join("/");
const sizeText = mb => mb < 0.1 ? "meno di 0,1 MB" : `${String(mb).replace(".", ",")} MB`;

function tagBadges(tags) {
  return tags.map(t => `<span class="badge tag">${esc(t)}</span>`).join(" ");
}

function pageTagChip(tag, label) {
  return `<span class="ptag" style="--c:${PAGE_TAG_COLORS[tag] || "#666"}">${esc(label || tag)}</span>`;
}

/** Dialogo con un campo di testo; restituisce il testo o null se annullato. */
async function promptDialog({ title, label, value = "", ok = "Conferma", help = "" }) {
  const ok_ = await confirmDialog({ title, ok, html: `
    <label for="prompt-input">${esc(label)}</label>
    <input type="text" id="prompt-input" value="${esc(value)}">
    ${help ? `<p class="hint">${help}</p>` : ""}` });
  const input = $("#prompt-input");
  return ok_ && input ? input.value.trim() : null;
}

// ------------------------------------------------------------------ caricamento
/** Elenco {file, sub} dai file trascinati, entrando anche nelle cartelle. */
async function droppedFiles(dataTransfer) {
  const entries = [...dataTransfer.items || []].map(i => i.webkitGetAsEntry && i.webkitGetAsEntry()).filter(Boolean);
  if (!entries.length) return [...dataTransfer.files].map(file => ({ file, sub: "" }));
  const out = [];
  const walk = async (entry, sub) => {
    if (entry.isFile) {
      const file = await new Promise((res, rej) => entry.file(res, rej));
      out.push({ file, sub });
    } else if (entry.isDirectory) {
      const reader = entry.createReader();
      const next = joinFolder(sub, entry.name);
      for (;;) {
        const batch = await new Promise((res, rej) => reader.readEntries(res, rej));
        if (!batch.length) break;
        for (const e of batch) await walk(e, next);
      }
    }
  };
  for (const e of entries) await walk(e, "");
  return out;
}

/** Carica i PDF nella cartella data (le sottocartelle trascinate vengono ricreate). */
async function uploadToLibrary(items, folder, statusEl) {
  const pdfs = items.filter(i => /\.pdf$/i.test(i.file.name));
  const skipped = items.length - pdfs.length;
  if (!pdfs.length) { toast("Nessun PDF tra i file scelti."); return; }
  const already = [], failed = [];
  let ok = 0, scanned = 0;
  for (const [i, item] of pdfs.entries()) {
    statusEl.textContent = `Caricamento ${i + 1} di ${pdfs.length}: ${item.file.name}…`;
    const form = new FormData();
    form.append("file", item.file);
    form.append("folder", joinFolder(folder, item.sub));
    try {
      const r = await api("POST", "/api/library/catalogs", form, true);
      if (r.already_present) already.push(`${item.file.name} (è "${r.title}" in ${r.folder || "Biblioteca"})`);
      else { ok++; if (r.scanned) scanned++; }
    } catch (e) { failed.push(`${item.file.name}: ${e.message}`); }
  }
  const lines = [`${ok} PDF caricati.`];
  if (scanned) lines.push(`${scanned} senza testo selezionabile (scansioni): Gemini leggerà solo le immagini.`);
  if (skipped) lines.push(`${skipped} file ignorati perché non sono PDF.`);
  if (already.length) lines.push(`Già presenti, lasciati dove sono: ${already.join("; ")}.`);
  if (failed.length) lines.push(`Non caricati: ${failed.join("; ")}.`);
  statusEl.textContent = lines.join(" ");
}

// ------------------------------------------------------------------ elenco
async function renderLibrary(folder) {
  const data = await api("GET", "/api/library/catalogs");
  const catalogs = data.catalogs;
  if (folder && !data.folders.includes(folder)) folder = "";
  const filtering = !!(libFilters.search.trim() || libFilters.tags.length);
  const words = libFilters.search.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const wanted = libFilters.tags.map(t => t.toLowerCase());
  const visible = catalogs.filter(c => {
    if (!filtering) return c.folder === folder;
    const tags = c.tags.map(t => t.toLowerCase());
    if (!wanted.every(t => tags.includes(t))) return false;
    const hay = `${c.title} ${c.source_filename} ${c.description} ${c.tags.join(" ")}`.toLowerCase();
    return words.every(w => hay.includes(w));
  }).sort((a, b) => a.title.localeCompare(b.title, "it", { sensitivity: "base" }));
  const count = path => catalogs.filter(c => inFolder(c.folder, path)).length;
  const children = path => data.folders.filter(f => parentFolder(f) === path);
  const selected = new Set();

  const tree = path => `<ul>${children(path).map(f => `
      <li><a href="${folderHash(f)}" class="${f === folder && !filtering ? "current" : ""}">📁 ${esc(folderName(f))}
        <span class="muted small">${count(f)}</span></a>${children(f).length ? tree(f) : ""}</li>`).join("")}</ul>`;
  const crumbs = folder ? folder.split("/").map((name, i, all) =>
    ` / <a href="${folderHash(all.slice(0, i + 1).join("/"))}">${esc(name)}</a>`).join("") : "";

  view.innerHTML = `
    <h1>Biblioteca</h1>
    <p class="muted">Tutti i cataloghi, organizzati in cartelle. Apri un catalogo per descriverlo, scegliere i
      campi da indicizzare e segnare le sue pagine; poi seleziona i cataloghi simili e crea un progetto.</p>
    <div class="lib">
      <aside class="panel lib-tree">
        <h3>Cartelle</h3>
        <a href="${folderHash("")}" class="${!folder && !filtering ? "current" : ""}">📚 Biblioteca <span class="muted small">${catalogs.length}</span></a>
        ${tree("")}
        <button class="secondary small" id="folder-new" type="button" style="margin-top:.75rem">+ Nuova cartella</button>
      </aside>
      <section>
        <div class="panel">
          <div class="row">
            <input type="text" id="lib-search" placeholder="Cerca per nome, file, descrizione o tag…" style="flex:1;min-width:12rem">
            ${filtering ? `<button class="secondary" id="lib-clear" type="button">Azzera filtri</button>` : ""}
          </div>
          ${data.tags.length ? `<div class="chips" style="margin-top:.5rem">${data.tags.map(t =>
            `<button type="button" class="chip ${libFilters.tags.some(x => x.toLowerCase() === t.name.toLowerCase()) ? "on" : ""}"
               data-tag="${esc(t.name)}">${esc(t.name)} <span class="small">${t.count}</span></button>`).join("")}</div>
            <p class="hint">Clic su un tag per filtrare (più tag: cataloghi che li hanno tutti). Con un filtro attivo
              vedi i cataloghi di tutte le cartelle.</p>` : ""}
        </div>

        <div class="panel">
          ${filtering ? `<h3>${visible.length} catalog${visible.length === 1 ? "o" : "hi"} trovat${visible.length === 1 ? "o" : "i"}</h3>` : `
          <div class="row">
            <h3 style="margin:0"><a href="${folderHash("")}">Biblioteca</a>${crumbs}</h3>
            <span class="spacer"></span>
            ${folder ? `<button class="secondary small" id="folder-rename" type="button">Rinomina / sposta</button>
                        <button class="secondary small" id="folder-delete" type="button">Elimina cartella</button>` : ""}
          </div>
          ${children(folder).length ? `<div class="chips" style="margin:.75rem 0">${children(folder).map(f =>
            `<a class="chip" href="${folderHash(f)}">📁 ${esc(folderName(f))} <span class="small">${count(f)}</span></a>`).join("")}</div>` : ""}
          <div class="drop" id="lib-drop" style="margin-top:.75rem">Trascina qui PDF o intere cartelle: verranno caricati in
            <strong>${esc(folderName(folder))}</strong> (le sottocartelle vengono ricreate)<br>
            <span class="row" style="justify-content:center;margin-top:.5rem">
              <button class="secondary small" id="pick-files" type="button">Scegli PDF</button>
              <button class="secondary small" id="pick-dir" type="button">Scegli una cartella</button></span>
            <input type="file" id="lib-files" accept=".pdf,application/pdf" multiple hidden>
            <input type="file" id="lib-dir" webkitdirectory multiple hidden></div>
          <p class="hint" id="lib-upload"></p>`}

          <div class="selbar" id="selbar" hidden>
            <strong id="sel-count"></strong>
            <select id="sel-folder" style="width:auto">${["", ...data.folders].map(f =>
              `<option value="${esc(f)}">${esc(f || "Biblioteca (principale)")}</option>`).join("")}</select>
            <button class="secondary small" id="sel-move" type="button">Sposta</button>
            <input type="text" id="sel-tag" placeholder="tag" style="width:9rem" list="all-tags">
            <button class="secondary small" id="sel-tag-add" type="button">Aggiungi tag</button>
            <button class="secondary small" id="sel-tag-remove" type="button">Togli tag</button>
            <button class="small" id="sel-project" type="button">Crea progetto…</button>
            <button class="secondary small" id="sel-delete" type="button">Elimina</button>
          </div>
          <datalist id="all-tags">${data.tags.map(t => `<option value="${esc(t.name)}">`).join("")}</datalist>

          ${visible.length ? `<div class="table-wrap" style="max-height:none"><table class="lib-table">
            <thead><tr><th><input type="checkbox" id="sel-all" title="Seleziona tutti"></th><th></th><th>Catalogo</th>
              <th>Pagine</th><th>Tag</th><th>Studio</th><th>Progetti</th>${filtering ? "<th>Cartella</th>" : ""}</tr></thead>
            <tbody>${visible.map(c => `<tr>
              <td><input type="checkbox" data-sel="${esc(c.id)}"></td>
              <td><img class="cover" loading="lazy" src="/api/documents/${enc(c.id)}/thumb/1" alt=""></td>
              <td><a href="#/biblioteca/c/${enc(c.id)}"><strong>${esc(c.title)}</strong></a><br>
                <span class="small muted">${esc(c.source_filename)}</span>
                ${c.description ? `<br><span class="small">${esc(c.description.length > 140 ? c.description.slice(0, 140) + "…" : c.description)}</span>` : ""}</td>
              <td>${c.page_count}</td>
              <td>${tagBadges(c.tags)}</td>
              <td class="small">${c.fields ? `${c.fields} campi<br>` : ""}${c.collections ? `${c.collections} collezioni` : ""}
                ${c.to_index ? `<br><span class="badge ok">${c.to_index} pag. da indicizzare</span>` : ""}
                ${c.proposals ? `<br><span class="badge warn">✨ proposta da rivedere</span>` : ""}
                ${!c.fields && !c.collections && !c.description && !c.proposals ? `<span class="muted">da studiare</span>` : ""}</td>
              <td class="small">${c.projects.map(p => `<a href="#/p/${enc(p.id)}/docs">${esc(p.name)}</a>`).join("<br>") || "—"}</td>
              ${filtering ? `<td class="small"><a href="${folderHash(c.folder)}">${esc(c.folder || "Biblioteca")}</a></td>` : ""}
            </tr>`).join("")}</tbody></table></div>`
          : `<p class="muted">${filtering ? "Nessun catalogo corrisponde ai filtri." : "Nessun catalogo in questa cartella."}</p>`}
        </div>
      </section>
    </div>`;

  // --- filtri
  const search = $("#lib-search");
  search.value = libFilters.search;
  let timer = null;
  search.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      const pos = search.selectionStart;
      libFilters.search = search.value;
      renderLibrary(folder).then(() => { const s = $("#lib-search"); s.focus(); s.setSelectionRange(pos, pos); });
    }, 250);
  });
  $$("[data-tag]").forEach(b => b.addEventListener("click", () => {
    const t = b.dataset.tag, i = libFilters.tags.findIndex(x => x.toLowerCase() === t.toLowerCase());
    if (i >= 0) libFilters.tags.splice(i, 1); else libFilters.tags.push(t);
    renderLibrary(folder);
  }));
  if ($("#lib-clear")) $("#lib-clear").addEventListener("click", () => {
    libFilters.search = ""; libFilters.tags = []; renderLibrary(folder);
  });

  // --- cartelle
  $("#folder-new").addEventListener("click", async () => {
    const name = await promptDialog({ title: "Nuova cartella", label: `Nome (dentro "${folderName(folder)}")`, ok: "Crea",
      help: "Puoi creare più livelli insieme con la barra, es. <code>Motori/Lombardini</code>." });
    if (!name) return;
    await busy(null, async () => {
      const r = await api("POST", "/api/library/folders", { path: joinFolder(filtering ? "" : folder, name) });
      location.hash = folderHash(r.folder);
    });
  });
  if ($("#folder-rename")) $("#folder-rename").addEventListener("click", async () => {
    const value = await promptDialog({ title: "Rinomina o sposta la cartella", label: "Percorso completo", value: folder,
      ok: "Applica", help: "Cambia l'ultimo nome per rinominare, il percorso per spostarla (con sottocartelle e cataloghi)." });
    if (!value || value === folder) return;
    await busy(null, async () => {
      const r = await api("PUT", "/api/library/folders", { path: folder, new_path: value });
      location.hash = folderHash(r.folder);
    });
  });
  if ($("#folder-delete")) $("#folder-delete").addEventListener("click", async () => {
    const ok = await confirmDialog({ title: "Eliminare la cartella?", danger: true, ok: "Elimina",
      html: `<p>La cartella <strong>${esc(folder)}</strong> deve essere vuota (senza cataloghi, anche nelle sottocartelle).</p>` });
    if (ok) await busy(null, async () => {
      await api("DELETE", `/api/library/folders?path=${enc(folder)}`);
      location.hash = folderHash(parentFolder(folder));
    });
  });

  // --- caricamento
  if (!filtering) {
    const status = $("#lib-upload");
    const done = () => renderLibrary(folder).then(() => { $("#lib-upload").textContent = status.textContent; });
    const drop = $("#lib-drop");
    drop.addEventListener("dragover", e => { e.preventDefault(); drop.classList.add("over"); });
    drop.addEventListener("dragleave", () => drop.classList.remove("over"));
    drop.addEventListener("drop", async e => {
      e.preventDefault(); drop.classList.remove("over");
      const items = await droppedFiles(e.dataTransfer);
      await uploadToLibrary(items, folder, status); done();
    });
    drop.addEventListener("click", e => { if (e.target === drop) $("#lib-files").click(); });
    $("#pick-files").addEventListener("click", () => $("#lib-files").click());
    $("#pick-dir").addEventListener("click", () => $("#lib-dir").click());
    $("#lib-files").addEventListener("change", async e => {
      await uploadToLibrary([...e.target.files].map(file => ({ file, sub: "" })), folder, status); done();
    });
    $("#lib-dir").addEventListener("change", async e => {
      const items = [...e.target.files].map(file => ({ file, sub: parentFolder(file.webkitRelativePath || "") }));
      await uploadToLibrary(items, folder, status); done();
    });
  }

  // --- selezione e azioni
  const updateSel = () => {
    $("#selbar").hidden = !selected.size;
    $("#sel-count").textContent = `${selected.size} selezionat${selected.size === 1 ? "o" : "i"}`;
    if ($("#sel-all")) $("#sel-all").checked = selected.size === visible.length && visible.length > 0;
  };
  $$("[data-sel]").forEach(cb => cb.addEventListener("change", () => {
    if (cb.checked) selected.add(cb.dataset.sel); else selected.delete(cb.dataset.sel);
    updateSel();
  }));
  if ($("#sel-all")) $("#sel-all").addEventListener("change", e => {
    $$("[data-sel]").forEach(cb => { cb.checked = e.target.checked; if (cb.checked) selected.add(cb.dataset.sel); else selected.delete(cb.dataset.sel); });
    updateSel();
  });
  const ids = () => [...selected];
  $("#sel-move").addEventListener("click", e => busy(e.target, async () => {
    const target = $("#sel-folder").value;
    await api("POST", "/api/library/move", { ids: ids(), folder: target });
    toast(`${selected.size} catalog${selected.size === 1 ? "o spostato" : "hi spostati"} in ${target || "Biblioteca"}.`);
    renderLibrary(folder);
  }));
  const retag = (key, button) => busy(button, async () => {
    const tag = $("#sel-tag").value.trim();
    if (!tag) throw new Error("Scrivi il tag.");
    await api("POST", "/api/library/tags", { ids: ids(), [key]: [tag] });
    toast(key === "add" ? `Tag "${tag}" aggiunto.` : `Tag "${tag}" tolto.`);
    renderLibrary(folder);
  });
  $("#sel-tag-add").addEventListener("click", e => retag("add", e.target));
  $("#sel-tag-remove").addEventListener("click", e => retag("remove", e.target));
  $("#sel-project").addEventListener("click", async () => {
    const chosen = catalogs.filter(c => selected.has(c.id));
    const name = await promptDialog({ title: "Nuovo progetto con i cataloghi selezionati", label: "Nome del progetto",
      value: chosen.length === 1 ? chosen[0].title : "", ok: "Crea progetto",
      help: `${chosen.map(c => esc(c.title)).join(", ")}.<br>Le pagine segnate <em>da indicizzare</em> diventano le pagine
             del progetto; le pagine d'esempio, i campi e le descrizioni precompilano la configurazione guidata.` });
    if (!name) return;
    await busy(null, async () => {
      const p = await api("POST", "/api/library/projects", { name, ids: ids() });
      location.hash = `#/p/${enc(p.id)}/docs`;
    });
  });
  $("#sel-delete").addEventListener("click", async e => {
    const chosen = catalogs.filter(c => selected.has(c.id));
    const used = chosen.filter(c => c.projects.length);
    const ok = await confirmDialog({ title: "Eliminare dalla biblioteca?", danger: true, ok: "Elimina", html: `
      <p>Verranno eliminati ${chosen.length - used.length} PDF con pagine, schede e risultati.</p>
      ${used.length ? `<p class="note warn">${used.map(c => esc(c.title)).join(", ")}: usati da un progetto, restano
        (toglili prima dal progetto).</p>` : ""}` });
    if (!ok) return;
    await busy(e.target, async () => {
      for (const c of chosen.filter(x => !x.projects.length)) await api("DELETE", `/api/library/catalogs/${enc(c.id)}`);
      renderLibrary(folder);
    });
  });
}

// ------------------------------------------------------------------ scheda del catalogo
async function renderCatalog(id) {
  const [c, data] = await Promise.all([api("GET", `/api/library/catalogs/${enc(id)}`), api("GET", "/api/library/catalogs")]);
  const tags = Object.fromEntries(data.page_tags.map(t => [t.id, t]));
  const total = c.page_count;
  let current = 1, selStart = 1, selEnd = 1, anchor = 1, editing = null;

  view.innerHTML = `
    <p class="small"><a href="${folderHash("")}">&larr; Biblioteca</a>${c.folder ? c.folder.split("/").map((n, i, all) =>
      ` / <a href="${folderHash(all.slice(0, i + 1).join("/"))}">${esc(n)}</a>`).join("") : ""}</p>
    <h1>${esc(c.title)}</h1>
    <p class="muted small">${esc(c.source_filename)} · ${total} pagine ·
      <a href="/api/documents/${enc(c.id)}/pdf" target="_blank" rel="noopener">apri il PDF</a>
      ${c.scanned ? ` · <span class="badge warn">scansione senza testo</span>` : ""}
      ${c.projects.length ? ` · progetti: ${c.projects.map(p => `<a href="#/p/${enc(p.id)}/docs">${esc(p.name)}</a>`).join(", ")}` : ""}</p>
    <div id="job"></div>
    <div id="proposal"></div>

    <div class="grid2">
      <div class="panel">
        <h3>Scheda</h3>
        <label for="cat-title">Titolo</label>
        <input type="text" id="cat-title">
        <label for="cat-folder">Cartella</label>
        <select id="cat-folder">${["", ...data.folders].map(f =>
          `<option value="${esc(f)}" ${f === c.folder ? "selected" : ""}>${esc(f || "Biblioteca (principale)")}</option>`).join("")}</select>
        <label for="cat-tags">Tag <span class="muted small">(separati da virgola)</span></label>
        <input type="text" id="cat-tags" placeholder="es. motori, Lombardini, tabelle FIN">
        ${data.tags.length ? `<div class="chips small" style="margin-top:.35rem">${data.tags.map(t =>
          `<button type="button" class="chip" data-addtag="${esc(t.name)}">+ ${esc(t.name)}</button>`).join("")}</div>` : ""}
        <label for="cat-desc">Descrizione</label>
        <textarea id="cat-desc" rows="5" placeholder="Cosa contiene, come è organizzato, particolarità (es. il codice OEM è nella colonna a destra; la versione è sotto il nome del motore)"></textarea>
      </div>
      <div class="panel">
        <h3>Campi da indicizzare</h3>
        <p class="hint">Le colonne che vuoi nel CSV, nell'ordine. La descrizione aiuta Gemini a capire cosa intendi.
          Pagina e file d'origine li aggiunge il programma.</p>
        <div id="fields"></div>
        <div class="row" style="margin-top:.5rem"><button class="secondary small" id="field-add" type="button">+ Aggiungi campo</button></div>
      </div>
    </div>
    <div class="row" style="margin:-.5rem 0 16px"><span class="spacer"></span>
      <button class="secondary" id="cat-delete" type="button">Elimina dalla biblioteca</button>
      <button id="cat-save" type="button">Salva scheda</button></div>

    <div class="panel" id="analysis">
      <h3>✨ Analisi con Gemini</h3>
      <p class="hint">Gemini guarda le pagine che scegli e propone descrizione, tag, campi e collezioni di pagine.
        Niente viene salvato finché non applichi la proposta; ogni analisi resta archiviata.</p>
      <label for="a-pages">Pagine da inviare</label>
      <div class="row">
        <input type="text" id="a-pages" placeholder="es. 1-6, 20, 45-46" style="flex:1;min-width:10rem">
        <button class="secondary small" id="a-add-sel" type="button" title="Aggiunge le pagine selezionate nel visualizzatore qui sotto">+ Selezione del visualizzatore</button>
        <button class="secondary small" id="a-quick" type="button">Selezione rapida (prime 6 + 8)</button>
        <button class="secondary small" id="a-clear" type="button">Svuota</button>
      </div>
      <label class="row" style="font-weight:normal"><input type="checkbox" id="a-whole"> Invia tutto il PDF
        (${total} pagine, ${sizeText(c.size_mb)})</label>
      <p class="hint" id="a-info"></p>
      <label for="a-instr">Istruzioni per Gemini <span class="muted small">(modificabili; il programma aggiunge
        da sé pagine inviate, tag disponibili e formato della risposta)</span></label>
      <textarea id="a-instr" rows="10"></textarea>
      <div class="row">
        <button class="secondary small" id="a-save-default" type="button">Salva come mio predefinito</button>
        <button class="secondary small" id="a-reset" type="button">Ripristina quello di fabbrica</button>
        <span class="hint" id="a-prompt-state"></span>
      </div>
      <label class="row" style="font-weight:normal"><input type="checkbox" id="a-card" checked> Tieni conto della
        scheda attuale (Gemini la completa e la corregge invece di ripartire da zero)</label>
      <div class="row"><div id="a-history" style="flex:1"></div>
        <button class="secondary" id="a-preview" type="button">Vedi il prompt</button>
        <button class="gemini" id="a-send" type="button">Analizza con Gemini</button></div>
    </div>

    <div class="panel">
      <h3>Pagine e collezioni</h3>
      <p class="hint">Clic su una miniatura per vederla in grande, <strong>Maiusc+clic</strong> per selezionare un
        intervallo; poi scegli il tag e aggiungi la collezione. Una pagina può stare in più collezioni.</p>
      <div class="viewer">
        <div class="thumbs viewer-thumbs" id="v-thumbs">${Array.from({ length: total }, (_, i) => i + 1).map(p => `
          <div class="thumb" data-p="${p}"><img loading="lazy" src="/api/documents/${enc(c.id)}/thumb/${p}" alt="">
            <div>${p}</div><div class="marks" data-marks="${p}"></div></div>`).join("")}</div>
        <div class="viewer-main">
          <div class="row">
            <button class="secondary small" id="v-prev" type="button">&larr;</button>
            <span>Pagina <input type="text" id="v-page" style="width:4rem;text-align:center"> di ${total}</span>
            <button class="secondary small" id="v-next" type="button">&rarr;</button>
            <span class="spacer"></span><span class="small muted" id="v-in"></span>
          </div>
          <img id="v-img" class="viewer-img" alt="">
        </div>
      </div>
      <div class="collect">
        <div class="row">
          <strong id="c-mode">Nuova collezione:</strong>
          <span>pagine <input type="text" id="c-start" style="width:4rem"> – <input type="text" id="c-end" style="width:4rem"></span>
          <select id="c-tag" style="width:auto">${data.page_tags.map(t =>
            `<option value="${esc(t.id)}" title="${esc(t.help)}">${esc(t.label)}</option>`).join("")}</select>
          <input type="text" id="c-comment" placeholder="commento (facoltativo)" style="flex:1;min-width:10rem">
          <button id="c-save" type="button">Aggiungi collezione</button>
          <button class="secondary" id="c-cancel" type="button" hidden>Annulla</button>
        </div>
        <p class="hint" id="c-help"></p>
      </div>
      <div id="collections"></div>
    </div>`;

  // --- scheda
  $("#cat-title").value = c.title;
  $("#cat-tags").value = c.tags.join(", ");
  const desc = $("#cat-desc");
  desc.value = c.description;
  desc.dataset.original = desc.value;
  $$("[data-addtag]").forEach(b => b.addEventListener("click", () => {
    const list = $("#cat-tags").value.split(",").map(t => t.trim()).filter(Boolean);
    if (!list.some(t => t.toLowerCase() === b.dataset.addtag.toLowerCase())) list.push(b.dataset.addtag);
    $("#cat-tags").value = list.join(", ");
  }));

  let fields = c.fields.map(f => ({ ...f }));
  const renderFields = () => {
    $("#fields").innerHTML = fields.length ? fields.map((f, i) => `
      <div class="field-row">
        <input type="text" data-fname="${i}" placeholder="nome (es. alesaggio)" value="${esc(f.name)}">
        <input type="text" data-fdesc="${i}" placeholder="descrizione (es. diametro del cilindro in mm)" value="${esc(f.description)}">
        <button class="secondary small" data-fup="${i}" type="button" title="Sposta su" ${i ? "" : "disabled"}>&uarr;</button>
        <button class="secondary small" data-fdel="${i}" type="button" title="Togli">&times;</button>
      </div>`).join("") : `<p class="muted small">Nessun campo: aggiungi le colonne che ti servono.</p>`;
    $$("[data-fname]").forEach(el => el.addEventListener("input", () => { fields[+el.dataset.fname].name = el.value; }));
    $$("[data-fdesc]").forEach(el => el.addEventListener("input", () => { fields[+el.dataset.fdesc].description = el.value; }));
    $$("[data-fup]").forEach(b => b.addEventListener("click", () => {
      const i = +b.dataset.fup; [fields[i - 1], fields[i]] = [fields[i], fields[i - 1]]; renderFields();
    }));
    $$("[data-fdel]").forEach(b => b.addEventListener("click", () => { fields.splice(+b.dataset.fdel, 1); renderFields(); }));
  };
  renderFields();
  $("#field-add").addEventListener("click", () => {
    fields.push({ name: "", description: "" }); renderFields();
    $(`[data-fname="${fields.length - 1}"]`).focus();
  });
  $("#cat-save").addEventListener("click", e => busy(e.target, async () => {
    const saved = await api("PUT", `/api/library/catalogs/${enc(c.id)}`, {
      title: $("#cat-title").value, folder: $("#cat-folder").value, description: desc.value,
      tags: $("#cat-tags").value.split(",").map(t => t.trim()).filter(Boolean),
      fields: fields.filter(f => f.name.trim()),
    });
    desc.dataset.original = desc.value;
    fields = saved.fields.map(f => ({ ...f }));
    renderFields();
    toast("Scheda salvata.");
    $("h1").textContent = saved.title;
  }));
  $("#cat-delete").addEventListener("click", async e => {
    if (c.projects.length) { toast("Il catalogo è usato da un progetto: toglilo prima dal progetto.", 6000); return; }
    const ok = await confirmDialog({ title: "Eliminare il catalogo?", danger: true, ok: "Elimina",
      html: `<p><strong>${esc(c.title)}</strong> verrà eliminato dalla biblioteca con le sue pagine, la scheda e le collezioni.</p>` });
    if (ok) await busy(e.target, async () => {
      await api("DELETE", `/api/library/catalogs/${enc(c.id)}`);
      desc.dataset.original = desc.value;
      location.hash = folderHash(c.folder);
    });
  });

  // --- visualizzatore e collezioni
  let collections = c.collections;
  const thumbs = $("#v-thumbs");
  const thumbOf = p => $(`.thumb[data-p="${p}"]`, thumbs);
  const show = (p, scroll = true) => {
    current = Math.min(total, Math.max(1, p));
    $("#v-page").value = current;
    $("#v-img").src = `/api/documents/${enc(c.id)}/page/${current}`;
    const inside = collections.filter(x => x.start <= current && current <= x.end);
    $("#v-in").innerHTML = inside.map(x => pageTagChip(x.tag, tags[x.tag].label)).join(" ");
    $$(".thumb.current", thumbs).forEach(t => t.classList.remove("current"));
    const t = thumbOf(current);
    t.classList.add("current");
    if (scroll) t.scrollIntoView({ block: "nearest" });
  };
  const select = (a, b) => {
    selStart = Math.min(a, b); selEnd = Math.max(a, b);
    $("#c-start").value = selStart; $("#c-end").value = selEnd;
    $$(".thumb", thumbs).forEach(t => t.classList.toggle("selected", +t.dataset.p >= selStart && +t.dataset.p <= selEnd));
    $("#c-help").textContent = `${selEnd - selStart + 1} pagin${selStart === selEnd ? "a" : "e"} selezionat${selStart === selEnd ? "a" : "e"}.`;
  };
  const drawMarks = () => {
    $$("[data-marks]", thumbs).forEach(m => {
      const p = +m.dataset.marks;
      m.innerHTML = collections.filter(x => x.start <= p && p <= x.end)
        .map(x => `<i style="background:${PAGE_TAG_COLORS[x.tag]}" title="${esc(tags[x.tag].label)}"></i>`).join("");
    });
    $("#collections").innerHTML = collections.length ? `<div class="table-wrap" style="margin-top:.75rem"><table>
      <thead><tr><th>Pagine</th><th>Tag</th><th>Commento</th><th></th></tr></thead>
      <tbody>${collections.map(x => `<tr>
        <td>${x.start === x.end ? x.start : `${x.start}–${x.end}`} <span class="muted small">(${x.end - x.start + 1})</span></td>
        <td>${pageTagChip(x.tag, tags[x.tag].label)}</td><td>${esc(x.comment)}
          ${x.origin ? `<span class="badge" title="${x.origin === "dedotta" ? "Gemini l'ha dedotta dall'indice, senza vedere le pagine" : "Proposta da Gemini"}">${x.origin === "dedotta" ? "dedotta" : "Gemini"}</span>` : ""}</td>
        <td style="white-space:nowrap"><button class="secondary small" data-cgo="${esc(x.id)}" type="button">Mostra</button>
          <button class="secondary small" data-cedit="${esc(x.id)}" type="button">Modifica</button>
          <button class="secondary small" data-cdel="${esc(x.id)}" type="button">Elimina</button></td></tr>`).join("")}
      </tbody></table></div>`
      : `<p class="muted small" style="margin-top:.75rem">Nessuna collezione: segna ad esempio copertina, indice, legenda e le pagine da indicizzare.</p>`;
    $$("[data-cgo]").forEach(b => b.addEventListener("click", () => {
      const x = collections.find(y => y.id === b.dataset.cgo); select(x.start, x.end); show(x.start);
    }));
    $$("[data-cedit]").forEach(b => b.addEventListener("click", () => {
      const x = collections.find(y => y.id === b.dataset.cedit);
      editing = x.id; select(x.start, x.end); show(x.start);
      $("#c-tag").value = x.tag; $("#c-comment").value = x.comment;
      $("#c-mode").textContent = "Modifica collezione:"; $("#c-save").textContent = "Salva modifiche"; $("#c-cancel").hidden = false;
    }));
    $$("[data-cdel]").forEach(b => b.addEventListener("click", e => busy(e.target, async () => {
      const r = await api("DELETE", `/api/library/catalogs/${enc(c.id)}/collections/${enc(b.dataset.cdel)}`);
      collections = r.collections; if (editing === b.dataset.cdel) stopEditing(); drawMarks(); show(current, false);
    })));
  };
  const stopEditing = () => {
    editing = null; $("#c-comment").value = "";
    $("#c-mode").textContent = "Nuova collezione:"; $("#c-save").textContent = "Aggiungi collezione"; $("#c-cancel").hidden = true;
  };

  thumbs.addEventListener("click", e => {
    const t = e.target.closest(".thumb");
    if (!t) return;
    const p = +t.dataset.p;
    if (e.shiftKey) select(anchor, p); else { anchor = p; select(p, p); }
    show(p, false);
  });
  $("#v-prev").addEventListener("click", () => show(current - 1));
  $("#v-next").addEventListener("click", () => show(current + 1));
  $("#v-page").addEventListener("change", e => { const p = parseInt(e.target.value, 10); if (p) { show(p); anchor = current; select(current, current); } });
  const rangeInput = () => {
    const a = parseInt($("#c-start").value, 10), b = parseInt($("#c-end").value, 10);
    if (a >= 1 && b >= 1 && a <= total && b <= total) select(a, b);
  };
  $("#c-start").addEventListener("change", rangeInput);
  $("#c-end").addEventListener("change", rangeInput);
  $("#c-cancel").addEventListener("click", stopEditing);
  $("#c-save").addEventListener("click", e => busy(e.target, async () => {
    const body = { start: +$("#c-start").value, end: +$("#c-end").value, tag: $("#c-tag").value, comment: $("#c-comment").value };
    const url = `/api/library/catalogs/${enc(c.id)}/collections` + (editing ? `/${enc(editing)}` : "");
    const r = await api(editing ? "PUT" : "POST", url, body);
    collections = r.collections;
    toast(editing ? "Collezione aggiornata." : `Collezione "${tags[body.tag].label}" aggiunta.`);
    stopEditing(); drawMarks(); show(current, false);
  }));
  // frecce della tastiera per sfogliare (non mentre si scrive)
  const onKey = e => {
    if (!$("#v-thumbs")) { window.removeEventListener("keydown", onKey); return; }
    if (/input|textarea|select/i.test(e.target.tagName) || document.querySelector("dialog[open]")) return;
    if (e.key === "ArrowRight") { e.preventDefault(); show(current + 1); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); show(current - 1); }
  };
  window.addEventListener("keydown", onKey);

  // --- analisi con Gemini
  const pagesInput = $("#a-pages"), whole = $("#a-whole"), instr = $("#a-instr");
  const pagesKey = `analysis-pages:${c.id}`, instrKey = "analysis-instructions";
  const promptInfo = await api("GET", "/api/library/analysis-prompt");
  pagesInput.value = drafts.get(pagesKey);
  instr.value = drafts.get(instrKey) || promptInfo.effective;
  const sendPages = () => { try { return parsePages(pagesInput.value, total); } catch { return []; } };
  const refreshSend = () => {
    drafts.set(pagesKey, pagesInput.value);
    const pages = new Set(whole.checked ? [] : sendPages());
    $$(".thumb", thumbs).forEach(t => t.classList.toggle("tosend", pages.has(+t.dataset.p)));
    pagesInput.disabled = whole.checked;
    let info = "";
    if (whole.checked) info = `Verrà inviato il PDF intero: ${total} pagine, ${sizeText(c.size_mb)}.`;
    else if (pagesInput.value.trim()) {
      try { info = `${parsePages(pagesInput.value, total).length} pagine da inviare (segnate con ✉ nelle miniature).`; }
      catch (e) { info = `⚠ ${e.message}`; }
    } else info = "Scegli le pagine: scrivile, oppure seleziona nel visualizzatore e premi + Selezione del visualizzatore.";
    $("#a-info").textContent = info;
  };
  const promptState = () => {
    const custom = promptInfo.custom;
    $("#a-prompt-state").textContent = instr.value.trim() === (custom || promptInfo.factory).trim()
      ? (custom ? "Istruzioni: il tuo predefinito." : "Istruzioni: quelle di fabbrica.")
      : "Istruzioni modificate (valgono per questo invio).";
  };
  pagesInput.addEventListener("input", refreshSend);
  whole.addEventListener("change", refreshSend);
  instr.addEventListener("input", () => { drafts.set(instrKey, instr.value); promptState(); });
  $("#a-add-sel").addEventListener("click", () => {
    const pages = new Set([...sendPages(), ...Array.from({ length: selEnd - selStart + 1 }, (_, i) => selStart + i)]);
    pagesInput.value = formatPages([...pages]); whole.checked = false; refreshSend();
  });
  $("#a-quick").addEventListener("click", e => busy(e.target, async () => {
    const r = await api("GET", `/api/library/catalogs/${enc(c.id)}/quick-pages`);
    pagesInput.value = r.pages; whole.checked = false; refreshSend();
  }));
  $("#a-clear").addEventListener("click", () => { pagesInput.value = ""; whole.checked = false; refreshSend(); });
  $("#a-save-default").addEventListener("click", e => busy(e.target, async () => {
    Object.assign(promptInfo, await api("PUT", "/api/library/analysis-prompt", { text: instr.value }));
    toast("Istruzioni salvate come tuo predefinito."); promptState();
  }));
  $("#a-reset").addEventListener("click", async e => {
    const ok = await confirmDialog({ title: "Ripristinare le istruzioni di fabbrica?", ok: "Ripristina",
      html: "<p>Il tuo predefinito personale (se c'è) viene cancellato e la casella torna al testo originale.</p>" });
    if (!ok) return;
    await busy(e.target, async () => {
      Object.assign(promptInfo, await api("PUT", "/api/library/analysis-prompt", { text: "" }));
      instr.value = promptInfo.factory; drafts.clear(instrKey); promptState();
    });
  });
  const requestBody = () => ({ pages: pagesInput.value, whole: whole.checked, instructions: instr.value,
                               include_card: $("#a-card").checked });
  $("#a-preview").addEventListener("click", e => busy(e.target, async () => {
    const p = await api("POST", `/api/library/catalogs/${enc(c.id)}/analysis/preview`, requestBody());
    showText("Prompt per Gemini", p.prompt, `Allegato: ${p.pages_text} (${p.pages} pagine, ${sizeText(p.size_mb)}).`);
  }));
  $("#a-send").addEventListener("click", async e => {
    if (hasUnsavedEdits()) { toast("Salva prima la scheda: Gemini deve vedere la versione aggiornata.", 6000); return; }
    const p = await busy(e.target, () => api("POST", `/api/library/catalogs/${enc(c.id)}/analysis/preview`, requestBody()));
    if (!p) return;
    const ok = await confirmDialog({ title: "Analizzare con Gemini?", ok: "Analizza", html: `
      <p><strong>1 richiesta</strong> a Gemini con ${p.pages_text === "PDF intero" ? "il <strong>PDF intero</strong>" : `le pagine <strong>${esc(p.pages_text)}</strong>`}
        (${p.pages} pagine, ${sizeText(p.size_mb)}).</p>
      ${p.heavy ? `<p class="note warn">È un allegato pesante: l'invio richiede più tempo e più token. Se Gemini lo rifiuta,
        scegli meno pagine.</p>` : ""}
      <p class="hint">La risposta diventa una proposta da rivedere: niente cambia nella scheda finché non la applichi.</p>` });
    if (!ok) return;
    await busy(e.target, async () => {
      const job = await api("POST", `/api/library/catalogs/${enc(c.id)}/analysis`, requestBody());
      watchJob($("#job"), job, j => { if (jobSucceeded(j)) { renderCatalog(c.id); } });
      $("#job").scrollIntoView({ block: "start", behavior: "smooth" });
    });
  });

  // --- proposte (analisi salvate)
  const analyses = await api("GET", `/api/library/catalogs/${enc(c.id)}/analyses`);
  const STATUS = { nuova: "da rivedere", applicata: "applicata", scartata: "scartata" };
  $("#a-history").innerHTML = analyses.length ? `<details><summary class="small">Analisi archiviate (${analyses.length})</summary>
    <ul class="small" style="margin:.25rem 0">${analyses.map(a => `<li>${esc(a.created_at.replace("T", " ").slice(0, 16))} ·
      ${esc(a.pages_text)} · ${STATUS[a.status] || esc(a.status)}
      <a href="#" data-review="${esc(a.id)}">rivedi</a></li>`).join("")}</ul></details>` : "";
  const showProposal = a => {
    const r = a.result;
    const has = { tags: new Set(c.tags.map(t => t.toLowerCase())), fields: Object.fromEntries(c.fields.map(f => [f.name.toLowerCase(), f])) };
    const exists = x => collections.some(y => y.start === x.start && y.end === x.end && y.tag === x.tag);
    const box = $("#proposal");
    box.innerHTML = `
      <div class="panel proposal">
        <div class="row"><h3 style="margin:0">✨ Proposta di Gemini</h3>
          <span class="muted small">${esc(a.created_at.replace("T", " ").slice(0, 16))} · ${esc(a.pages_text)} · ${STATUS[a.status] || ""}</span>
          <span class="spacer"></span><button class="secondary small" id="p-close" type="button">Chiudi</button></div>
        ${a.problems.length ? `<details class="note warn small"><summary>${a.problems.length === 1 ? "1 voce della risposta scartata perché non valida" : `${a.problems.length} voci della risposta scartate perché non valide`}</summary>
          <ul>${a.problems.map(x => `<li>${esc(x)}</li>`).join("")}</ul></details>` : ""}
        <p class="hint">Scegli cosa applicare alla scheda. Le voci già presenti non vengono duplicate.</p>

        <h4>Descrizione</h4>
        <blockquote>${esc(r.description) || "<em>vuota</em>"}</blockquote>
        <div class="row small">
          <label class="row" style="font-weight:normal;margin:0"><input type="radio" name="p-desc" value="none"> non usare</label>
          <label class="row" style="font-weight:normal;margin:0"><input type="radio" name="p-desc" value="replace"> sostituisci la descrizione</label>
          <label class="row" style="font-weight:normal;margin:0"><input type="radio" name="p-desc" value="append"> aggiungi in fondo</label>
        </div>
        ${r.notes ? `<h4>Osservazioni</h4><blockquote>${esc(r.notes)}</blockquote>
          <label class="row small" style="font-weight:normal;margin:0"><input type="checkbox" id="p-notes"> aggiungi le osservazioni alla descrizione</label>` : ""}

        <h4>Tag</h4>
        <div class="chips">${r.tags.map(t => has.tags.has(t.toLowerCase())
          ? `<span class="chip" title="già presente">✓ ${esc(t)}</span>`
          : `<label class="chip"><input type="checkbox" data-ptag="${esc(t)}" checked> ${esc(t)}</label>`).join("") || `<span class="muted small">nessuno</span>`}</div>

        <h4>Campi</h4>
        ${r.fields.length ? r.fields.map((f, i) => {
          const old = has.fields[f.name.toLowerCase()];
          return `<label class="row small" style="font-weight:normal;margin:.15rem 0">
            <input type="checkbox" data-pfield="${i}" ${old ? "" : "checked"}>
            <span><strong>${esc(f.name)}</strong> — ${esc(f.description)}
            ${old ? `<span class="badge">${old.description ? "già presente" : "già presente: ne completa la descrizione"}</span>` : ""}</span></label>`;
        }).join("") : `<span class="muted small">nessuno</span>`}

        <h4>Collezioni di pagine</h4>
        ${r.collections.length ? `<div class="table-wrap"><table><thead><tr><th></th><th>Pagine</th><th>Tag</th><th>Commento</th><th></th></tr></thead>
          <tbody>${r.collections.map((x, i) => `<tr>
            <td>${exists(x) ? "✓" : `<input type="checkbox" data-pcoll="${i}" checked>`}</td>
            <td>${x.start === x.end ? x.start : `${x.start}–${x.end}`}</td>
            <td>${pageTagChip(x.tag, tags[x.tag].label)}</td>
            <td>${esc(x.comment)} ${x.deduced ? `<span class="badge warn" title="Gemini non ha visto queste pagine: l'ha dedotto dall'indice">dedotta</span>` : ""}
              ${exists(x) ? `<span class="badge">già presente</span>` : ""}</td>
            <td><button class="secondary small" data-pgo="${i}" type="button">Mostra</button></td></tr>`).join("")}</tbody></table></div>`
          : `<span class="muted small">nessuna</span>`}

        <div class="row" style="margin-top:1rem"><span class="spacer"></span>
          ${a.status === "nuova" ? `<button class="secondary" id="p-dismiss" type="button">Scarta la proposta</button>` : ""}
          <button id="p-apply" type="button">Applica i selezionati</button></div>
      </div>`;
    const defaultDesc = !r.description ? "none" : c.description.trim() ? "none" : "replace";
    $(`input[name="p-desc"][value="${defaultDesc}"]`, box).checked = true;
    $("#p-close").addEventListener("click", () => { box.innerHTML = ""; });
    $$("[data-pgo]", box).forEach(b => b.addEventListener("click", () => {
      const x = r.collections[+b.dataset.pgo]; select(x.start, x.end); show(x.start);
      thumbs.scrollIntoView({ block: "center", behavior: "smooth" });
    }));
    if ($("#p-dismiss")) $("#p-dismiss").addEventListener("click", e => busy(e.target, async () => {
      await api("POST", `/api/library/catalogs/${enc(c.id)}/analyses/${enc(a.id)}/dismiss`);
      toast("Proposta scartata: resta nell'archivio delle analisi."); renderCatalog(c.id);
    }));
    $("#p-apply").addEventListener("click", e => {
      if (hasUnsavedEdits()) { toast("Salva prima la scheda: le modifiche non salvate andrebbero perse.", 6000); return; }
      return busy(e.target, async () => {
        await api("POST", `/api/library/catalogs/${enc(c.id)}/analyses/${enc(a.id)}/apply`, {
          description: $('input[name="p-desc"]:checked', box).value,
          notes: !!($("#p-notes") && $("#p-notes").checked),
          tags: $$("[data-ptag]:checked", box).map(i => i.dataset.ptag),
          fields: $$("[data-pfield]:checked", box).map(i => +i.dataset.pfield),
          collections: $$("[data-pcoll]:checked", box).map(i => +i.dataset.pcoll),
        });
        toast("Proposta applicata alla scheda."); renderCatalog(c.id);
      });
    });
  };
  $$("[data-review]").forEach(link => link.addEventListener("click", e => {
    e.preventDefault();
    showProposal(analyses.find(a => a.id === link.dataset.review));
    $("#proposal").scrollIntoView({ block: "start", behavior: "smooth" });
  }));
  const pending = analyses.find(a => a.status === "nuova");
  if (pending) showProposal(pending);

  drawMarks();
  select(1, 1);
  show(1, false);
  refreshSend();
  promptState();

  // analisi in corso (pagina riaperta durante il lavoro)
  const running = await api("GET", `/api/library/catalogs/${enc(c.id)}/job`);
  if (running && running.status === "in_corso") watchJob($("#job"), running, j => { if (jobSucceeded(j)) renderCatalog(c.id); });
}

// ------------------------------------------------------------------ scelta dalla biblioteca (per i progetti)
/** Dialogo con ricerca e caselle: restituisce gli id scelti (vuoto se annullato). */
async function pickFromLibrary(exclude = []) {
  const data = await api("GET", "/api/library/catalogs");
  const items = data.catalogs.filter(c => !exclude.includes(c.id))
    .sort((a, b) => (a.folder + "/" + a.title).localeCompare(b.folder + "/" + b.title, "it", { sensitivity: "base" }));
  if (!items.length) { toast("Nella biblioteca non ci sono altri cataloghi."); return []; }
  const ok = await confirmDialog({ title: "Scegli dalla biblioteca", ok: "Aggiungi al progetto", html: `
    <input type="text" id="pick-search" placeholder="Cerca per nome, cartella o tag…">
    <div class="pick-list">${items.map(c => `
      <label class="row" style="font-weight:normal;margin:.2rem 0" data-hay="${esc(`${c.title} ${c.folder} ${c.tags.join(" ")}`.toLowerCase())}">
        <input type="checkbox" value="${esc(c.id)}"> <span><strong>${esc(c.title)}</strong>
        <span class="small muted">${esc(c.folder || "Biblioteca")} · ${c.page_count} pag.</span> ${tagBadges(c.tags)}</span></label>`).join("")}
    </div>
    <p class="hint">Le pagine segnate <em>da indicizzare</em> nella biblioteca diventano le pagine del PDF nel progetto.</p>` });
  return ok ? $$(".pick-list input:checked").map(i => i.value) : [];
}

// ricerca nel dialogo di scelta (il dialogo è sempre lo stesso: delega sul documento)
document.addEventListener("input", e => {
  if (e.target.id !== "pick-search") return;
  const words = e.target.value.toLowerCase().split(/\s+/).filter(Boolean);
  $$(".pick-list label").forEach(l => { l.hidden = !words.every(w => l.dataset.hay.includes(w)); });
});
