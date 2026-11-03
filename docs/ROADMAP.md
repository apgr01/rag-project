# Roadmap

Stato del progetto, decisioni prese e prossimi passi. Va aggiornato a ogni step.

## Visione

Un framework RAG generico per PDF, modulare e resiliente, basato su un unico pilastro:

> **Vision-first** — ogni PDF viene sempre renderizzato in immagini ed elaborato da un
> modello multimodale, che ne trascrive e descrive il contenuto (testo, tabelle, schemi).
> È la base di estrazione universale del sistema.

Sopra questa base il sistema offre tre **modalità**:

| Modalità | Descrizione | Stato |
|---|---|---|
| 1 — RAG standard | Una pagina = un chunk, ricerca semantica, risposta con citazione delle fonti | ✅ funzionante (`v0.1.0`) |
| 2 — Indicizzazione automatica | Estrazione della struttura gerarchica del documento (albero JSON) e output strutturati per tabelle e schede | ⏳ da progettare |
| 3 — RAG avanzato | Chunk arricchiti con metadati dalla Modalità 2, filtri, routing per sezione, ricerca ibrida (keyword + vettoriale) | 🔜 futuro |

## Requisiti di progetto

Decisi insieme e da rispettare in tutti gli step:

- **Utente non esperto** — il programma deve guidare l'utente: se mancano dati
  (PDF, primer, chiavi API) deve dirlo chiaramente e proporre come procurarli o generarli,
  invece di fallire con un errore tecnico.
- **Interfaccia: solo CLI** per ora, ma curata (messaggi chiari, comandi guidati).
- **Embedding locali** multilingue (niente quota API consumata per l'indicizzazione).
- **Modularità** — fasi della pipeline separate e rilanciabili singolarmente; modalità
  intercambiabili.
- **Didattico** — codice leggibile e commentato, documentazione aggiornata a ogni step,
  un commit per step.

## Fase 0 — Consolidamento (in corso)

Obiettivo: rendere solida, usabile e documentata la Modalità 1 prima di costruire le altre.
Ogni step è preceduto da un brainstorming e chiuso da uno o più commit sul branch
`refactor/fase-0`.

- [x] **Step 1 — Fondamenta della repo**
  `pyproject.toml` al posto di `requirements.txt`, `.gitignore` corretto (docs versionata,
  file generati esclusi), README riscritto sullo stato reale, roadmap, changelog.
- [ ] **Step 2 — Pipeline a fasi e registro documenti**
  Fasi `render → describe → index → query` separate e rilanciabili; registro dei documenti
  con primer e metadati per ciascun PDF; `upsert` nell'indice; prompt fuori dal codice;
  configurazione ripulita (rimozione delle opzioni non usate); selezione reale delle
  modalità tramite registry.
- [ ] **Step 3 — Qualità del retrieval**
  Embedding multilingue locali configurabili; soglia di rilevanza; reindicizzazione senza
  chiamate a Gemini.
- [ ] **Step 4 — Esperienza d'uso**
  CLI guidata: verifica dell'ambiente (`doctor`), stato dei documenti, chat interattiva,
  flusso "aggiungi documento" che accompagna l'utente dal PDF al documento interrogabile,
  inclusa la **generazione automatica della bozza del primer** da rivedere.
- [ ] **Step 5 — Test**
  Test automatici senza chiamate API (client Gemini simulato).

## Problemi noti (da risolvere nella Fase 0)

| Problema | Step |
|---|---|
| Embedding di default di ChromaDB solo inglese, descrizioni in italiano | 3 |
| `add()` invece di `upsert()`: le pagine rigenerate non aggiornano l'indice | 2 |
| Un solo primer globale in configurazione, invece di uno per tipo di documento | 2 |
| `--rpm` è per worker: con N chiavi il ritmo reale è circa N×rpm | 2 |
| Opzioni di configurazione non usate (`chunk_size`, `chunk_overlap`, `ingestion.mode`, `rag.mode`) | 2 |
| Interfacce `BaseIngestor` / `BaseQueryEngine` non allineate alle implementazioni; modalità scelte in modo fisso in `main.py` | 2 |
| Prompt scritti nel codice e specifici per "cataloghi industriali" | 2 |
| `GeminiClient`: nuovo client a ogni chiamata, errori riconosciuti tramite stringhe, nessun backoff | 2 |
| Il comando `list` richiede chiavi API e database anche se legge solo i manifest | 4 |
| Le pagine fallite oltre il numero massimo di tentativi vengono ignorate senza avviso | 4 |
| Nessun logging strutturato, nessun test | 4, 5 |

## Modalità 2 — Indicizzazione automatica (da progettare)

Obiettivo: estrarre la struttura logica del documento senza necessariamente fare RAG.

- [ ] Ingestor strutturato per l'analisi macro del documento.
- [ ] Indice gerarchico (`catalog_tree.json`): capitoli, sezioni, categorie, schede.
- [ ] Output strutturati (JSON Schema / Pydantic) per tabelle e schede tecniche.

Nota: l'analisi del documento intero necessaria per generare il primer (Step 4) è molto
vicina a quella che serve per estrarne la struttura: le due cose vanno progettate insieme.

## Modalità 3 — RAG avanzato (futuro)

- [ ] Metadati sui chunk (capitolo, sezione, categoria) derivati dalla Modalità 2.
- [ ] Query engine con filtri sui metadati e routing per sezione.
- [ ] Ricerca ibrida: keyword per codici esatti + ricerca vettoriale.
