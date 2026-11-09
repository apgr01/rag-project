# Roadmap

Stato del progetto, decisioni prese e prossimi passi. Va aggiornato a ogni step.

## Visione

Uno strumento generico per estrarre informazioni da documenti PDF, modulare e resiliente,
basato su un unico pilastro:

> **Vision-first** — ogni PDF viene sempre renderizzato in immagini ed elaborato da un
> modello multimodale (Gemini), guidato da testi scritti dall'utente in linguaggio naturale.

| Modalità | Descrizione | Stato |
|---|---|---|
| **Indicizzazione** | PDF → CSV secondo una specifica dell'utente (righe, colonne, regole) | ✅ prima versione (`0.2.0`) |
| **RAG** | Descrizione delle pagine, ricerca semantica, risposte con citazione delle fonti | ✅ funzionante, da migliorare |
| **Struttura** | Albero del documento (capitoli, sezioni) in JSON | 🔜 da progettare |

## Requisiti di progetto

Decisi insieme e da rispettare in tutti gli step:

- **Utente non esperto** — il programma guida: se mancano dati (PDF, primer, specifica,
  chiavi) lo dice chiaramente e propone come procurarli o generarli.
- **Generalità** — il contenuto del CSV dipende da ciò che l'utente chiede (istruzioni,
  CSV d'esempio), non dal codice. Nessuna regola specifica di un catalogo nel programma.
- **Interfaccia: solo CLI** per ora, curata e leggibile su Windows.
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
- [x] **Bozze automatiche**: primer da PDF intero; specifica da descrizione a parole + CSV
  d'esempio + pagine campione.
- [x] **Esperienza d'uso**: CLI guidata (`doctor`, `add`, `list`, domande quando manca
  qualcosa, stima dei tempi, ripresa), guida passo passo.
- [x] **Test**: 50 test senza rete con Gemini simulato.
- [ ] **Step 3 — Qualità del retrieval (RAG)**: embedding multilingue locali configurabili,
  soglia di rilevanza.

## Prossimi passi (da discutere)

Indicizzazione, in ordine di utilità prevista:

1. **Collaudo su cataloghi reali** e messa a punto di prompt e specifiche d'esempio.
2. ~~Confronto con un CSV di riferimento~~ → fatto: `ragpdf compare`.
3. **Rilettura di verifica** opzionale: seconda chiamata sulle pagine con anomalie o con
   poche righe rispetto alle attese.
4. **Classificazione rapida delle pagine** per saltare automaticamente copertine, indici e
   legende senza indicarle in `pages`.
5. **Stima dei costi e del tempo** più precisa, in base ai token effettivi.

Altro:

- Modalità **Struttura**: indice gerarchico del documento; l'analisi del PDF intero usata
  per il primer è un buon punto di partenza.
- RAG con filtri sui metadati e ricerca ibrida (keyword per codici esatti + vettoriale).
- Eventuale interfaccia grafica, riutilizzando `app.py`.

## Note dal collaudo

- Nel vecchio indice Finimpianti la colonna `Pagina_PDF_Reale` delle righe della **seconda**
  pagina di un motore riporta il numero della **prima** pagina del motore. Il nuovo
  programma registra la pagina in cui la riga compare davvero. Se serve il vecchio
  comportamento si può aggiungere un'opzione di output.
