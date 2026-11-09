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
├── app.py              strato applicativo: collega i pezzi, usato dalla CLI
├── setup_session.py    logica della configurazione guidata (giri, anteprima, salvataggio)
├── primer.py           bozza del primer da PDF intero o da un estratto
│
├── core/               infrastruttura comune alle due modalità
│   ├── paths.py        percorsi dell'applicazione
│   ├── config.py       configurazione validata (Pydantic)
│   ├── workspace.py    struttura della cartella dati
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
│   └── export.py       scrittura di CSV e report anomalie
│
└── rag/                modalità RAG (domande e risposte)
    ├── pipeline.py     descrizione pagine, indice, risposte
    └── vector_index.py wrapper ChromaDB (dipendenza opzionale)
```

Le dipendenze vanno in una sola direzione: `cli → app → indexing/rag → core`. Nessun modulo
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

## La configurazione guidata (`ragpdf setup`)

```
utente: pagine significative (1,4,10-14,...), obiettivo, CSV di riferimento
   │
   ▼
pdf_tools.build_subset_pdf ─► estratto PDF con solo quelle pagine
   │                           (pagine NON modificate: la corrispondenza con le
   │                            pagine originali è scritta nel prompt)
   ▼
Gemini (setup_initial.md) ─► JSON: primer, specifica YAML, riepilogo, domande, pagine di prova
   │
   ▼  giro N salvato in documents/<id>/setup/rounds/NN/
Extractor.preview ─► anteprima sulle pagine di prova, STESSE regole dell'indicizzazione vera
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
- risposte interrotte (lunghezza massima) o bloccate dai filtri → errore immediato della
  pagina con spiegazione, senza tentativi inutili;
- altri errori → subito, con messaggio chiaro.

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
| ChromaDB opzionale | Dipendenza obbligatoria | L'indicizzazione non lo usa e su alcune versioni di Python/Windows è difficile da installare |
| Estratto PDF con pagine scelte (setup/primer) | PDF intero sempre | Limiti di dimensione, costi e contenuto ripetitivo; l'utente sa quali pagine spiegano il catalogo |
| Corrispondenza delle pagine nel prompt | Numero originale stampato sulle pagine | Le pagine restano identiche all'originale; nessun elemento estraneo da interpretare |
| Immagine + testo nativo per pagina | Solo immagine / solo PDF | Struttura dall'immagine, caratteri esatti dal testo; configurabile e misurabile con `compare` |
