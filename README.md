# rag-project — `ragpdf`

Strumento a riga di comando per **estrarre dati da documenti PDF con Gemini**, pensato per
documenti in cui tabelle, schemi e immagini contano quanto il testo (es. cataloghi tecnici
di ricambi).

Due modalità, che condividono la stessa base:

| Modalità | A cosa serve | Comandi |
|---|---|---|
| **Indicizzazione** | Produrre un **CSV** dal PDF, con le righe e le colonne che decidi tu | `ragpdf index`, `ragpdf export` |
| **RAG** | Fare **domande** in linguaggio naturale sui documenti | `ragpdf rag ...` |

> Progetto personale a scopo didattico. Stato e prossimi passi: [`docs/ROADMAP.md`](docs/ROADMAP.md).
> Come è fatto dentro: [`docs/ARCHITETTURA.md`](docs/ARCHITETTURA.md).

## Come funziona

Il PDF non viene "letto" come testo (si perderebbero tabelle e disegni): ogni pagina
diventa un'immagine e la legge Gemini, guidato da due documenti scritti in linguaggio
naturale.

- **Primer**: spiega *com'è fatto il documento* (struttura, sigle, convenzioni delle
  tabelle). Serve perché Gemini vede una pagina alla volta. Il programma ne genera una bozza
  da solo.
- **Specifica di indicizzazione**: spiega *cosa vuoi nel CSV* (cosa rappresenta una riga,
  quali colonne, regole come "se manca il codice lascia vuoto"). Gemini può scriverne una
  bozza partendo da una tua descrizione.

```
PDF ─► immagini delle pagine ─► Gemini (pagina + primer + specifica) ─► righe JSON per pagina
                                                                            │
                         CSV + report anomalie ◄─ assemblaggio e controlli ◄─┘
```

Il lavoro è **riprendibile**: se si interrompe (quota API esaurita, Ctrl+C, rete), al
rilancio riparte dalle pagine mancanti. Cambiare il formato del CSV non richiede di
richiamare Gemini.

## Installazione

Requisiti: **Python 3.11+** e almeno una **chiave API Gemini**
([Google AI Studio](https://aistudio.google.com/apikey)).

```bash
git clone https://github.com/apgr01/rag-project.git
cd rag-project
python -m venv .venv
```

Attiva l'ambiente virtuale (Windows PowerShell: `.venv\Scripts\Activate.ps1`; macOS/Linux:
`source .venv/bin/activate`), poi:

```bash
pip install -e .
```

Per usare anche la modalità RAG: `pip install -e ".[rag]"`.

Copia `.env.example` in `.env` e inserisci la tua chiave (`GEMINI_API_KEY_01=...`). Con più
chiavi (`_02`, `_03`, ...) le pagine vengono elaborate in parallelo.

Controlla che sia tutto pronto:

```bash
ragpdf doctor --online
```

## Uso rapido: dal PDF al CSV

```bash
ragpdf add "C:\cataloghi\Catalogo Lombardini.pdf"      # aggiunge il PDF (e propone il primer)
ragpdf primer generate lombardini                       # bozza del primer: poi rileggila
ragpdf spec draft ricambi --doc lombardini --request "Una riga per ogni codice ..."
ragpdf spec check ricambi                               # controlla la specifica
ragpdf index lombardini --spec ricambi --pages 40-43    # prova su poche pagine
ragpdf index lombardini --spec ricambi                  # tutto il documento
```

Il CSV finisce in `data/exports/<specifica>/<documento>.csv`, con accanto
`<documento>_anomalie.csv` (righe e pagine da controllare a mano).

La guida completa, passo per passo e con esempi, è in
[`docs/GUIDA_INDICIZZAZIONE.md`](docs/GUIDA_INDICIZZAZIONE.md).

## Comandi

| Comando | Cosa fa |
|---|---|
| `ragpdf doctor [--online]` | Verifica chiavi, cartelle, documenti (e una richiesta reale a Gemini) |
| `ragpdf add <pdf>...` | Aggiunge PDF al workspace |
| `ragpdf list` | Documenti, primer associati, avanzamento delle indicizzazioni |
| `ragpdf remove <doc>` | Elimina un documento e i suoi risultati |
| `ragpdf primer generate <doc>` | Bozza del primer scritta da Gemini leggendo tutto il PDF |
| `ragpdf primer set <doc> <primer>` | Associa un primer esistente (o un file `.md`) |
| `ragpdf primer list` | Elenca i primer |
| `ragpdf spec new <nome> [--from modello]` | Nuova specifica da un modello (`base`, `finimpianti-fin-oem`) |
| `ragpdf spec draft <nome> --request "..."` | Bozza della specifica scritta da Gemini |
| `ragpdf spec check <nome> [--show-prompt <doc>]` | Valida la specifica e mostra cosa verrà chiesto a Gemini |
| `ragpdf index <doc>... --spec <nome>` | Indicizza ed esporta il CSV (`--pages`, `--force`, `--all`, `-y`) |
| `ragpdf export [--all] --spec <nome>` | Rigenera i CSV senza chiamare Gemini; con più documenti anche `TUTTI.csv` |
| `ragpdf rag describe/build/ask` | Modalità domande e risposte |

Ogni comando ha il suo aiuto: `ragpdf <comando> --help`.

Per provare tutto **senza chiavi e senza consumare quota**, imposta la variabile
d'ambiente `RAGPDF_FAKE_GEMINI=1`: le risposte di Gemini vengono simulate.

## Dove stanno i file

```
rag-project/
├── src/ragpdf/          codice (core/, indexing/, rag/, cli.py)
├── configs/
│   ├── default.yaml     configurazione (modelli, DPI, ritmo delle richieste, ...)
│   └── prompts/         prompt inviati a Gemini, modificabili
├── examples/            primer e specifiche d'esempio
├── tests/               test automatici (Gemini simulato)
├── docs/                documentazione
└── data/                IL TUO WORKSPACE (non versionato)
    ├── documents/<id>/  PDF, immagini delle pagine, risultati per pagina
    ├── primers/         primer
    ├── specs/           specifiche di indicizzazione
    └── exports/         CSV prodotti
```

## Sviluppo

```bash
pip install -e ".[dev]"
pytest
ruff check src tests
```
