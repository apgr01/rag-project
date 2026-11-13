# Guida: indicizzare un catalogo PDF in CSV

Questa guida accompagna passo per passo dall'installazione al CSV finale. Gli esempi usano
un catalogo ricambi motore, ma il procedimento è lo stesso per qualunque PDF.

I comandi sono scritti per **Windows PowerShell**; su macOS/Linux cambia solo
l'attivazione dell'ambiente virtuale (`source .venv/bin/activate`).

**In breve:** il modo più comodo è l'interfaccia grafica nel browser.

```powershell
ragpdf ui
```

Si apre il browser su `http://127.0.0.1:8765`: carichi il PDF, lo configuri con Gemini
guardando le anteprime, avvii l'indicizzazione e scarichi il CSV. Tutto il resto della guida
spiega gli stessi passi, anche da riga di comando.

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
> **progetto** Google Cloud, non per singola chiave: più chiavi aumentano davvero la
> capacità solo se appartengono a progetti diversi. Il programma se ne accorge da solo:
> - **quota al minuto superata** → rallenta il ritmo complessivo fino a quello che il
>   progetto sopporta e aspetta il tempo indicato da Google (un messaggio
>   "rallento a circa N richieste/minuto"); poi risale piano piano;
> - **quota giornaliera esaurita** → la chiave viene esclusa; quando lo sono tutte il lavoro
>   si ferma in modo ordinato.
>
> In entrambi i casi le pagine **non** vengono segnate come fallite: rilanciando lo stesso
> comando si riparte da dove ci si era fermati. Se conosci il limite del tuo progetto puoi
> indicarlo in `configs\default.yaml` (`gemini.max_requests_per_minute_total`) per evitare
> del tutto gli errori di quota.

---

## 1. Interfaccia grafica: `ragpdf ui` (consigliata)

```powershell
ragpdf ui
```

Il comando avvia un piccolo server **solo su questo computer** e apre il browser. Lascia
aperta la finestra del terminale finché usi l'interfaccia; per chiuderla premi Ctrl+C.

### Biblioteca: studiare i cataloghi prima di indicizzarli

La scheda **Biblioteca** (in alto) raccoglie tutti i PDF, organizzati in **cartelle**:

1. **Carica** i PDF nella cartella aperta: trascinali, oppure *Scegli PDF* / *Scegli una
   cartella*. Trascinando una cartella intera le sottocartelle vengono ricreate. Un PDF già
   presente (stesso contenuto, anche con un altro nome) non viene duplicato.
2. **Organizza**: crea cartelle (anche su più livelli, `Motori/Lombardini`), rinominale o
   spostale; seleziona più cataloghi per spostarli, dare o togliere **tag**, eliminarli.
3. **Cerca e filtra**: la casella di ricerca guarda nome, file, descrizione e tag; un clic su
   un tag mostra i cataloghi con quel tag da tutte le cartelle (più tag: devono averli tutti).
4. **Studia un catalogo** (clic sul nome):
   - **Scheda**: titolo, cartella, tag e una descrizione libera di cosa contiene e com'è fatto.
   - **Dati presenti**: l'inventario dei dati che si potrebbero estrarre dal catalogo (motore,
     versione, codice, alesaggio...), in qualsiasi ordine, con una descrizione facoltativa
     (es. `alesaggio` → "diametro del cilindro in mm"). Non decidono le colonne del CSV, che
     sceglie il progetto: servono a capire il PDF e a trovare i cataloghi da unire.
   - **Pagine e collezioni**: sfoglia le miniature (frecce ← → per cambiare pagina; mentre giochi
     a Snake le frecce sono del gioco); *Apri il PDF ↗* apre il PDF originale in una nuova
     scheda alla pagina che stai guardando; clic per vedere una pagina, **Maiusc+clic** per selezionare un intervallo, poi scegli il tag e
     aggiungi la collezione (es. 1-2 *Copertina*, 3-4 *Indice*, 9-250 *Dati* con il commento
     "tabelle codici per motore", 12-13 *Pagina d'esempio*). Le collezioni descrivono il
     contenuto; quelle *Dati* indicano le pagine con dati indicizzabili. Una pagina può stare in più collezioni.
5. **Fatti aiutare da Gemini** (pannello *✨ Analisi con Gemini* nella scheda del catalogo):
   - scegli le **pagine da inviare** sulle miniature, come nella configurazione guidata: clic
     per selezionare, Maiusc+clic per un intervallo, doppio clic per ingrandire; oppure
     scrivile (`1-6, 20, 45-46`). *Selezione rapida* mette le prime 6 pagine più 8
     distribuite; in alternativa spunta *Invia tutto il PDF*. Di solito bastano copertina,
     indice, legenda e qualche pagina tipo: dall'indice Gemini deduce anche le parti che non
     vede;
   - le **indicazioni** dicono a Gemini *cosa* cercare e sono modificabili: *Salva come mio
     predefinito* le ricorda per le prossime analisi, *Ripristina quello di fabbrica* torna
     all'originale. Il **formato** della risposta invece è fisso: lo aggiunge il programma dopo
     le tue indicazioni, con la regola che prevale su qualsiasi richiesta diversa (*Vedi il
     prompt* mostra il testo esatto). Ogni risposta viene controllata: se manca un campo, ce n'è
     uno in più, un tipo è sbagliato o un tag non esiste, la risposta è **non valida**, non
     tocca la scheda e resta in archivio (*vedi la risposta*) per capire cosa è successo;
   - con *Tieni conto della scheda attuale* Gemini completa e corregge quello che hai già
     scritto;
   - la risposta diventa una **proposta da rivedere**: scegli la descrizione (non usare,
     sostituisci, aggiungi in fondo), i tag, i dati presenti e le collezioni da applicare. Le
     collezioni marcate *dedotta* sono state ricavate dall'indice senza vedere le pagine:
     controllale. Nella scheda restano segnate *Gemini* o *dedotta*;
   - ogni analisi resta in archivio (*Analisi archiviate*) e si può rivedere e applicare anche
     dopo; nella biblioteca i cataloghi con una proposta in sospeso hanno il badge
     *✨ proposta da rivedere*.

   Se Gemini rifiuta la richiesta perché troppo pesante (troppe pagine o file troppo
   grande), il messaggio lo dice: scegli meno pagine. Prima dell'invio il programma blocca i
   PDF oltre 1000 pagine o circa 50 MB e avvisa per gli allegati pesanti.
6. **Crea un progetto guidato** (pannello *✨ Crea progetto guidato* nella scheda del catalogo
   da cui parti):
   - descrivi **l'indice che vuoi**, in uno o più modi: *cosa rappresenta una riga*, le
     **colonne** (anche con un clic sui dati presenti del catalogo), un **CSV d'esempio** (ne
     legge colonne e prime righe; *Usa le sue colonne* le copia; diventa anche il riferimento
     del progetto per la configurazione) oppure **a parole**;
   - **dove cercare**: tutta la biblioteca o una cartella. Gemini confronta solo i cataloghi
     **elaborati** (con descrizione o dati presenti): il riquadro dice quanti sono e quanti
     restano esclusi;
   - le **indicazioni** sono modificabili come nell'analisi; il formato della risposta è fisso;
   - parte **una sola richiesta**, solo testo (al massimo 300 cataloghi). Gemini usa codici
     per cataloghi (`C1`) e sezioni (`C1/2`): se cita codici inesistenti, mette il catalogo di
     partenza tra i candidati o sbaglia il formato, la risposta è *non valida* e non si usa;
   - il **report** mostra la **tabella** proposta (nome del progetto, descrizione, riga,
     colonne: tutto modificabile), quali colonne si ricavano dal catalogo di partenza e i
     **candidati** *compatibili* (già spuntati) o *parziali*, con motivo e colonne coperte;
     accanto a ogni colonna vedi in quanti PDF scelti si ricava. Per ogni PDF scegli le
     sezioni da indicizzare (proposte da Gemini, altrimenti quelle *Dati*);
   - **Crea progetto**: la tabella diventa l'impostazione globale del progetto (vincolante per
     la configurazione), le sezioni scelte le pagine da indicizzare. Le proposte restano nel
     pannello della scheda (*Proposte precedenti*), con il link al progetto creato.

   Lo stesso catalogo può avviare più progetti guidati, per tabelle diverse (es. "una riga per
   motore" e "una riga per componente").
7. **Oppure crea il progetto a mano**: seleziona i cataloghi simili e premi *Crea progetto…*. Le
   pagine *dati* diventano le pagine del progetto, le *pagine d'esempio* sono già
   scelte nella configurazione guidata e dati presenti e descrizioni compongono l'obiettivo proposto
   (modificabile).

Dalla scheda *Documenti* di un progetto puoi anche aggiungere PDF con **Scegli dalla
biblioteca…**. Togliere un PDF da un progetto o eliminare un progetto **non** cancella il PDF:
resta nella biblioteca con i suoi risultati. Si elimina dalla biblioteca, e solo se nessun
progetto lo usa.

### Progetti: più PDF, un CSV

Tutto è organizzato per **progetto**: uno o più PDF **con la stessa struttura** (per esempio
i cataloghi dello stesso editore) che condividono primer e specifica e producono **un CSV
unico** da usare in una pagina di ricerca. Un singolo PDF è semplicemente un progetto con un
solo file; i cataloghi già configurati prima dei progetti sono stati convertiti
automaticamente.

**Progetti (home).** Dai un nome, trascina uno o più PDF nel riquadro e premi *Crea progetto*.

**Documenti.** Aggiungi altri PDF in qualsiasi momento (anche dopo l'indicizzazione: verranno
elaborate solo le loro pagine). Il nome di ogni PDF porta alla sua **scheda della biblioteca**;
sotto vedi la sua descrizione e i dati presenti. Per ogni PDF puoi impostare, facoltativamente:
- **note**: istruzioni in più valide solo per quel PDF, per le differenze o le particolarità
  da segnalare (es. "in questo catalogo la versione è scritta sotto il nome del motore");
- **pagine da indicizzare**: si scelgono **spuntando le collezioni** della sua scheda (di
  default quelle *Dati*, già spuntate quando il PDF arriva dalla biblioteca), oppure si
  scrivono a mano (es. `5-` se ha 4 pagine introduttive). Il badge 📚 indica le pagine prese
  dalla biblioteca.

Le pagine sono una **copia**: se poi modifichi le collezioni nella biblioteca, il progetto non
cambia da solo (le pagine fanno parte delle regole), ma in cima ai *Documenti* compare l'avviso
**La biblioteca è cambiata** con le differenze; *Aggiorna dalla biblioteca* ricopia le pagine
e dice quali PDF già indicizzati andranno rifatti. Descrizioni e dati presenti invece si
leggono sempre in diretta.

Le **colonne** restano le stesse per tutti: è ciò che permette il CSV unico. Cambiando le note
di un PDF andrà reindicizzato solo quel PDF.

**Impostazioni.** La tabella che il progetto deve produrre: **descrizione**, **cosa rappresenta
una riga**, **colonne** nell'ordine del CSV (nome e descrizione) e **altre indicazioni**.
Accanto a ogni colonna vedi in quanti PDF del progetto compare tra i *dati presenti* della
biblioteca (es. `1/3`: negli altri resterà vuota o va spiegata nelle note); a destra trovi
tutti i dati presenti dei PDF, e un clic ne aggiunge uno come colonna. Sono facoltative: senza
colonne la configurazione guidata le propone dall'obiettivo scritto a parole. Se le compili,
sono **vincolanti**: Gemini deve usarle esattamente, e il programma lo controlla (vedi sotto).

**1 · Configurazione.** Gemini riceve anche le **schede della biblioteca** dei PDF (descrizione,
dati presenti, mappa delle pagine con i commenti): le usa per il primer e per le note dei
singoli PDF. Se le impostazioni hanno le colonne, la casella dell'obiettivo diventa *altre
indicazioni*, e ogni versione proposta viene confrontata con le colonne del progetto: se non
coincidono compare un avviso con le differenze e il pulsante *Chiedi a Gemini di correggerle*;
salvare comunque richiede una conferma, e l'avviso resta nelle schede Impostazioni e
Indicizzazione finché non si rifà la configurazione.
1. Scegli le pagine da mostrare a Gemini. La proposta iniziale viene dalla mappa della
   biblioteca: le collezioni *Indice*, *Legenda* e *Pagina d'esempio* di ogni PDF (altrimenti le
   prime pagine più alcune distribuite). Con più PDF c'è una scheda per ciascuno: puoi
   prendere, per esempio, indice e legenda da un catalogo e due schede tipo da un altro.
   Clic sulle miniature (Maiusc+clic per un intervallo, doppio clic per ingrandire), oppure
   scrivi le pagine nel campo di testo. Il riepilogo sotto mostra tutto ciò che hai scelto.
2. Scrivi cosa vuoi ottenere (o lascia vuoto) e, se vuoi, carica un CSV di riferimento.
3. *Avanti* mostra un riepilogo: puoi tornare indietro, vedere **il prompt esatto** che verrà
   inviato e solo allora premere *Invia a Gemini*.
4. Arrivano la proposta di Gemini (riepilogo, domande, colonne, eventuali **note per singoli
   PDF** che si discostano dagli altri) e la **prova di estrazione**, che include sempre
   **almeno una pagina per ogni PDF**: è lì che si vede se hanno davvero la stessa struttura.
   Clic su una riga per vedere la sua pagina.
5. Scrivi cosa correggere nel riquadro *Correzione* (anche riferito a un solo PDF: "nel Kubota
   la versione è sotto il nome") e premi *Invia correzione*. Gemini decide se è una regola
   comune o una nota per quel PDF. Le versioni restano nell'elenco: un clic per tornare a una
   precedente. Primer, specifica e note si possono anche modificare a mano nei riquadri in
   fondo.
6. *Salva configurazione* quando il risultato ti convince.

**2 · Indicizzazione.** Una tabella per PDF con pagine fatte e da fare e la stima dei tempi;
*Avvia* elabora tutti i PDF dopo una conferma. La barra di avanzamento si aggiorna da sola e
*Ferma* interrompe in modo ordinato. Puoi chiudere la pagina: il lavoro continua finché il
terminale con `ragpdf ui` è aperto, e riaprendo la pagina lo ritrovi. Se hai cambiato primer,
specifica o note, vengono rifatti **solo** i PDF interessati.

Per ingannare l'attesa, sotto la barra c'è **Gemini Snake**: il serpente è Gemini che
"ingerisce" le pagine PDF (frecce o WASD, spazio per la pausa, sul telefono si scorre il dito).
Se perdi mentre Gemini lavora si ricomincia da soli; a fine indicizzazione il serpente produce
gli `index.csv`, esplode e solo dopo si passa ai risultati. Lo stesso succede a fine
configurazione se stai giocando (primer, specifica e anteprima). *Nascondi il gioco* lo toglie e la scelta
viene ricordata; il gioco non influisce in alcun modo sul lavoro.

**3 · Risultati.**
- **CSV unico** del progetto (colonna del file d'origine per sapere da quale PDF viene ogni
  riga) e un CSV per ciascun PDF;
- anomalie di tutti i PDF (prima errori, poi avvisi, poi note di Gemini; clic per vedere la
  pagina);
- **pagina di ricerca**: si apre con il pulsante, ed è anche salvata come file autonomo in
  `data\exports\<progetto>\ricerca.html`, che si apre con un doppio clic senza il server
  (vedi più sotto).

Lo stesso codice presente in più cataloghi compare **una volta per ogni catalogo**, con il suo
file e la sua pagina: in una ricerca è utile sapere dove si trova.

### La pagina di ricerca

Una pagina HTML con tutti i dati del CSV unico:
- **ricerca libera** su tutte le colonne, tollerante a spazi, punti e trattini: `fin 102 af`
  trova `FIN102AF`, `6501044` trova `6501.044`; con più parole devono esserci tutte, e in
  cima compaiono le righe che contengono la ricerca intera (il codice esatto);
- **filtri** automatici sulle colonne con pochi valori (costruttore, file, cilindri, …);
- clic sulle intestazioni per **ordinare**; risultati a pagine da 100;
- link **"apri p.N"** per aprire il PDF alla pagina da cui viene la riga;
- **Scarica risultati (CSV)**: solo le righe trovate.

Il file si rigenera a ogni indicizzazione o export. Provata con un indice reale di circa
30.000 righe: si apre in meno di mezzo secondo e cerca in pochi millesimi di secondo. Per
condividerla con altri basta il file `ricerca.html` (i link ai PDF funzionano se si copia
l'intera cartella `data`).

**Protezioni:**
- i pulsanti con la stellina ✨ usano Gemini e chiedono sempre conferma, dicendo quante
  richieste partiranno; il tasto Invio non invia mai nulla;
- obiettivo e correzioni restano bozze, salvate nel browser, finché non premi il pulsante;
- se hai modificato primer o specifica senza salvare, cambiando pagina viene chiesto se
  perdere le modifiche;
- su ogni progetto può girare una sola operazione alla volta;
- durante un'operazione i messaggi di Gemini (sovraccarico, quote, rallentamenti) compaiono sotto
  la barra di avanzamento; *Ferma* interrompe subito anche le attese in corso.

Il resto della guida descrive gli stessi passi da riga di comando, utili per automatizzare o
se preferisci il terminale.

---

## 1b. Da riga di comando: progetti e configurazione guidata

```powershell
ragpdf project new "Cataloghi motori" "C:\cataloghi\Lombardini.pdf" "C:\cataloghi\Kubota.pdf"
ragpdf setup cataloghi-motori
ragpdf project index cataloghi-motori
ragpdf project search cataloghi-motori
```

Con più PDF le pagine si indicano così: `lombardini: 1-4, 38-41; kubota: 12` (basta una parte
del nome). Altri comandi: `ragpdf project add <progetto> file.pdf`,
`ragpdf project note <progetto> <pdf> --notes "..." --pages "5-"`, `ragpdf project export`,
`ragpdf project remove-doc`, `ragpdf project list`.

Il resto di questa sezione descrive la sessione `ragpdf setup` per un singolo PDF; con un
progetto funziona allo stesso modo.

Non serve leggere tutto il catalogo né scrivere a mano cosa indicizzare: lo propone Gemini, tu
correggi a parole guardando un'anteprima del risultato.

```powershell
ragpdf setup "C:\cataloghi\Catalogo 1 LOMBARDINI rel 1501.pdf"
```

Il PDF viene aggiunto al workspace, in un progetto con il suo stesso nome, (`data\documents\catalogo-1-lombardini-rel-1501\`) con un
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
sul nuovo catalogo e si corregge solo se serve. Al salvataggio si aggiornano gli **stessi**
file (un solo primer per tipo di catalogo): se li hai modificati ti viene chiesto se
sovrascriverli o salvarli con un nome nuovo.

```powershell
ragpdf setup "C:\cataloghi\Catalogo 2 LOMBARDINI.pdf" --primer catalogo-1-lombardini-rel-1501 --spec catalogo-1-lombardini-rel-1501
```

---

## 2. Indicizza tutto il documento (riga di comando)

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
| `gemini.retry_delay_seconds` | 5 | attesa fissa prima di riprovare dopo un sovraccarico (503) o un errore di rete |
| `gemini.max_wait_minutes` | 10 | oltre questa attesa continua (sovraccarico, quote) il lavoro si ferma |
| `gemini.max_requests_per_minute_total` | (vuoto: adattivo) | il limite al minuto del tuo progetto, se lo conosci |
| `gemini.media_resolution` | (vuoto) | `high` se Gemini legge male testi minuscoli |
| `gemini.thinking_level` | (vuoto) | `low` per estrazioni più veloci ed economiche; verifica con `compare` che la qualità regga |
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
| `Gemini sovraccarico (503, alta richiesta)` | Momentaneo e lato Google, uguale per tutte le chiavi: il programma riprova ogni 5 secondi (`gemini.retry_delay_seconds`) senza far fallire le pagine; se dura più di 10 minuti (`gemini.max_wait_minutes`) si ferma e si riprende rilanciando. Nell'interfaccia il messaggio compare sotto la barra; *Ferma* interrompe subito |
| `quota giornaliera esaurita` | Aspetta il reset della quota (o usa chiavi di altri progetti) e rilancia: riprende da dove era |
| Molti messaggi "rallento a circa N richieste/minuto" | Normale con chiavi dello stesso progetto; per evitarli imposta `gemini.max_requests_per_minute_total` al limite del progetto |
| Troppe pagine di fila fallite | Problema generale (nome del modello, chiave non valida): `ragpdf doctor --online` |
| `la risposta ha superato la lunghezza massima` | Pagina con moltissime righe: riduci le colonne o rendi le istruzioni più selettive |
| Il CSV ha righe inventate o mancanti | Riprendi `ragpdf setup` e descrivi l'errore; oppure rendi più precise `row`, `description`, `instructions` |
| Testo piccolo letto male | Aumenta `render.dpi` o imposta `gemini.media_resolution: high` |
| Excel mostra tutto in una colonna | Nella specifica `delimiter: ";"`, poi `ragpdf export` |
| Vuoi provare senza consumare quota | `$env:RAGPDF_FAKE_GEMINI=1` (risposte simulate); per tornare normale chiudi il terminale |
| Errore imprevisto | Rilancia con `$env:RAGPDF_DEBUG=1` per vedere i dettagli tecnici |
