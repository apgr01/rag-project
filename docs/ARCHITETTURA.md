# Architettura

Come è organizzato il codice e **perché**. Utile per orientarsi prima di modificarlo.

## Principi

1. **Vision-first.** Ogni pagina viene trasformata in immagine e letta da Gemini. Estrarre
   il testo dal PDF è più economico, ma perde tabelle, icone e disposizione grafica, che nei
   cataloghi tecnici sono il contenuto principale.
2. **Il contenuto lo decide l'utente, non il codice.** Il programma non sa nulla di motori,
   codici o cataloghi. Tutto ciò che riguarda un tipo di documento sta in file di testo
   modificabili: il **primer** (com'è fatto il documento) e la **specifica** (cosa estrarre).
   Un nuovo catalogo non richiede codice nuovo.
3. **Ogni chiamata a Gemini si paga una volta sola.** I risultati di ogni pagina vengono
   salvati su disco con un manifest; tutto ciò che viene dopo (unione, controlli, CSV) è
   deterministico e si può rifare all'infinito senza quota.
4. **Dati dell'utente separati dall'applicazione.** Il codice sta nel repository, i dati nel
   *workspace* (`data/`). Si aggiorna il programma senza toccare i dati, e viceversa.
5. **Moduli piccoli con un solo compito**, collegati da uno strato applicativo (`app.py`)
   indipendente dall'interfaccia.

## Mappa dei moduli

```
src/ragpdf/
├── cli.py              interfaccia a riga di comando (solo input/output)
├── ui.py               domande, messaggi, tabelle nel terminale
├── cli_setup.py        dialogo della configurazione guidata (ragpdf setup)
├── web/                interfaccia grafica (ragpdf ui)
│   ├── server.py       API JSON (FastAPI) sopra app.py e setup_session.py
│   ├── jobs.py         lavori in background: avanzamento, annullamento
│   └── static/         pagina HTML, CSS e JavaScript (senza librerie esterne);
│                       app.js progetti, library.js biblioteca,
│                       snake.js il mini gioco mostrato durante l'attesa
├── app.py              strato applicativo: collega i pezzi, usato dalla CLI
├── setup_session.py    logica della configurazione guidata (giri, anteprima, salvataggio)
├── primer.py           bozza del primer da PDF intero o da un estratto
├── library_analysis.py analisi di un catalogo della biblioteca con Gemini (proposte)
├── guided_project.py   progetto guidato: dalla scheda di un PDF a un progetto, con Gemini
│
├── core/               infrastruttura comune alle due modalità
│   ├── paths.py        percorsi dell'applicazione
│   ├── config.py       configurazione validata (Pydantic)
│   ├── workspace.py    struttura della cartella dati
│   ├── projects.py     progetti: più PDF, impostazioni per PDF
│   ├── library.py      biblioteca: cartelle, tag, dati presenti, collezioni di pagine
│   ├── documents.py    registro dei documenti (id, SHA-256, primer)
│   ├── render.py       PDF -> PNG con cache
│   ├── pdf_tools.py    estratti PDF, testo nativo ripulito
│   ├── manifest.py     stato per pagina, per riprendere il lavoro
│   ├── page_runner.py  esecuzione parallela con arresto ordinato
│   ├── gemini.py       client Gemini: pool di chiavi, ritmo, errori
│   ├── fake_gemini.py  client simulato (test, prove senza quota)
│   ├── prompts.py      caricamento dei prompt da configs/prompts/
│   └── fileio.py       scrittura atomica di file
│
├── indexing/           modalità INDICIZZAZIONE (PDF -> CSV)
│   ├── spec.py         la specifica: modello, validazione, impronta
│   ├── spec_draft.py   bozza della specifica scritta da Gemini
│   ├── prompt.py       prompt e JSON Schema per ogni pagina
│   ├── extractor.py    Gemini pagina per pagina -> JSON salvati
│   ├── assemble.py     JSON -> righe del CSV, controlli, anomalie
│   ├── compare.py      confronto con un CSV di riferimento
│   ├── search_page.py  pagina di ricerca HTML autonoma sul CSV unico
│   └── export.py       scrittura di CSV e report anomalie
│
└── rag/                modalità RAG (domande e risposte)
    ├── pipeline.py     descrizione pagine, indice, risposte
    └── vector_index.py wrapper ChromaDB (dipendenza opzionale)
```

Le dipendenze vanno in una sola direzione: `cli / web → app → indexing/rag → core`. Nessun modulo
di `core` conosce le modalità, e le due modalità non si conoscono tra loro.

## Il flusso dell'indicizzazione

```
             ┌──────────── una volta per documento ────────────┐
PDF ─► add ─► documents/<id>/source.pdf       primer generate ─► primers/<nome>.md
                                              spec draft/new  ─► specs/<nome>.yaml

index:
  render.py      pagine selezionate ─► pages/page-NNNN.png   (cache, saltate se presenti)
  prompt.py      specifica + primer ─► prompt + JSON Schema
  extractor.py   per ogni pagina, in parallelo:
                   Gemini(immagine, prompt, schema) ─► extractions/<spec>/page-NNNN.json
                   manifest.json aggiornato (done / failed)
  assemble.py    tutti i JSON ─► contesto ereditato, normalizzazione, duplicati,
                                 ordinamento, controlli
  export.py      ─► exports/<spec>/<id>.csv  +  <id>_anomalie.csv
```

### Perché l'output strutturato

Gemini riceve un **JSON Schema** costruito dalle colonne della specifica e deve rispondere
con un oggetto conforme (`page_context`, `rows`, `notes`, ...). Rispetto a chiedere "scrivi
un CSV" evita problemi di separatori, virgolette e righe spezzate, e rende la risposta
verificabile. Tutti i valori sono stringhe: tipi e formati vengono controllati dopo, in modo
deterministico, da `assemble.py`.

### Perché il contesto di pagina

Molti documenti mettono un'informazione in intestazione (es. il modello di motore) e poi una
tabella che non la ripete, a volte proseguendo sulla pagina successiva. Le colonne marcate
`context: true` vengono chieste anche come `page_context`; in fase di assemblaggio una riga
senza quel valore lo prende dalla sua pagina o, se manca, dall'ultima pagina che lo aveva.
In questo modo le pagine si elaborano **in parallelo** (nessuna dipende dalla precedente
durante la chiamata a Gemini) e la continuità si ricostruisce dopo.

### Perché l'impronta della specifica

Il manifest salva un'impronta (hash) di tutto ciò che influenza le risposte: righe, colonne,
istruzioni, mappature, esempi, primer, modello. Se cambia dopo un'estrazione, mescolare
pagine vecchie e nuove produrrebbe un CSV incoerente: il programma lo rileva e chiede se
rifare. Le modifiche che non toccano le risposte (sezione `output`, `required`) non
cambiano l'impronta: basta `ragpdf export`.

## Progetti

Un **progetto** è l'unità di lavoro: uno o più PDF con la stessa struttura, un primer e una
specifica comuni, un CSV unico.

```
progetto ── documenti: alfa, beta, gamma
         ├── impostazioni globali: descrizione, riga, colonne, altre indicazioni (decise dall'utente)
         ├── primer + specifica  (comuni: stesse colonne per tutti)
         ├── impostazioni per PDF: note, pagine   ── IndexingSpec.for_document()
         └── export: CSV unico + CSV per PDF + anomalie con il file + ricerca.html
```

- **Impostazioni globali** (`ProjectInfo.description/row/fields/instructions`): la tabella che il
  progetto deve produrre. La configurazione guidata le mette nel prompt come vincolo e le
  colonne della specifica vengono confrontate con quelle decise (`compare_columns`: nomi senza
  badare a maiuscole e spazi, ordine compreso). Il confronto compare nel giro della
  configurazione e, per la specifica salvata, nel riepilogo del progetto. Le impostazioni non
  entrano direttamente nelle regole (l'impronta resta quella di primer e specifica): cambiarle
  non rende vecchi i risultati, ma segnala che la specifica va riallineata.
- **Schede della biblioteca nel prompt della configurazione**: descrizione, dati presenti e
  collezioni di ciascun PDF, lette in diretta (sono descrittive). Le pagine proposte da
  mostrare a Gemini vengono dalla mappa (`App.suggested_context_refs`: indice, legenda, esempi,
  a turno tra i PDF).
- **Pagine da indicizzare dalle collezioni** (`DocumentSettings.collections` + `pages`): le pagine
  sono una **copia** delle collezioni scelte, perché fanno parte delle regole. Regola generale:
  *ciò che descrive si legge in diretta, ciò che comanda si copia*. `App.library_changes`
  confronta la copia con la biblioteca di oggi (collezioni cambiate o eliminate) e
  `update_pages_from_library` ricopia su richiesta; i PDF già indicizzati diventano vecchi per
  l'impronta e si rifanno solo loro.

- **Note per PDF**: le differenze di un catalogo si esprimono come istruzioni aggiuntive solo
  per lui, invece di complicare le regole comuni. Le colonne non cambiano, così il CSV resta
  unico. L'impronta dei risultati è per documento: cambiare la nota di un PDF fa rifare solo
  quello.
- **Righe uguali di PDF diversi** vengono tenute (lo stesso codice in due cataloghi è
  un'informazione); i duplicati dentro un PDF sono già tolti in fase di assemblaggio.
- **Contesto ereditato** solo dentro ciascun PDF: ogni documento viene assemblato a sé, poi i
  risultati vengono uniti.
- **Compatibilità**: i documenti delle versioni precedenti senza progetto diventano progetti
  da un PDF all'avvio, con la loro sessione di configurazione; poi ricevono la scheda della
  biblioteca e da lì in avanti non vengono più toccati.

## Biblioteca

I PDF **vivono nella biblioteca**; i progetti li usano ma non li possiedono. Togliere un PDF da
un progetto o eliminare un progetto non cancella il PDF né i suoi risultati; un PDF si elimina
dalla biblioteca solo se nessun progetto lo usa.

```
data/documents/<id>/library.yaml    scheda: cartella, tag, descrizione, dati presenti, collezioni
data/library/folders.yaml           cartelle (anche vuote)
```

- **Cartelle**: percorsi testuali (`Motori/Lombardini`), un catalogo sta in una sola cartella.
  Rinominare o spostare una cartella aggiorna le schede che contiene.
- **Tag del catalogo**: liberi, senza doppioni (maiuscole e minuscole non contano). Filtrano
  i cataloghi di tutte le cartelle.
- **Dati presenti** (`fields` nella scheda): l'inventario dei dati indicizzabili del catalogo
  (nome + descrizione facoltativa), senza ordine. Servono a capire il PDF e a proporre
  progetti; le colonne del CSV le decide il progetto.
- **Collezioni di pagine**: intervalli continui con un **tag fisso** (`PAGE_TAGS` in
  `core/library.py`) e un commento. I tag sono fissi perché il programma li usa: creando un
  progetto dalla biblioteca le pagine `dati` diventano le pagine del PDF nel
  progetto, le pagine `esempio` quelle proposte nella configurazione guidata, dati presenti e
  descrizioni l'obiettivo proposto. Una pagina può stare in più collezioni.
- **Analisi con Gemini** (`library_analysis.py`): estratto PDF delle pagine scelte (o PDF
  intero) e prompt in due parti, le **istruzioni** modificabili dall'utente
  (`configs/prompts/library_analyze.md`, personali in `data/library/analysis_prompt.md`) e la
  **cornice** fissa (`library_analyze_frame.md`: corrispondenza delle pagine, tag disponibili,
  scheda attuale, regole sui numeri di pagina, formato della risposta) che viene *dopo* le
  indicazioni, le racchiude tra delimitatori e dichiara di prevalere su di esse: modificare le
  indicazioni non può cambiare il formato. Risposta a schema rigido (`response_json_schema`,
  tag delle collezioni come `enum`), poi **ricontrollata in modo rigoroso** con modelli Pydantic
  (`extra="forbid"`, `strict=True`): un campo mancante o in più, un tipo sbagliato o un tag
  inesistente rendono la risposta *non valida* (`InvalidAnswerError`), archiviata con la
  risposta originale ma mai applicata. Con la struttura a posto, le singole voci inutilizzabili
  (pagine fuori dal documento, campi doppi) vengono scartate e segnalate. Il risultato
  è una proposta salvata in `documents/<id>/analyses/`, applicata solo per le voci scelte e
  senza doppioni. Le richieste troppo pesanti (400/413 con messaggi sui limiti) diventano
  `RequestTooLargeError`, senza nuovi tentativi.
- **Progetto guidato** (`guided_project.py`): parte da un catalogo e da una richiesta (riga,
  colonne, CSV d'esempio, testo). Una sola richiesta, solo testo, con le schede dei cataloghi
  **elaborati** (al massimo 300; si può restringere a una cartella). Cataloghi e sezioni hanno
  codici (`C3`, `C3/2`, `P/1`) che nello schema sono `enum`; stessa cornice fissa e stessa
  convalida rigorosa dell'analisi, più i controlli sui codici (sconosciuti, ripetuti, il
  catalogo di partenza tra i candidati: non valida) e le correzioni di contenuto segnalate
  (colonne doppie, coperture con nomi che non sono colonne, sezioni di un altro catalogo). Il
  report (`data/library/guided/<id>.json`) diventa un progetto con le impostazioni globali, le
  pagine dalle sezioni scelte e il CSV d'esempio come riferimento (`ProjectInfo.reference`).
  Sostituisce il raggruppamento di più cataloghi selezionati, che partiva dai cataloghi invece
  che dalla tabella voluta.
- **Scala**: l'interfaccia riceve l'elenco completo in una risposta (centinaia di schede sono
  pochi KB) e filtra nel browser; le miniature vengono create solo quando servono e restano
  in cache (`documents/<id>/thumbs/`).

## La configurazione guidata (`ragpdf setup`)

```
utente: pagine significative anche da più PDF (alfa 1-4, beta 12), obiettivo, CSV di riferimento
   │
   ▼
pdf_tools.build_subset_pdf ─► estratto PDF con solo quelle pagine
   │                           (pagine NON modificate: la corrispondenza con le
   │                            pagine originali è scritta nel prompt)
   ▼
Gemini (setup_initial.md) ─► JSON: primer, specifica YAML, note per PDF, riepilogo, domande,
   │                            pagine di prova (numerate come nell'estratto)
   │
   ▼  giro N salvato in documents/<id>/setup/rounds/NN/
Extractor.preview ─► anteprima sulle pagine di prova (almeno una per PDF), STESSE regole
   │                 dell'indicizzazione vera, con le note di ciascun PDF
   │                 + confronto con il riferimento (stesso file, stesse pagine)
   ▼
utente: correzione a parole ──► Gemini (setup_revise.md) riceve estratto, primer e specifica
   ▲                            attuali, risultati della prova, correzioni precedenti
   └──────────── nuovo giro ◄───┘
   │
  /ok ─► primer e specifica nel workspace, predefiniti per il documento;
         pagine dell'anteprima riusate nell'indicizzazione se l'impronta coincide
```

Scelte:

- **Estratto scelto dall'utente invece del PDF intero.** Il PDF intero può superare i limiti
  della Files API (50 MB) ed è in gran parte ripetitivo: poche pagine scelte bene (indice,
  legende, schede tipo) danno un primer più mirato, più veloce ed economico. Un estratto PDF
  (non immagini) mantiene il testo nativo, utile per sigle e codici.
- **Una sola risposta strutturata per primer e specifica.** Primer e specifica restano due
  file distinti (il primer descrive un *tipo* di documento ed è riusabile per indicizzazioni
  diverse), ma l'utente dialoga con un solo assistente.
- **I risultati della prova tornano a Gemini.** Nella revisione Gemini vede le righe
  estratte, le anomalie e le differenze con il riferimento: può correggere errori che
  l'utente non ha ancora notato.
- **Le correzioni precedenti sono sempre nel prompt**, per evitare che una revisione annulli
  le precedenti.
- **Ogni giro è un file su disco**: `/indietro`, ripresa dopo interruzioni, modifiche
  manuali registrate come nuovi giri.
- **Specifica non valida**: si chiede una correzione mostrando gli errori, una volta; se
  fallisce ancora, la versione precedente resta quella valida.
- **Pagine di prova numerate come nell'estratto**: con più PDF un numero di pagina "originale"
  sarebbe ambiguo.
- **Note per PDF incrementali**: Gemini indica solo le note da aggiungere, cambiare o togliere
  (esplicitamente); una nota non citata resta, così una dimenticanza del modello non cancella
  le note scritte dall'utente.

## La pagina di ricerca

`search_page.py` genera un unico file HTML con i dati incorporati in JSON e un piccolo script:
nessun server, nessuna libreria, si apre ovunque. La ricerca normalizza testo e query
(minuscole, senza accenti, senza spazi/punti/trattini) e confronta per sottostringa: semplice
e adatta a codici scritti in modi diversi. Le righe che contengono la query intera vanno in
cima. I filtri nascono dalle colonne con 2-60 valori distinti. Con circa 30.000 righe il file
pesa ~4,5 MB e la ricerca richiede pochi millisecondi; oltre qualche centinaio di migliaia di
righe converrebbe un indice vero (es. SQLite FTS) servito da un server.

## L'interfaccia grafica (`ragpdf ui`)

```
browser (static/app.js) ──HTTP JSON──► web/server.py ──► App, SetupSession, Extractor (come la CLI)
        ▲                                   │
        └──── GET /api/jobs/<id> ◄── web/jobs.py: thread in background + RunControl
                (ogni secondo)               (avanzamento per pagina, annullamento)
```

Scelte:

- **Server locale + pagina web**, non un file HTML da solo: il browser non può leggere le
  chiavi nel `.env` né elaborare i PDF; il server ascolta solo su `127.0.0.1`.
- **FastAPI + JavaScript semplice** invece di Streamlit: Streamlit riesegue la pagina a ogni
  interazione, scomodo per bozze, conferme e versioni; un'interfaccia scritta a mano lascia
  il pieno controllo del flusso e resta leggibile senza strumenti di compilazione.
- **Nessuna logica nell'interfaccia**: le API chiamano gli stessi oggetti della riga di
  comando, quindi i due modi d'uso producono gli stessi file e possono essere alternati.
- **Lavori in background, uno per documento**: chiamate lunghe e indicizzazione non
  bloccano il server; `RunControl` porta l'avanzamento pagina per pagina e la richiesta di
  annullamento fino a `run_pages`.
- **Niente parte verso Gemini da sola**: gli endpoint che usano Gemini (`start`, `revise`,
  `preview`, `index`) sono separati da quelli che preparano o mostrano (`setup`, `prompt`,
  `plan`), e la pagina li chiama solo dopo una conferma con il numero di richieste.
- **PyMuPDF e thread**: miniature e lavori possono arrivare insieme, quindi tutte le
  aperture di PDF passano da un unico lucchetto (`PDF_LOCK`).

## Cosa riceve Gemini per ogni pagina

`indexing.input` in configurazione:

| Modalità | Allegati | Note |
|---|---|---|
| `image+text` (default) | PNG della pagina + testo nativo estratto nel prompt | immagine per struttura e associazioni, testo per caratteri esatti |
| `image` | PNG della pagina | per PDF con testo estratto inaffidabile |
| `pdf` | PDF di una pagina | Gemini usa testo e grafica da sé; risoluzione non controllabile |

Il testo nativo viene ripulito (`pdf_tools.page_text`): i frammenti duplicati e sovrapposti
del *finto grassetto* vengono eliminati prima di ricomporre le righe; tra frammenti lontani
sulla stessa riga si inserisce ` | ` per separare le colonne.

Una pagina per richiesta: con più pagine insieme crescono il rischio di righe perse e quello
di superare la lunghezza massima della risposta, e un errore costa più pagine. Le colonne di
contesto non vengono ripetute nelle righe (Gemini le dà una volta in `page_context`) per
tenere corte le risposte.

## Il client Gemini

`core/gemini.py` gestisce N chiavi API come un pool:

- ogni chiave ha un *prossimo istante libero*; il ritmo `requests_per_minute_per_key` è
  rispettato **per chiave e tra tutti i thread** (un unico lock);
- `429` al minuto → pausa della chiave; `429` giornaliero → chiave esclusa; quando tutte
  sono escluse → `QuotaExhaustedError`, che ferma l'elaborazione in modo ordinato;
- `5xx` e rete → nuovo tentativo con attesa crescente (backoff esponenziale);
- `503` sovraccarico ("high demand") → nuovo tentativo a intervallo fisso, senza consumare
  tentativi: è un problema momentaneo di Google, uguale per tutte le chiavi, e raddoppiare le
  attese allungherebbe solo i tempi; oltre `max_wait_minutes` → `ServiceUnavailableError`, che
  ferma il lavoro in modo ordinato come le quote esaurite;
- annullamento: ogni lavoro registra nel proprio thread l'evento "Ferma" (`cancellation`), che il
  client controlla prima di ogni tentativo e durante ogni attesa;
- risposte interrotte (lunghezza massima) o bloccate dai filtri → errore immediato della
  pagina con spiegazione, senza tentativi inutili;
- altri errori → subito, con messaggio chiaro.

**Quote.** Il tipo di quota superata (al minuto / giornaliera) e l'attesa suggerita si leggono
dai *dettagli* dell'errore 429 (`QuotaFailure.quotaId`, `RetryInfo.retryDelay`). Poiché le
quote valgono per progetto, oltre al limite per chiave c'è un **ritmo complessivo adattivo**
(`_AdaptiveLimit`): a ogni episodio di quota superata scende al 90% delle richieste riuscite
nell'ultimo minuto, poi risale del 10% ogni 20 successi. Gli errori di quota non fanno
fallire le pagine: oltre 15 minuti di quota esaurita il lavoro si ferma in modo ordinato.

PDF fino a 15 MB vengono inviati direttamente nella richiesta; oltre, tramite la Files API
(caricati con la chiave del tentativo, attesi fino allo stato ACTIVE, poi eliminati).

`orologio` e `sleep` sono iniettabili: i test verificano rotazione e attese senza aspettare
davvero. `FakeGeminiClient` ha la stessa interfaccia (`LLMClient`) e permette di provare
l'intero programma senza rete (`RAGPDF_FAKE_GEMINI=1`).

## Scelte e alternative scartate

| Scelta | Alternativa | Motivo |
|---|---|---|
| Specifica in YAML | Prompt libero unico | YAML separa ciò che va a Gemini (descrizioni) da ciò che controlla il programma (tipi, formati, output) ed è validabile |
| Una chiamata per pagina | Più pagine per chiamata | Pagine dense producono molte righe: una per chiamata riduce il rischio di righe perse e rende il lavoro riprendibile |
| Contesto ricostruito dopo | Passare la pagina precedente a Gemini | Raddoppierebbe i costi e impedirebbe il parallelismo |
| CLI con argparse | Typer/Rich | Nessuna dipendenza in più; output semplice leggibile su ogni console Windows |
| Interfaccia web locale (FastAPI + JS) | Streamlit/Gradio, app desktop | Pieno controllo di conferme, bozze e versioni; nessuna compilazione; si apre in qualsiasi browser |
| ChromaDB opzionale | Dipendenza obbligatoria | L'indicizzazione non lo usa e su alcune versioni di Python/Windows è difficile da installare |
| Estratto PDF con pagine scelte (setup/primer) | PDF intero sempre | Limiti di dimensione, costi e contenuto ripetitivo; l'utente sa quali pagine spiegano il catalogo |
| Corrispondenza delle pagine nel prompt | Numero originale stampato sulle pagine | Le pagine restano identiche all'originale; nessun elemento estraneo da interpretare |
| Immagine + testo nativo per pagina | Solo immagine / solo PDF | Struttura dall'immagine, caratteri esatti dal testo; configurabile e misurabile con `compare` |
