# Changelog

Tutte le modifiche rilevanti al progetto sono documentate qui.
Formato ispirato a [Keep a Changelog](https://keepachangelog.com/it-IT/1.1.0/).

## [Non rilasciato] — 0.2.0

### Aggiunto
- **Gemini Snake**: mini gioco durante l'attesa dei lavori di Gemini nell'interfaccia, con
  l'avanzamento vero, ripartenza automatica se si perde e animazione finale che porta ai
  risultati. Si può nascondere (scelta ricordata).
- Il browser ricontrolla sempre i file dell'interfaccia (`Cache-Control: no-cache`), così dopo
  un aggiornamento non mescola pagine vecchie e script nuovi.
- **Progetti di indicizzazione**: più PDF con la stessa struttura, primer e specifica comuni,
  note e pagine proprie per singolo PDF (anche proposte da Gemini), configurazione guidata con
  pagine da più PDF e prova su ognuno, **CSV unico** con la colonna del file e CSV per PDF.
  Comandi `ragpdf project ...`; interfaccia organizzata per progetti. I cataloghi esistenti
  diventano automaticamente progetti da un PDF.
- **Pagina di ricerca** HTML autonoma generata dal CSV unico (`ricerca.html`).
- **Interfaccia grafica** (`ragpdf ui`): server locale e pagina web con configurazione guidata
  (miniature cliccabili, prompt visibile prima dell'invio, conferme, bozze, versioni),
  indicizzazione con avanzamento e annullamento, risultati e download.
- Ritmo complessivo adattivo alle quote di Gemini e opzione
  `gemini.max_requests_per_minute_total`.
- **Configurazione guidata** (`ragpdf setup`): Gemini guarda le pagine scelte dall'utente
  (estratto PDF, corrispondenza delle pagine nel prompt), propone primer e specifica, prova
  l'estrazione su alcune pagine e li corregge in base alle osservazioni scritte a parole.
  Versioni salvate, `/indietro`, ripresa, modifiche manuali, confronto con un CSV di
  riferimento, partenza da primer e specifica esistenti.
- Primer da pagine scelte: `ragpdf primer generate <doc> --pages 1,4,10-14`.
- Modalità di input per pagina (`indexing.input`): immagine + testo nativo (default),
  solo immagine, PDF di una pagina. Opzione `gemini.media_resolution`.
- Specifica predefinita per documento: `index`/`export --all` con cataloghi diversi,
  unione in `TUTTI.csv` se le colonne coincidono.
- Avviso per PDF scansionati (senza testo).
- Riepilogo del consumo (richieste e token) a fine indicizzazione e sessione guidata.
- Opzione `gemini.thinking_level` per regolare il "ragionamento" del modello.
- `ragpdf list` mostra anche la specifica predefinita di ogni documento.
- **Modalità indicizzazione**: da un PDF a un CSV secondo una *specifica* YAML scritta
  dall'utente (cosa rappresenta una riga, colonne, istruzioni libere, tabelle di
  conversione, CSV d'esempio). Estrazione strutturata pagina per pagina con JSON Schema,
  contesto di pagina ereditato, normalizzazione, duplicati, ordinamento, report anomalie,
  unione di più documenti (`TUTTI.csv`).
- **Bozza automatica del primer** a partire dal PDF intero (`ragpdf primer generate`).
- **Bozza automatica della specifica** da una descrizione a parole, con CSV d'esempio e
  pagine campione (`ragpdf spec draft`).
- Confronto con un CSV di riferimento (`ragpdf compare`): copertura, precisione e
  differenze riga per riga.
- Comando `ragpdf` con CLI guidata: `doctor`, `add`, `list`, `remove`, `primer`, `spec`,
  `index`, `export`, `rag`.
- Workspace dati (`data/`) separato dall'applicazione, con registro dei documenti
  (id leggibile e impronta SHA-256 per riconoscere i duplicati).
- Client Gemini con pool di chiavi, ritmo per chiave condiviso tra i thread, gestione
  degli errori per codice HTTP, arresto ordinato a quote esaurite.
- Client Gemini simulato (`RAGPDF_FAKE_GEMINI=1`) e 136 test automatici senza rete.
- Specifiche d'esempio (`examples/specs/`) e guida passo passo
  (`docs/GUIDA_INDICIZZAZIONE.md`), documento di architettura (`docs/ARCHITETTURA.md`).
- `pyproject.toml`, roadmap, changelog.

### Modificato
- Sovraccarico di Gemini (503 "high demand"): nuovi tentativi a intervallo fisso
  (`gemini.retry_delay_seconds`) invece che raddoppiati, senza far fallire le pagine; oltre
  `gemini.max_wait_minutes` il lavoro si ferma in modo ordinato.
- Interfaccia: i messaggi di servizio (sovraccarico, quote, rallentamenti) compaiono sotto la barra
  di avanzamento; *Ferma* interrompe subito anche le attese in corso.
- Errori di quota riconosciuti dai dettagli della risposta (al minuto / giornaliera, attesa
  suggerita): non fanno più fallire le pagine.
- Tutte le operazioni su PDF passano da un unico lucchetto (sicurezza tra thread).
- Le pagine dell'anteprima vengono riusate nell'indicizzazione se le regole non cambiano.
- Le colonne di contesto non sono più ripetute in ogni riga della risposta di Gemini; prompt
  di estrazione più compatto e con una regola esplicita sulla completezza delle righe.
- Configurando un catalogo a partire da primer e specifica esistenti si aggiornano gli stessi
  file invece di crearne copie; la conferma di sovrascrittura compare solo se cambiano.
- Risposte troncate o bloccate da Gemini: errore immediato e spiegato, senza tentativi.
- Codice raccolto nel pacchetto `src/ragpdf` (prima: pacchetti `common`, `ingestion`, `rag`).
- Modalità RAG divisa in fasi rilanciabili (`rag describe`, `rag build`, `rag ask`), con
  `upsert` nell'indice: le pagine rigenerate lo aggiornano.
- Prompt spostati in `configs/prompts/` e resi generici.
- Configurazione ripulita e validata; rimosse le opzioni inutilizzate.
- ChromaDB diventa opzionale: `pip install -e ".[rag]"`.
- README riscritto; `.gitignore` riorganizzato e `docs/` versionata.

### Rimosso
- `main.py` (sostituito dal comando `ragpdf`), `requirements.txt` (UTF-16, dipendenze
  mancanti), interfacce astratte non allineate alle implementazioni.
- File generati tracciati per errore: bytecode Python e database ChromaDB.

## [0.1.0] — 2026-10-06

### Aggiunto
- Modalità 1, RAG standard vision-first: rendering PDF → PNG, descrizione delle pagine con
  Gemini Vision guidata da un primer, manifest per riprendere il lavoro interrotto,
  indicizzazione in ChromaDB, query con citazione delle fonti.
- Rotazione automatica di più API key Gemini ed elaborazione in parallelo.
- CLI con i comandi `ingest`, `query`, `list`.
