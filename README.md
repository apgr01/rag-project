# rag-project

Sistema RAG per l'interrogazione di cataloghi PDF (componenti, tabelle, immagini) tramite descrizioni semantiche generate da un modello multimodale.

## Flusso di lavoro

1. **Caricare il PDF sorgente** in `data/pdfs/`.

2. **Scrivere/verificare il primer del template** in `config/primers/`.
   Il primer è una descrizione (scritta a mano, con l'aiuto di un'AI in chat interattiva) della struttura del catalogo: come sono organizzati i capitoli, l'ordine dei contenuti, le convenzioni delle tabelle e dei codici. Un primer per ogni tipologia/template di catalogo — se cambia il layout, se ne scrive uno nuovo.

3. **Renderizzare le pagine** (`ingestion/render_pages.py`)
   PDF → immagini pagina in `data/images/`. Passo eseguito una volta per PDF, il risultato è cache.

4. **Generare le descrizioni per pagina** (`ingestion/describe_pages.py`)
   Per ogni pagina: immagine + primer → chiamata API Gemini → descrizione salvata in `data/descriptions/`.
   Lo script legge `manifest.json` per sapere quali pagine sono già state processate e salta quelle con stato `done`, permettendo di interrompere e riprendere il lavoro su più sessioni (utile con i limiti giornalieri del tier gratuito).

5. **Costruire l'indice RAG** (`rag/build_index.py`)
   Legge tutte le descrizioni in `data/descriptions/`, genera gli embedding e popola il vector store in `rag/store/`. Può essere rilanciato in qualsiasi momento senza richiamare Gemini, perché lavora solo sulle descrizioni già generate.

6. **Interrogare il sistema** (`rag/query.py`)
   Retrieval sul vector store + generazione della risposta finale.

## Struttura del progetto

```
rag-project/
├── data/
│   ├── pdfs/                    # PDF originali
│   ├── images/                  # immagini pagina renderizzate (cache)
│   └── descriptions/           # descrizioni generate da Gemini, una per pagina
│       └── <nome_pdf>/
│           ├── page_0001.json
│           ├── page_0002.json
│           └── manifest.json   # stato di avanzamento dell'ingestion
│
├── config/
│   └── primers/                # un file per ogni template di catalogo
│       └── <nome_template>.md
│
├── ingestion/                  # produzione delle descrizioni a partire dal PDF
│   ├── render_pages.py
│   └── describe_pages.py
│
├── rag/                        # indicizzazione e interrogazione
│   ├── build_index.py
│   ├── store/                  # vector database persistente
│   └── query.py
│
├── requirements.txt
└── README.md
```

## Note

- `ingestion/` e `rag/` sono fasi separate ma condividono lo stesso venv Python: `ingestion` chiama API esterne (rendering PDF, Gemini), `rag` gestisce embedding e retrieval. Le due fasi comunicano solo tramite il contenuto di `data/descriptions/`.
- Ogni file di descrizione contiene, oltre al testo generato, i metadati di provenienza (`primer_version`, `model`, `generated_at`) per facilitare debug e rigenerazioni parziali.  