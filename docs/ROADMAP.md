# Roadmap

Stato del progetto, decisioni prese e prossimi passi. Va aggiornato a ogni step.

## Visione

Uno strumento generico per estrarre informazioni da documenti PDF, modulare e resiliente,
basato su un unico pilastro:

> **Vision-first** — ogni PDF viene sempre renderizzato in immagini ed elaborato da un
> modello multimodale (Gemini), guidato da testi scritti dall'utente in linguaggio naturale.

| Modalità | Descrizione | Stato |
|---|---|---|
| **Indicizzazione** | PDF → CSV secondo una specifica dell'utente (righe, colonne, regole) | ✅ prima versione (`0.2.0`, 2026-11-12) |
| **RAG** | Descrizione delle pagine, ricerca semantica, risposte con citazione delle fonti | ✅ funzionante, da migliorare |
| **Struttura** | Albero del documento (capitoli, sezioni) in JSON | 🔜 da progettare |

## Requisiti di progetto

Decisi insieme e da rispettare in tutti gli step:

- **Utente non esperto** — il programma guida: se mancano dati (PDF, primer, specifica,
  chiavi) lo dice chiaramente e propone come procurarli o generarli.
- **Generalità** — il contenuto del CSV dipende da ciò che l'utente chiede (istruzioni,
  CSV d'esempio), non dal codice. Nessuna regola specifica di un catalogo nel programma.
- **Interfaccia grafica** nel browser per l'uso quotidiano, CLI curata e leggibile su Windows.
- **Embedding locali** multilingue per il RAG (niente quota per l'indicizzazione vettoriale).
- **Modularità** — fasi separate e rilanciabili; ogni chiamata a Gemini si paga una volta.
- **Didattico** — codice commentato, documentazione aggiornata, un commit per argomento.

## Fase 0 — Consolidamento

- [x] **Step 1 — Fondamenta della repo**: `pyproject.toml`, `.gitignore`, README, roadmap,
  changelog.
- [x] **Step 2 — Ristrutturazione**: pacchetto `ragpdf` con comando `ragpdf`; workspace
  dati separato dall'applicazione; registro documenti (id leggibile + SHA-256); manifest e
  esecuzione parallela comuni; client Gemini con pool di chiavi, ritmo condiviso ed errori
  per codice HTTP; prompt in `configs/prompts/`; `upsert` nel RAG; configurazione ripulita.
- [x] **Modalità indicizzazione** (anticipata per un bisogno concreto): specifica YAML,
  estrazione strutturata pagina per pagina, contesto ereditato, controlli e report anomalie,
  export CSV e unione di più documenti.
- [x] **Bozze automatiche**: primer da PDF intero o da pagine scelte; specifica da descrizione a parole + CSV
  d'esempio + pagine campione.
- [x] **Esperienza d'uso**: CLI guidata (`doctor`, `add`, `list`, domande quando manca
  qualcosa, stima dei tempi, ripresa), guida passo passo.
- [x] **Configurazione guidata** (`ragpdf setup`): l'utente sceglie le pagine significative,
  Gemini propone primer e specifica, anteprima con le stesse regole dell'indicizzazione,
  correzioni a parole in un ciclo con versioni, ripresa e salvataggio; partenza da file
  esistenti per cataloghi dello stesso tipo.
- [x] **Input per pagina configurabile** (`image+text` di default, `image`, `pdf`); testo
  nativo ripulito dal finto grassetto.
- [x] **Robustezza**: risposte troncate/bloccate riconosciute senza tentativi inutili; righe
  più corte (colonne di contesto non ripetute); riuso delle pagine dell'anteprima; specifica
  predefinita per documento.
- [x] **Interfaccia grafica** (`ragpdf ui`): server locale + pagina web; configurazione con
  miniature cliccabili, prompt visibile prima dell'invio, conferme con il numero di richieste,
  bozze, versioni, indicizzazione con avanzamento e annullamento, risultati e download.
- [x] **Progetti** con più PDF della stessa struttura: primer e specifica comuni, note e
  pagine per singolo PDF (anche proposte da Gemini), configurazione con pagine da più PDF e
  prova su ognuno, indicizzazione unica, **CSV unico** e CSV per PDF.
- [x] **Pagina di ricerca** HTML autonoma sul CSV unico (ricerca tollerante, filtri, link ai
  PDF, download dei risultati).
- [x] **Gemini Snake**: mini gioco nella pagina di attesa, per divertimento.
- [x] **Quote**: classificazione dai dettagli dell'errore e ritmo complessivo adattivo
  (emerso dalla prima indicizzazione reale con 10 chiavi dello stesso progetto).
- [x] **Consumo e costi**: riepilogo di richieste e token; opzione `thinking_level`.
- [x] **Test**: 136 test senza rete con Gemini simulato, più prove della sessione guidata in
  un terminale reale (pseudo-tty) e dell'interfaccia in Chromium (Playwright).

## Fase 1 — Biblioteca e collaudo sui cataloghi reali

- [x] **A · Biblioteca**: cartelle, tag, ricerca, scheda (descrizione, campi), collezioni di
  pagine con tag fissi, progetto dalla selezione con pagine e obiettivo precompilati.
- [x] **B · Analisi con Gemini** di un catalogo: prompt predefinito modificabile, pagine scelte
  dall'utente (estratto PDF) o PDF intero, gestione dei rifiuti per richieste troppo pesanti;
  proposta di descrizione, tag, campi e collezioni da accettare o correggere.
- [ ] **B2 · Analisi in blocco** dei cataloghi selezionati (selezione rapida o PDF intero,
  stesse istruzioni), con proposte da rivedere una per una.
- [x] **Dati presenti e tag *dati***: i campi della scheda diventano un inventario senza ordine;
  il tag di pagina *da indicizzare* diventa *dati*.
- [x] **Impostazioni globali del progetto** (descrizione, riga, colonne vincolanti con
  copertura); PDF del progetto che rimandano alla scheda; configurazione guidata che usa
  impostazioni e schede.
- [x] **Pagine per PDF scelte dalle collezioni** e *Aggiorna dalla biblioteca* (regola: ciò che
  descrive si legge in diretta, ciò che comanda si copia).
- [ ] **Progetto guidato** dalla scheda del PDF, al posto del raggruppamento in biblioteca.
- [ ] **Ritorno nella scheda**: progetti in cui il PDF è usato e stato dell'indicizzazione.
- [x] **C · Raggruppamento**: azione sui cataloghi selezionati; Gemini riceve le schede (e, a
  richiesta, una pagina d'esempio ciascuno) e risponde con uno schema rigido (gruppi con
  commento, tag comuni e colonne unificate; non accorpabili con il motivo); report in archivio
  con spostamenti, tag e creazione dei progetti.

## Altri passi della fase 0

- [ ] **Step 3 — Qualità del retrieval (RAG)**: embedding multilingue locali configurabili,
  soglia di rilevanza.

## Prossimi passi (da discutere)

Indicizzazione, in ordine di utilità prevista:

1. **Collaudo su cataloghi reali** e messa a punto dei prompt (`configs/prompts/`):
   setup, revisione, estrazione. Confrontare le modalità di input con `compare`.
2. ~~Confronto con un CSV di riferimento~~ → fatto: `ragpdf compare`.
3. **Rifare solo alcune pagine** dopo una correzione (es. solo quelle con anomalie),
   accettando consapevolmente regole miste; oggi una correzione propone di rifare tutto.
4. **Più pagine per richiesta** (`pages_per_request`), solo se le quote giornaliere
   diventassero il collo di bottiglia: dimezza le richieste ma aumenta il rischio di righe
   perse; da misurare.
5. **Rilettura di verifica** opzionale: seconda chiamata sulle pagine con anomalie o con
   poche righe rispetto alle attese.
6. **Classificazione rapida delle pagine** per saltare automaticamente copertine, indici e
   legende senza indicarle in `pages`.
7. **Stima dei costi e del tempo** più precisa, in base ai token effettivi (l'SDK li
   restituisce in `usage_metadata`).
8. **Cache del contesto** di Gemini per primer ed esempi, ripetuti identici in ogni pagina:
   ridurrebbe i costi sui cataloghi lunghi.

Altro:

- Modalità **Struttura**: indice gerarchico del documento; l'analisi del PDF intero usata
  per il primer è un buon punto di partenza.
- RAG con filtri sui metadati e ricerca ibrida (keyword per codici esatti + vettoriale).
- Interfaccia grafica: modalità RAG (domande sui cataloghi), confronto con un riferimento
  sull'indicizzazione completa.
- Pagina di ricerca: colonne visibili configurabili, ricerca per campo, pubblicazione su un
  sito (oggi è un file autonomo).

## Note dal collaudo

- **Quote per progetto.** I limiti di Gemini valgono per progetto Google Cloud, non per
  chiave: più chiavi dello stesso progetto non aumentano la capacità. Documentato nella
  guida.
- **Finto grassetto.** PyMuPDF lo estrae come parola ripetuta (`LOMBARDINILOMBARDINI`), non
  come lettere triplicate come pdfplumber: gestito eliminando i frammenti sovrapposti.

- Nel vecchio indice Finimpianti la colonna `Pagina_PDF_Reale` delle righe della **seconda**
  pagina di un motore riporta il numero della **prima** pagina del motore. Il nuovo
  programma registra la pagina in cui la riga compare davvero. Se serve il vecchio
  comportamento si può aggiungere un'opzione di output.
