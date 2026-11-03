# rag-project

Sistema **RAG (Retrieval-Augmented Generation) vision-first** per interrogare documenti PDF
in linguaggio naturale — pensato per documenti in cui tabelle, schemi e immagini contano
quanto il testo (es. cataloghi tecnici di ricambi).

> Progetto personale a scopo didattico. Lo stato di avanzamento e i prossimi passi sono in
> [`docs/ROADMAP.md`](docs/ROADMAP.md).

## Come funziona

Invece di estrarre il testo dal PDF (che perde tabelle e disegni), ogni pagina viene
trasformata in immagine e "letta" da un modello multimodale:

```
PDF ──► immagini PNG ──► Gemini Vision + primer ──► descrizione testuale per pagina
                                                              │
                         domanda ──► ricerca semantica ◄── ChromaDB (embedding)
                                            │
                                            ▼
                              Gemini genera la risposta citando file e pagina
```

1. **Rendering** — ogni pagina del PDF diventa un PNG (PyMuPDF).
2. **Descrizione** — Gemini riceve l'immagine di *una* pagina insieme al **primer**:
   un testo che spiega com'è fatto il documento nel suo complesso (struttura, sigle,
   convenzioni delle tabelle). Senza il primer, una pagina isolata spesso non è
   interpretabile.
3. **Indicizzazione** — le descrizioni vengono salvate su disco e indicizzate in ChromaDB.
4. **Interrogazione** — la domanda recupera le pagine più pertinenti e Gemini risponde
   basandosi solo su quelle, citando le fonti.

L'avanzamento della fase 2 è tracciato in un `manifest.json` per documento: se il lavoro
si interrompe (es. quota giornaliera API esaurita), al rilancio riparte dalle pagine
mancanti.

## Requisiti

- Python **3.11+**
- Almeno una **API key Gemini** ([Google AI Studio](https://aistudio.google.com/apikey))

## Installazione

```bash
git clone https://github.com/apgr01/rag-project.git
cd rag-project

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -e .                 # aggiungi ".[dev]" per gli strumenti di sviluppo
```

Copia il file di esempio delle chiavi e inserisci la tua:

```bash
cp .env.example .env
```

## Utilizzo

### 1. Prepara il primer del documento

Il primer è un file Markdown in `configs/primers/` che descrive il documento. Si scrive una
volta per ogni *tipo* di documento (stesso layout ⇒ stesso primer). Per generarlo si può
usare un assistente AI con il prompt in [`docs/primer_prompt.md`](docs/primer_prompt.md),
allegando il PDF completo.

Poi indica il primer da usare in `configs/default.yaml` (`ingestion.primer_path`).

### 2. Ingestion

```bash
python main.py ingest --file "data/pdfs/Mio catalogo.pdf"
```

| Opzione   | Effetto                                                      |
|-----------|--------------------------------------------------------------|
| `--force` | Rigenera immagini e descrizioni anche se già presenti         |
| `--rpm N` | Pausa tra le richieste di ciascun worker (default 10/min)     |

### 3. Interrogazione

```bash
python main.py query "Qual è il codice del pistone per il motore D905?"
python main.py query "..." --source "Mio catalogo.pdf"   # limita a un documento
```

### 4. Documenti indicizzati

```bash
python main.py list
```

## Configurazione

`configs/default.yaml` contiene i parametri principali (modelli Gemini, DPI di rendering,
numero di risultati recuperati, percorso del primer). Viene validato all'avvio con Pydantic.

Le chiavi API stanno in `.env` (mai versionato). Se ne indichi più d'una
(`GEMINI_API_KEY_01`, `_02`, ...), l'ingestion le usa in parallelo e passa alla successiva
quando una esaurisce la quota.

## Struttura del progetto

```
rag-project/
├── main.py                  # CLI: ingest, query, list
├── common/                  # componenti condivisi
│   ├── paths.py             #   percorsi ancorati alla root del progetto
│   ├── config.py            #   caricamento e validazione della configurazione
│   ├── gemini_client.py     #   client Gemini con rotazione delle chiavi
│   └── vector_store.py      #   wrapper ChromaDB
├── ingestion/               # PDF → immagini → descrizioni → indice
├── rag/                     # retrieval + generazione della risposta
│   └── store/               #   database vettoriale (generato, non versionato)
├── configs/
│   ├── default.yaml         # configurazione
│   └── primers/             # un primer per tipo di documento
├── data/                    # PDF, immagini, descrizioni (generato, non versionato)
└── docs/                    # roadmap e documentazione
```

## Limitazioni note

Il progetto è in fase di consolidamento (vedi [roadmap](docs/ROADMAP.md), *Fase 0*).
Le principali limitazioni attuali:

- il primer è unico per tutto il progetto: per un documento di tipo diverso va cambiato
  in configurazione;
- il modello di embedding di default di ChromaDB è addestrato su testo inglese, mentre le
  descrizioni sono in italiano: la qualità del retrieval ne risente;
- rigenerare una pagina con `--force` non aggiorna la versione già presente nell'indice.
