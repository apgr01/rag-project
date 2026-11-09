# Guida: indicizzare un catalogo PDF in CSV

Questa guida accompagna passo per passo dall'installazione al CSV finale. Gli esempi usano
un catalogo ricambi motore, ma il procedimento è lo stesso per qualunque PDF.

I comandi sono scritti per **Windows PowerShell**; su macOS/Linux cambia solo
l'attivazione dell'ambiente virtuale (`source .venv/bin/activate`).

**In breve:**

```powershell
ragpdf setup "C:\cataloghi\Catalogo 1 LOMBARDINI rel 1501.pdf"   # configurazione guidata
ragpdf index lombardini                                          # indicizzazione completa
```

---

## 0. Installazione (una volta sola)

```powershell
git clone https://github.com/apgr01/rag-project.git
cd rag-project
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
copy .env.example .env
notepad .env
```

Nel file `.env` inserisci le chiavi, una per riga:

```
GEMINI_API_KEY_01=la-tua-prima-chiave
GEMINI_API_KEY_02=la-tua-seconda-chiave
```

Poi verifica:

```powershell
ragpdf doctor --online
```

> **Se PowerShell non permette di attivare l'ambiente** ("l'esecuzione di script è
> disabilitata"), esegui una volta `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.
>
> **Ogni volta che apri un nuovo terminale** entra nella cartella del progetto e riattiva
> l'ambiente con `.venv\Scripts\Activate.ps1`.

> **Quote e chiavi.** I limiti di Gemini (richieste al minuto e al giorno) valgono per
> **progetto** Google Cloud, non per singola chiave. Più chiavi aumentano davvero la
> capacità solo se appartengono a progetti diversi. Il programma rispetta comunque il
> ritmo impostato e, se una chiave esaurisce la quota, passa alle altre o si ferma in modo
> ordinato: rilanciando, riparte da dove era.

---

## 1. Configurazione guidata: `ragpdf setup`

È il modo consigliato. Non serve leggere tutto il catalogo né scrivere a mano cosa
indicizzare: lo propone Gemini, tu correggi a parole guardando un'anteprima del risultato.

```powershell
ragpdf setup "C:\cataloghi\Catalogo 1 LOMBARDINI rel 1501.pdf"
```

Il PDF viene aggiunto al workspace (`data\documents\catalogo-1-lombardini-rel-1501\`) con un
identificativo ricavato dal nome; nei comandi successivi basta una parte dell'id, purché
non sia ambigua: `lombardini`. Se il PDF è già stato aggiunto, si usa quello.

### Le tre domande iniziali

**1. Pagine da mostrare a Gemini.** Non serve inviare tutto il catalogo: bastano le pagine
che servono a *capirlo*. Scrivile come `1,4,10-14,102,105` (massimo 40):

- l'indice o l'elenco delle sezioni;
- le pagine di legenda, "come leggere il catalogo", tabelle di sigle;
- 2-3 pagine tipo per ogni layout diverso (ad esempio le due pagine di una scheda motore).

Premendo invio si accetta la proposta automatica (prime pagine + alcune distribuite). Gemini
riceve un PDF ridotto con solo quelle pagine e, nel prompt, la corrispondenza con le pagine
originali.

**2. Cosa vuoi ottenere.** Descrivi il CSV a parole, ad esempio:

> Una riga per ogni coppia codice FIN / codice OEM di ogni motore. Per ogni riga:
> costruttore, motore, cilindri, alesaggio, corsa, versione dall'intestazione; tipo di
> componente; codice FIN; codice OEM. Se il codice OEM manca lascia vuoto.

Oppure premi invio e lascia che sia Gemini a proporre. Per testi lunghi scrivi `/note`: si
apre il Blocco note.

**3. CSV di riferimento (facoltativo).** Un indice già fatto, anche di altri cataloghi:
Gemini ne copia nomi e ordine delle colonne e lo stile dei valori. Se contiene righe di
**questo** catalogo (stesso nome file nella colonna del file d'origine), ogni anteprima viene
anche **confrontata** con esso. Puoi trascinare il file nel terminale o incollare il percorso
copiato con "Copia come percorso": le virgolette vanno bene.

### Il ciclo di correzione

Gemini analizza le pagine (un minuto o due) e mostra:

- un riepilogo di cosa ha capito e cosa propone;
- cosa rappresenta una riga e le colonne (`*` = obbligatoria, *(intestazione)* = dato di
  pagina ereditato se la pagina non lo ripete);
- eventuali domande;
- una **prova di estrazione** su 2-4 pagine: righe per pagina, prime righe del CSV, controlli
  e, se c'è il riferimento, copertura/precisione con le righe mancanti o in più.

A questo punto scrivi **a parole** cosa non va, ad esempio:

```
Correzione> il TIPO_COMPONENTE deve essere in maiuscolo come nel riferimento
Correzione> a pagina 39 manca l'ENGINE KIT
Correzione> la versione deve includere la freccia, scritta --> o <--
```

Gemini aggiorna primer e specifica tenendo conto anche delle correzioni precedenti e dei
risultati della prova, poi la prova viene rifatta. Ripeti finché il risultato ti convince.

| Comando | Cosa fa |
|---|---|
| `/ok` | salva primer e specifica, poi propone l'indicizzazione completa |
| `/anteprima 40-41,77` | rifà la prova su altre pagine (utile per controllare layout diversi) |
| `/pagine 1-6,40-41,90` | cambia le pagine che Gemini guarda per capire il documento |
| `/mostra primer` · `/mostra specifica` · `/mostra righe` | mostra il testo nel terminale |
| `/apri csv` · `/apri primer` · `/apri specifica` | apre il file (il CSV in Excel) |
| `/modifica primer` · `/modifica specifica` | correggi tu nel Blocco note; la modifica diventa una nuova versione |
| `/note` | scrivi una correzione lunga nel Blocco note |
| `/indietro` | torna alla versione precedente |
| `/riferimento FILE` | imposta o cambia il CSV di riferimento |
| `/esci` | esce senza salvare: riprendi più tardi con lo stesso comando |
| `/aiuto` | elenco dei comandi |

Ogni versione resta salvata: puoi interrompere (anche con Ctrl+C) e riprendere con
`ragpdf setup lombardini`. Per ricominciare da zero: `ragpdf setup lombardini --new`.

### Al salvataggio (`/ok`)

- il primer va in `data\primers\<nome>.md` e la specifica in `data\specs\<nome>.yaml`
  (il nome predefinito è l'id del documento; se esistono già viene chiesto se
  sovrascriverli);
- primer e specifica diventano quelli **predefiniti del documento**: nei comandi successivi
  non serve indicarli;
- le pagine dell'ultima prova contano già come indicizzate, se le regole non sono cambiate:
  non si pagano due volte.

### Cataloghi dello stesso tipo

Per un secondo catalogo dello stesso editore/layout non serve ripartire da zero: alla domanda
*"Da dove vuoi partire?"* scegli *primer e specifica esistenti*. Si va subito all'anteprima
sul nuovo catalogo e si corregge solo se serve.

```powershell
ragpdf setup "C:\cataloghi\Catalogo 2 LOMBARDINI.pdf" --primer catalogo-1-lombardini-rel-1501 --spec catalogo-1-lombardini-rel-1501
```

---

## 2. Indicizza tutto il documento

```powershell
ragpdf index lombardini
```

Il programma mostra quante pagine deve elaborare e una stima del tempo, poi chiede conferma.
Al termine scrive il CSV in `data\exports\<specifica>\<documento>.csv`, con accanto il report
`<documento>_anomalie.csv`.

**Se si interrompe** (quota giornaliera finita, rete, Ctrl+C) non perdi nulla: rilancia lo
stesso comando più tardi e riparte dalle pagine mancanti. `ragpdf list` mostra l'avanzamento.

Più documenti in una volta, ciascuno con la propria specifica:

```powershell
ragpdf index --all -y
```

(`-y` evita le domande, utile per lasciarlo andare da solo.)

**Prima di tutto il catalogo, una prova mirata** su pagine che non hai visto nell'anteprima
è una buona idea: `ragpdf index lombardini --pages 120-123`.

---

## 3. Controlla e rigenera i CSV

Il report anomalie elenca:

| livello | significato |
|---|---|
| `errore` | pagina fallita (dopo i tentativi), oppure campo obbligatorio vuoto |
| `avviso` | valore che non rispetta il formato, il tipo o i valori ammessi |
| `info` | note di Gemini su dubbi di lettura; pagine non ancora elaborate (es. dopo una prova con `--pages`) |

Le righe con anomalie restano comunque nel CSV: decidi tu cosa farne.

Se cambi solo la sezione `output` della specifica (separatore, ordinamento, nomi delle
colonne pagina/file) o i controlli (`required`, `pattern`), **non serve richiamare
Gemini**:

```powershell
ragpdf export --all
```

Esporta ogni documento con la sua specifica e, se hanno le stesse colonne, crea anche
`TUTTI.csv` con l'unione di tutti.

**Hai un indice fatto a mano per lo stesso catalogo?** Misura il risultato completo:

```powershell
ragpdf compare lombardini --reference "C:\indici\indice_componenti_all.csv"
```

Mostra **copertura** (righe del riferimento trovate) e **precisione** (righe prodotte presenti
nel riferimento) sulle pagine elaborate; le differenze riga per riga finiscono in
`..._confronto.csv`. Con `--key FIN_CODE,OEM_CODE,MOTORE` scegli quali colonne confrontare.

**Vuoi correggere dopo l'indicizzazione completa?** Riprendi `ragpdf setup lombardini`,
correggi e salva. Al successivo `ragpdf index` il programma si accorge che le regole sono
cambiate e chiede se rifare le pagine: i risultati ottenuti con regole diverse non vengono
mai mescolati.

---

## 4. Percorso manuale (alternativa)

Gli stessi file si possono creare un pezzo alla volta:

```powershell
ragpdf add "C:\cataloghi\Catalogo.pdf"
ragpdf primer generate catalogo --pages 1-6,40-41,90           # bozza del primer
ragpdf spec draft componenti --doc catalogo --request "..."     # bozza della specifica
ragpdf spec new componenti --from finimpianti-fin-oem           # oppure da un modello
ragpdf spec check componenti --show-prompt catalogo --page 40   # il prompt esatto per una pagina
ragpdf index catalogo --spec componenti --pages 40-43           # prova su poche pagine
```

Primer e specifica sono file di testo modificabili con qualsiasi editor
(`data\primers\*.md`, `data\specs\*.yaml`). Il formato della specifica è spiegato nei commenti
di `examples\specs\base.yaml`:

```yaml
row: >                       # cosa rappresenta UNA riga: la scelta più importante
  Una riga per ogni codice articolo presente nella pagina.
columns:                     # le colonne, nell'ordine del CSV
  - name: MOTORE
    description: Modello del motore, dall'intestazione della pagina.
    context: true            # dato di pagina: se manca, preso dalla pagina precedente
    required: true           # se vuoto, finisce nel report anomalie
  - name: CODICE
    description: Codice articolo, esattamente come stampato.
    pattern: '^FIN.*'        # controllo di formato (espressione regolare)
instructions: |              # regole libere, come le diresti a una persona
  - Se il codice OEM non è presente lascia la cella vuota.
mappings:                    # tabelle di conversione (es. sigla -> nome)
  componenti:
    values: {AF: Filtro aria, OP: Pompa olio}
examples:
  csv: esempio.csv           # CSV d'esempio come riferimento di formato
pages: "20-"                 # salta le pagine introduttive (meno richieste, meno tempo)
output:
  delimiter: ","             # ";" se apri il CSV con Excel in italiano
  page_column: Pagina_PDF_Reale
  source_column: File_origine
  sort_by: [CODICE]
```

---

## 5. Impostazioni utili (`configs\default.yaml`)

| Impostazione | Default | Quando cambiarla |
|---|---|---|
| `gemini.model` | `gemini-3.6-flash` | se `doctor --online` segnala un modello non disponibile |
| `gemini.requests_per_minute_per_key` | 10 | in base ai limiti del tuo piano |
| `gemini.media_resolution` | (vuoto) | `high` se Gemini legge male testi minuscoli |
| `render.dpi` | 200 | 250-300 per testo molto piccolo (immagini più pesanti) |
| `indexing.input` | `image+text` | `image` per PDF con testo estratto inaffidabile, `pdf` da provare in alternativa |

`indexing.input` decide cosa riceve Gemini per ogni pagina:

- `image+text`: immagine della pagina **più** il testo nativo del PDF. L'immagine dà la
  struttura (tabelle, icone, associazioni), il testo i caratteri esatti dei codici (riduce
  confusioni come 0/O, 8/B, 1/I). Per le scansioni si usa solo l'immagine.
- `image`: solo l'immagine.
- `pdf`: la pagina come PDF di una pagina.

Quale funzioni meglio dipende dai cataloghi: prova le modalità sulle stesse pagine e
confronta i risultati con `ragpdf compare`.

---

## Problemi frequenti

| Problema | Soluzione |
|---|---|
| `Nessuna chiave API Gemini` | Crea `.env` da `.env.example` e inserisci almeno una chiave |
| `quota giornaliera esaurita` | Aspetta il reset della quota (o usa chiavi di altri progetti) e rilancia: riprende da dove era |
| Troppe pagine di fila fallite | Problema generale (nome del modello, chiave non valida): `ragpdf doctor --online` |
| `la risposta ha superato la lunghezza massima` | Pagina con moltissime righe: riduci le colonne o rendi le istruzioni più selettive |
| Il CSV ha righe inventate o mancanti | Riprendi `ragpdf setup` e descrivi l'errore; oppure rendi più precise `row`, `description`, `instructions` |
| Testo piccolo letto male | Aumenta `render.dpi` o imposta `gemini.media_resolution: high` |
| Excel mostra tutto in una colonna | Nella specifica `delimiter: ";"`, poi `ragpdf export` |
| Vuoi provare senza consumare quota | `$env:RAGPDF_FAKE_GEMINI=1` (risposte simulate); per tornare normale chiudi il terminale |
| Errore imprevisto | Rilancia con `$env:RAGPDF_DEBUG=1` per vedere i dettagli tecnici |
