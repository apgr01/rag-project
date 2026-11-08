# Guida: indicizzare un catalogo PDF in CSV

Questa guida accompagna passo per passo dall'installazione al CSV finale. Gli esempi usano
un catalogo ricambi motore, ma il procedimento è lo stesso per qualunque PDF.

I comandi sono scritti per **Windows PowerShell**; su macOS/Linux cambia solo
l'attivazione dell'ambiente virtuale (`source .venv/bin/activate`).

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

---

## 1. Aggiungi il PDF

```powershell
ragpdf add "C:\cataloghi\Catalogo 1 LOMBARDINI rel 1501.pdf"
```

Il PDF viene copiato nel workspace (`data\documents\catalogo-1-lombardini-rel-1501\`) con un
identificativo ricavato dal nome. Nei comandi successivi basta una parte dell'id, purché
non sia ambigua: `lombardini`.

Il programma chiede subito del **primer**: scegli *Genera ora una bozza*. Puoi anche farlo
dopo (passo 2). Più PDF insieme: `ragpdf add file1.pdf file2.pdf ...`.

---

## 2. Il primer: com'è fatto il documento

```powershell
ragpdf primer generate lombardini
```

Gemini legge tutto il PDF e scrive una bozza in `data\primers\<nome>.md`. **Aprila e
rileggila** (`notepad data\primers\catalogo-1-lombardini-rel-1501.md`). Controlla
soprattutto:

- che la struttura del catalogo (sezioni, pagine) sia giusta;
- il significato di sigle e icone;
- la sezione *Punti da verificare*, dove Gemini indica i dubbi.

Correggi direttamente il file: la qualità del primer incide molto sui risultati.

**Cataloghi con lo stesso layout** (stesso editore) possono usare lo stesso primer:

```powershell
ragpdf primer set kubota catalogo-1-lombardini-rel-1501
```

---

## 3. La specifica: cosa vuoi nel CSV

La specifica è un file YAML in `data\specs\<nome>.yaml`. Ci sono tre modi per crearla.

### a) Farla scrivere a Gemini (consigliato per iniziare)

Descrivi a parole cosa vuoi. Se hai un CSV già fatto a mano, passalo come esempio:
Gemini ne copierà nomi e ordine delle colonne.

```powershell
ragpdf spec draft componenti --doc lombardini --example-csv "C:\indici\indice_componenti.csv" --request "Una riga per ogni coppia codice FIN / codice OEM di ogni motore. Per ogni riga: costruttore, motore, cilindri, alesaggio, corsa, versione dall'intestazione della pagina; tipo di componente; codice FIN; codice OEM. Se il codice OEM manca lascia vuoto."
```

Per descrizioni lunghe scrivi la richiesta in un file di testo e usa
`--request-file richiesta.txt`. Con `--pages 40-41` scegli quali pagine mostrare a Gemini
(di default alcune pagine centrali).

### b) Partire da un modello

```powershell
ragpdf spec new componenti --from finimpianti-fin-oem
```

Modelli disponibili: `base` (generico, commentato) e `finimpianti-fin-oem` (l'indice
componenti dei cataloghi Finimpianti). `ragpdf spec list` li elenca.

### c) Scriverla a mano

Parti da `examples\specs\base.yaml`: ogni campo è spiegato nei commenti.

### Le parti della specifica

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
  - Ignora copertina, indici e legende.

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

Controlla sempre la specifica dopo ogni modifica:

```powershell
ragpdf spec check componenti
ragpdf spec check componenti --show-prompt lombardini --page 40
```

Il secondo comando mostra il prompt esatto che riceverà Gemini per la pagina 40: utile per
capire perché un risultato non è quello atteso.

---

## 4. Prova su poche pagine

Prima di lanciare tutto il catalogo, prova su 2-4 pagine rappresentative (ad esempio le due
pagine di un motore):

```powershell
ragpdf index lombardini --spec componenti --pages 40-43
```

Apri il CSV prodotto (`data\exports\componenti\catalogo-1-lombardini-rel-1501.csv`) e il file
`..._anomalie.csv` accanto. Se qualcosa non va:

1. correggi la specifica (istruzioni, descrizioni delle colonne) o il primer;
2. rilancia lo stesso comando. Il programma si accorge che le regole sono cambiate e chiede
   se rifare le pagine già elaborate: rispondi **sì**.

Ripeti finché il risultato sulle pagine di prova ti convince.

---

## 5. Indicizza tutto il documento

```powershell
ragpdf index lombardini --spec componenti
```

Il programma mostra quante pagine deve elaborare e una stima del tempo, poi chiede
conferma. Con 10 chiavi e 10 richieste al minuto per chiave, 100 pagine richiedono circa
1-2 minuti più i tempi di risposta di Gemini.

**Se si interrompe** (quota giornaliera finita, rete, Ctrl+C) non perdi nulla: rilancia lo
stesso comando più tardi e riparte dalle pagine mancanti. `ragpdf list` mostra
l'avanzamento.

Più documenti in una volta: `ragpdf index --all --spec componenti -y` (`-y` evita le
domande, utile per lasciarlo andare da solo).

---

## 6. Controlla e rigenera i CSV

Il report anomalie (`<documento>_anomalie.csv`) elenca:

| livello | significato |
|---|---|
| `errore` | pagina non elaborata, oppure campo obbligatorio vuoto |
| `avviso` | valore che non rispetta il formato, il tipo o i valori ammessi |
| `info` | note di Gemini su dubbi di lettura in quella pagina |

Le righe con anomalie restano comunque nel CSV: decidi tu cosa farne.

Se cambi solo la sezione `output` della specifica (separatore, ordinamento, nomi delle
colonne pagina/file) o i controlli (`required`, `pattern`), **non serve richiamare
Gemini**:

```powershell
ragpdf export --spec componenti --all
```

Con più documenti indicizzati produce anche `TUTTI.csv`, l'unione di tutti.

---

## Problemi frequenti

| Problema | Soluzione |
|---|---|
| `Nessuna chiave API Gemini` | Crea `.env` da `.env.example` e inserisci almeno una chiave |
| `quota giornaliera esaurita` | Aspetta il reset della quota (o aggiungi chiavi) e rilancia: riprende da dove era |
| Troppe pagine di fila fallite | Problema generale (nome del modello, chiave non valida): `ragpdf doctor --online` |
| Il CSV ha righe inventate o mancanti | Rendi più precise `row`, le `description` e le `instructions`; prova `--show-prompt` |
| Testo piccolo letto male | Aumenta `render: dpi:` in `configs/default.yaml` (es. 250) |
| Excel mostra tutto in una colonna | Usa `delimiter: ";"` nella specifica e `ragpdf export` |
| Vuoi provare senza consumare quota | `$env:RAGPDF_FAKE_GEMINI=1` (risposte simulate); per tornare normale chiudi il terminale |
| Errore imprevisto | Rilancia con `$env:RAGPDF_DEBUG=1` per vedere i dettagli tecnici |
