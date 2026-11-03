# Changelog

Tutte le modifiche rilevanti al progetto sono documentate qui.
Formato ispirato a [Keep a Changelog](https://keepachangelog.com/it-IT/1.1.0/).

## [Non rilasciato]

### Aggiunto
- `pyproject.toml` con dipendenze dirette, extra di sviluppo e configurazione di ruff.
- `docs/ROADMAP.md`: visione, requisiti di progetto, piano della Fase 0, problemi noti.
- `docs/primer_prompt.md`: prompt per generare il primer di un documento.
- Questo changelog.

### Modificato
- README riscritto in base allo stato reale del codice.
- `.gitignore` riorganizzato; `docs/` ora è versionata.
- `.env.example` documentato.

### Rimosso
- `requirements.txt` (codificato in UTF-16 e con dipendenze mancanti).
- File generati tracciati per errore: bytecode Python e database ChromaDB.

## [0.1.0] — 2026-10-06

### Aggiunto
- Modalità 1, RAG standard vision-first: rendering PDF → PNG, descrizione delle pagine con
  Gemini Vision guidata da un primer, manifest per riprendere il lavoro interrotto,
  indicizzazione in ChromaDB, query con citazione delle fonti.
- Rotazione automatica di più API key Gemini ed elaborazione in parallelo.
- CLI con i comandi `ingest`, `query`, `list`.
