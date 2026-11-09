"""Pagina di ricerca HTML generata dal CSV unico di un progetto.

Un solo file HTML **autonomo** (dati incorporati, nessuna libreria esterna, funziona anche
senza connessione e senza il server): ``data/exports/<progetto>/ricerca.html``.

Funzioni:

- ricerca libera su tutte le colonne, tollerante a spazi, punti, trattini e barre
  (``fin 102 af`` trova ``FIN102AF``; ``6501044`` trova ``6501.044``); più parole = tutte,
  e in cima le righe che contengono la ricerca intera (es. il codice esatto);
- filtri automatici sulle colonne con pochi valori distinti (es. costruttore, file);
- ordinamento cliccando sulle intestazioni, risultati a pagine;
- link alla pagina del PDF da cui proviene ogni riga;
- download in CSV dei soli risultati filtrati.

I link ai PDF sono relativi alla posizione del file nel workspace; servita da ``ragpdf ui``
la pagina usa invece gli indirizzi del server.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from ragpdf.core.fileio import atomic_write_text, utc_now

if TYPE_CHECKING:
    from ragpdf.app import App, ProjectExport
    from ragpdf.core.projects import Project

MAX_FILTER_VALUES = 60
STANDALONE_PDF_LINK = "../../documents/{doc}/source.pdf#page={page}"


def build_search_page(app: App, project: Project, result: ProjectExport, pdf_link: str | None = None) -> Path:
    """Scrive (e restituisce) ``exports/<progetto>/ricerca.html``."""
    spec = app.project_spec(project)
    columns = result.index.columns
    rows = [[row.get(c, "") for c in columns] for row in result.index.rows]
    files = {doc.info.source_filename: doc.id for doc, _ in result.documents}

    filters = []
    for i, col in enumerate(columns):
        if col == spec.output.page_column:
            continue
        values = {r[i] for r in rows if r[i]}
        if 1 < len(values) <= MAX_FILTER_VALUES:
            filters.append(i)

    data = {
        "title": project.info.name,
        "generated": utc_now()[:16].replace("T", " "),
        "columns": columns,
        "rows": rows,
        "filters": filters,
        "page_col": columns.index(spec.output.page_column) if spec.output.page_column in columns else -1,
        "source_col": columns.index(spec.output.source_column) if spec.output.source_column in columns else -1,
        "files": files,
        "pdf_link": pdf_link or STANDALONE_PDF_LINK,
    }
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = TEMPLATE.replace("__TITLE__", _escape(project.info.name)).replace("__DATA__", payload)
    path = app.project_export_dir(project) / "ricerca.html"
    atomic_write_text(path, html)
    return path


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


TEMPLATE = r"""<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ricerca — __TITLE__</title>
<style>
  :root { --bg:#f5f6f8; --panel:#fff; --text:#1d2330; --muted:#667085; --border:#d9dde5; --accent:#2c5fd6; --soft:#e8eefc; --mark:#fff1a8; }
  @media (prefers-color-scheme: dark) { :root { --bg:#14171d; --panel:#1d222b; --text:#e6e9ef; --muted:#98a2b3; --border:#343b48; --accent:#7aa2ff; --soft:#23304d; --mark:#5c4d00; } }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--text); font:15px/1.45 system-ui, "Segoe UI", Roboto, sans-serif; }
  header { padding:14px 16px; background:var(--panel); border-bottom:1px solid var(--border); }
  h1 { font-size:1.2rem; margin:0; } .muted { color:var(--muted); font-size:.85rem; }
  main { max-width:1400px; margin:0 auto; padding:16px; }
  .bar { display:flex; gap:.5rem; flex-wrap:wrap; align-items:center; margin-bottom:.75rem; }
  input[type=search] { flex:1 1 320px; font:inherit; font-size:1.05rem; padding:.6rem .8rem; border:1px solid var(--border); border-radius:8px; background:var(--panel); color:var(--text); }
  select { font:inherit; padding:.45rem; border:1px solid var(--border); border-radius:6px; background:var(--panel); color:var(--text); max-width:16rem; }
  button { font:inherit; padding:.45rem .8rem; border-radius:6px; border:1px solid var(--accent); background:transparent; color:var(--accent); cursor:pointer; }
  .wrap { overflow:auto; background:var(--panel); border:1px solid var(--border); border-radius:8px; }
  table { border-collapse:collapse; width:100%; font-size:.88rem; }
  th, td { text-align:left; padding:.4rem .55rem; border-bottom:1px solid var(--border); white-space:nowrap; }
  th { position:sticky; top:0; background:var(--panel); cursor:pointer; user-select:none; }
  th.sorted::after { content:" \25B4"; } th.sorted.desc::after { content:" \25BE"; }
  tr:hover td { background:var(--soft); }
  mark { background:var(--mark); color:inherit; padding:0; }
  a { color:var(--accent); }
  .pager { display:flex; gap:.5rem; align-items:center; justify-content:center; margin:.75rem 0; }
  .empty { padding:2rem; text-align:center; color:var(--muted); }
</style>
</head>
<body>
<header><h1 id="title"></h1><div class="muted" id="info"></div></header>
<main>
  <div class="bar">
    <input type="search" id="q" placeholder="Cerca un codice, un modello, una descrizione… (es. FIN102AF, 3LD 450, filtro aria)" autofocus>
    <button id="reset" type="button">Azzera</button>
    <button id="export" type="button">Scarica risultati (CSV)</button>
  </div>
  <div class="bar" id="filters"></div>
  <p class="muted" id="count"></p>
  <div class="wrap"><table><thead id="head"></thead><tbody id="body"></tbody></table></div>
  <div class="pager" id="pager"></div>
</main>
<script>
"use strict";
const D = __DATA__;
const PAGE = 100;
const norm = s => String(s).toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/[\s.\-_/\\,;:]+/g, "");
const esc = s => String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const haystack = D.rows.map(r => norm(r.join(" ")));
const $ = id => document.getElementById(id);
let sortCol = -1, sortDesc = false, page = 0, current = [], exactCount = 0;

$("title").textContent = D.title;
$("info").textContent = `${D.rows.length.toLocaleString("it-IT")} righe · ${Object.keys(D.files).length} PDF · aggiornato il ${D.generated}`;

// filtri sulle colonne con pochi valori
const filterSelects = D.filters.map(i => {
  const values = [...new Set(D.rows.map(r => r[i]).filter(Boolean))].sort((a, b) => a.localeCompare(b, "it", {numeric: true}));
  const sel = document.createElement("select");
  sel.innerHTML = `<option value="">${esc(D.columns[i])}: tutti</option>` + values.map(v => `<option>${esc(v)}</option>`).join("");
  sel.dataset.col = i;
  sel.addEventListener("change", () => { page = 0; update(); });
  $("filters").appendChild(sel);
  return sel;
});

function update() {
  const tokens = $("q").value.split(/\s+/).map(norm).filter(Boolean);
  const whole = norm($("q").value);          // la ricerca intera, senza spazi: "FIN 102 AF" -> "fin102af"
  const active = filterSelects.filter(s => s.value).map(s => [+s.dataset.col, s.value]);
  const exact = [], others = [];
  for (let i = 0; i < D.rows.length; i++) {
    const r = D.rows[i];
    if (active.some(([c, v]) => r[c] !== v)) continue;
    if (tokens.some(t => !haystack[i].includes(t))) continue;
    // rilevanza: prima le righe che contengono la ricerca intera come sequenza (es. un codice esatto)
    (tokens.length > 1 && haystack[i].includes(whole) ? exact : others).push(r);
  }
  current = exact.concat(others);
  exactCount = tokens.length > 1 ? exact.length : 0;
  if (sortCol >= 0) {
    current.sort((a, b) => a[sortCol].localeCompare(b[sortCol], "it", {numeric: true}) * (sortDesc ? -1 : 1));
  }
  render(tokens);
}

function highlight(value, tokens) {
  const text = esc(value);
  if (!tokens.length) return text;
  const plain = String(value);
  for (const t of tokens) {
    // evidenzia la parola esatta se compare così com'è (senza normalizzazione)
    const idx = plain.toLowerCase().indexOf(t);
    if (idx >= 0 && t.length > 1) {
      return esc(plain.slice(0, idx)) + "<mark>" + esc(plain.slice(idx, idx + t.length)) + "</mark>" + esc(plain.slice(idx + t.length));
    }
  }
  return text;
}

function pdfLink(r) {
  if (D.page_col < 0 || D.source_col < 0) return "";
  const doc = D.files[r[D.source_col]];
  if (!doc || !r[D.page_col]) return "";
  const url = D.pdf_link.replace("{doc}", encodeURIComponent(doc)).replace("{page}", encodeURIComponent(r[D.page_col]));
  return `<a href="${url}" target="_blank" rel="noopener">apri p.${esc(r[D.page_col])}</a>`;
}

function render(tokens) {
  const withLink = D.page_col >= 0 && D.source_col >= 0;
  $("head").innerHTML = "<tr>" + D.columns.map((c, i) =>
    `<th data-i="${i}" class="${i === sortCol ? "sorted" + (sortDesc ? " desc" : "") : ""}">${esc(c)}</th>`).join("") +
    (withLink ? "<th>PDF</th>" : "") + "</tr>";
  const pages = Math.max(1, Math.ceil(current.length / PAGE));
  page = Math.min(page, pages - 1);
  const slice = current.slice(page * PAGE, page * PAGE + PAGE);
  $("body").innerHTML = slice.length ? slice.map(r => "<tr>" + r.map(v => `<td>${highlight(v, tokens)}</td>`).join("") +
    (withLink ? `<td>${pdfLink(r)}</td>` : "") + "</tr>").join("")
    : `<tr><td class="empty" colspan="${D.columns.length + 1}">Nessun risultato</td></tr>`;
  $("count").textContent = `${current.length.toLocaleString("it-IT")} risultati` +
    (exactCount && sortCol < 0 ? ` (i primi ${exactCount.toLocaleString("it-IT")} contengono la ricerca esatta)` : "") +
    (current.length > PAGE ? ` · pagina ${page + 1} di ${pages}` : "");
  $("pager").innerHTML = current.length > PAGE
    ? `<button ${page === 0 ? "disabled" : ""} id="prev">&larr; Precedenti</button><button ${page >= pages - 1 ? "disabled" : ""} id="next">Successivi &rarr;</button>` : "";
  if ($("prev")) $("prev").onclick = () => { page--; render(tokens); scrollTo(0, 0); };
  if ($("next")) $("next").onclick = () => { page++; render(tokens); scrollTo(0, 0); };
}

$("head").addEventListener("click", e => {
  const th = e.target.closest("th[data-i]"); if (!th) return;
  const i = +th.dataset.i;
  sortDesc = sortCol === i ? !sortDesc : false; sortCol = i; update();
});
let timer;
$("q").addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => { page = 0; update(); }, 120); });
$("reset").addEventListener("click", () => { $("q").value = ""; filterSelects.forEach(s => s.value = ""); sortCol = -1; page = 0; update(); });
$("export").addEventListener("click", () => {
  const cell = v => /[",\n;]/.test(v) ? `"${String(v).replace(/"/g, '""')}"` : v;
  const csv = [D.columns, ...current].map(r => r.map(cell).join(",")).join("\r\n");
  const blob = new Blob(["﻿" + csv], {type: "text/csv;charset=utf-8"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = "risultati_ricerca.csv"; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
});
update();
</script>
</body>
</html>
"""
