# Glossario di ragpdf

Le parole usate nel programma, nell'interfaccia e nella documentazione, con il significato
preciso di ciascuna. Serve a non confondere cose che si somigliano (tag, note, pagine...).

**Legenda dello stato**

- ✅ **esiste oggi** e funziona così;
- 🔜 **deciso**, in lavorazione (vedi *Fase 1* nella [roadmap](ROADMAP.md)).

---

## 1. Il quadro in una figura

```
                 BIBLIOTECA                                   PROGETTO (= una tabella)
   ┌──────────────────────────────────────┐        ┌──────────────────────────────────────────┐
   │ catalogo (PDF) ── scheda              │        │ impostazioni globali                     │
   │   descrizione, tag, dati presenti,    │        │   descrizione, riga, colonne             │
   │   collezioni di pagine                │        │ PDF del progetto (rimandano alla scheda) │
   │                                       │        │   + pagine e note proprie del progetto   │
   │ ✨ analisi → proposta per la scheda   │        │ configurazione guidata ✨ → primer +     │
   │ ✨ progetto guidato ──────────────────┼──────► │   specifica                              │
   │   (dalla scheda di un PDF)            │ crea   │ indicizzazione ✨ → risultati (CSV)      │
   └──────────────────────────────────────┘        └──────────────────────────────────────────┘
   ✨ = passaggio che usa Gemini (sempre con conferma, il prompt è visibile prima dell'invio)
```

In breve: **la biblioteca serve a capire i PDF** (a te e al programma); **il progetto decide la
struttura del CSV** ed è l'unico che indicizza.

---

## 2. Termini generali

**PDF / catalogo / documento** ✅
La stessa cosa vista da punti diversi: un file PDF caricato nel programma. Nell'interfaccia si
dice *catalogo* (perché i casi d'uso sono cataloghi tecnici), nel codice *documento*. Ha un
**id** leggibile ricavato dal nome del file (`catalogo-lombardini`) e un'**impronta del
contenuto** (SHA-256) che permette di riconoscere lo stesso PDF anche con un altro nome: un PDF
non viene mai caricato due volte.

**Workspace** ✅
La cartella `data/` con tutti i dati dell'utente (PDF, schede, progetti, risultati). Per un
backup basta copiarla.

**Gemini** ✅
Il modello di Google che legge le pagine. Ogni chiamata è una **richiesta**; si paga in
**token** e ha dei limiti (**quote**) per minuto e per giorno. In **modalità simulata** il
programma finge le risposte, per provare senza chiavi e senza costi.

**Lavoro** ✅
Un'operazione lunga che chiama Gemini (analisi, configurazione, indicizzazione...). Mostra
l'avanzamento e i **messaggi** (sovraccarico, quote), si può **fermare**, e intanto si può
giocare a **Gemini Snake**.

---

## 3. La biblioteca: capire i PDF

**Biblioteca** ✅
L'elenco di tutti i PDF, indipendente dai progetti. I PDF *vivono* qui: i progetti li usano
ma non li possiedono. Togliere un PDF da un progetto o eliminare un progetto non cancella il
PDF; un PDF si elimina dalla biblioteca solo se nessun progetto lo usa. Quello che si scrive
nella biblioteca serve sia all'utente (capire i cataloghi) sia al programma (proporre progetti,
dare contesto a Gemini).

**Cartella** ✅
Dove sta un catalogo nella biblioteca (es. `Motori/Lombardini`). Ogni catalogo sta in **una
sola** cartella. Serve solo a tenere ordine.

**Tag del catalogo** ✅
Etichette **libere** e **multiple** (es. `motori`, `lombardini`, `da studiare`). Servono a
filtrare e ritrovare i cataloghi in tutte le cartelle. *Non confonderli con i tag di pagina.*

**Scheda (del catalogo)** ✅
La pagina di un catalogo con tutto ciò che si sa di lui: titolo, cartella, tag, descrizione,
dati presenti, collezioni di pagine, analisi archiviate. È **descrittiva**: dice com'è fatto il
PDF, non dà ordini all'estrazione.

**Descrizione (del catalogo)** ✅
Testo libero su cosa contiene il PDF e com'è organizzato (marca, prodotti, sezioni,
particolarità). Scritta a mano o proposta dall'analisi. *Non è una nota per l'estrazione*
(vedi *note per il PDF*).

**Dati presenti** ✅
L'**inventario** dei dati che si potrebbero estrarre in una tabella da quel catalogo (motore,
versione, componente, pistone...), **senza ordine**, ciascuno con una descrizione facoltativa
(unità, formato, dove si trova). Non sono una decisione di indicizzazione: le **colonne** le
sceglie il progetto. Servono a capire il PDF e a trovare i cataloghi da unire.

**Catalogo elaborato** ✅
Un catalogo con almeno una descrizione o dei dati presenti. È tra questi che il progetto
guidato cerca i cataloghi da unire.

**Collezione di pagine** ✅
Un **intervallo continuo** di pagine con **un tag di pagina** e un **commento** facoltativo
(es. *pagine 9-250, Dati, "tabelle codici per motore"*). Una pagina può stare in più collezioni.
Può essere scritta a mano, proposta da Gemini o **dedotta** da Gemini dall'indice senza vedere
quelle pagine. Le collezioni descrivono il **contenuto**: insieme sono la **mappa** del
catalogo.

**Tag di pagina** ✅
L'elenco **fisso** di etichette per le collezioni (fisso perché il programma le usa): *dati,
pagina d'esempio, copertina, indice, introduzione, legenda/simboli, tabelle tecniche,
disegni/immagini, avvertenze/note, pubblicità/vuota, altro*. Le pagine **dati** sono quelle con
dati indicizzabili (prima si chiamava *da indicizzare*).

**Visualizzatore** ✅
Nella scheda: miniature a sinistra, pagina grande a destra, frecce per sfogliare,
*Apri il PDF ↗* per aprire l'originale nel browser alla pagina mostrata. Serve a creare le
collezioni.

**Analisi (con Gemini)** ✅
Gemini guarda alcune pagine scelte (o il PDF intero) e propone **una scheda**: descrizione,
tag, dati presenti, collezioni, osservazioni. Il risultato è una **proposta**.

**Proposta** ✅
Il risultato di un'analisi: **non viene applicato da solo**. Si sceglie voce per voce cosa
copiare nella scheda. Resta nell'**archivio delle analisi** con uno stato: *da rivedere*,
*applicata*, *scartata* o *non valida*.

**Progetto guidato** ✅
Dalla scheda di un PDF: descrivi l'**indice che vuoi** (con una tabella di colonne, un CSV
d'esempio o a parole) e Gemini cerca **tra i cataloghi elaborati** quali possono produrre
righe di quella tabella. Una richiesta, poi un **report**: la tabella ripulita (modificabile),
quali colonne copre il PDF di partenza, i **cataloghi candidati** (*compatibile* o *parziale*,
con motivo, colonne coperte e collezioni con i dati). Scegli i candidati e **crei il
progetto**, con la tabella come impostazioni globali. Lo stesso PDF può avviare più progetti
guidati (es. "per motore" e "per componente").

**Raggruppamento** (tolto)
La proposta di gruppi a partire da più cataloghi selezionati nella biblioteca. È stata
sostituita dal progetto guidato, che parte da un bisogno concreto (una tabella) invece di
dividere i cataloghi in gruppi.

---

## 4. Il progetto: decidere ed estrarre

**Progetto** ✅
**Una tabella**: uno o più PDF con struttura compatibile, estratti con le **stesse regole**,
che producono **un CSV unico**. È il coordinatore finale: solo lui ha la struttura desiderata
del CSV. Lo stesso PDF può stare in più progetti (per esempio uno "per motore" e uno "per
componente"): i risultati restano separati.

**Impostazioni globali del progetto** ✅
Cosa deve uscire dal progetto: **descrizione**, **cosa è una riga**, **colonne** (nome +
descrizione), **altre indicazioni**. Le scrivi tu (progetto a mano) o arrivano dal progetto
guidato. Sono la fonte autorevole: la specifica deve usare **esattamente** queste colonne, e il
programma lo controlla. Nella scelta delle colonne vedi la **copertura** nei PDF del progetto
(es. *pistone: 1 PDF su 3*), ricavata dai loro dati presenti.

**Riga ("cosa rappresenta una riga")** ✅
La scelta più importante: la **granularità** della tabella (una riga per motore, per codice,
per coppia FIN/OEM...). Due tabelle con le stesse colonne ma righe diverse non si accorpano.

**Colonna** ✅
Nelle impostazioni globali: nome + descrizione. Nella **specifica** ha anche i dettagli
tecnici scritti da Gemini: tipo (testo/numero), obbligatoria, formato atteso, valori ammessi
e se è una **colonna di contesto**.

**Colonna di contesto** ✅
Un dato valido per tutta la pagina (es. il nome del motore nell'intestazione) che vale per
ogni riga anche se non è ripetuto. Con il **contesto ereditato** passa anche alle pagine
successive dello stesso PDF che non lo riportano.

**Obiettivo** ✅
La casella "Cosa vuoi ottenere" della configurazione guidata, a parole. Se le impostazioni
globali hanno la tabella, diventa *altre indicazioni per questa configurazione*.

**PDF del progetto** ✅
I PDF che il progetto usa, ciascuno con le sue **impostazioni per PDF**. Il nome porta alla sua
**scheda della biblioteca**, e nel progetto se ne vedono descrizione e dati presenti.

**Impostazioni per PDF (nel progetto)** ✅
Valgono solo dentro quel progetto:
- **pagine da indicizzare** di quel PDF: si scelgono **spuntando le sue collezioni** (di
  default quelle *dati*) e vengono **copiate**; oppure si scrivono a mano;
- **note per il PDF**: istruzioni di estrazione in più, solo per lui (es. "in questo catalogo
  la versione è sotto il nome del motore"). Possono essere proposte da Gemini in
  configurazione.

**Note per il PDF** vs **descrizione** ✅
La *descrizione* (biblioteca) dice **com'è** il PDF; le *note* (progetto) dicono **come
estrarre** da lui in quel progetto. Le note entrano nelle regole, la descrizione no.

**Primer** ✅
Testo che spiega a Gemini **com'è fatto il documento** (struttura, convenzioni, sigle, cosa
non si capisce da una pagina isolata). Viene dato insieme a **ogni** pagina durante
l'indicizzazione. Uno per progetto.

**Specifica (di indicizzazione)** ✅
Il file YAML con le **regole operative** dell'estrazione: cosa è una riga, colonne con i
dettagli tecnici, istruzioni per i casi particolari, tabelle di conversione, CSV d'esempio,
pagine. Una per progetto. *Primer = com'è il documento; specifica = cosa estrarre.*

**Regole** ✅
Tutto ciò che determina il risultato dell'estrazione di un PDF: primer, specifica, note e
pagine di quel PDF, modello di Gemini, modo di input.

**Impronta (delle regole)** ✅
Un riassunto delle regole salvato insieme ai risultati. Se le regole cambiano, i risultati di
quel PDF sono **vecchi** e vanno rifatti: il programma rifà **solo** i PDF interessati.

**Configurazione guidata** ✅
Il processo, a **versioni** successive, in cui Gemini scrive primer e specifica:
1. scegli le *pagine mostrate a Gemini* e l'obiettivo (o le altre indicazioni, se la tabella è
   già nelle impostazioni globali);
2. Gemini propone primer, specifica, note per i PDF e fa una *prova*;
3. tu correggi **a parole**, e Gemini produce una nuova versione;
4. *Salva configurazione* quando il risultato ti convince.

Gemini riceve anche le **schede** dei PDF (descrizione, mappa delle pagine, dati presenti) e
la tabella delle impostazioni globali come vincolo; ogni versione della specifica viene
confrontata con le colonne del progetto. Le pagine mostrate sono proposte dalla mappa
(indice, legenda, esempi).

**Estratto PDF** ✅
Un PDF temporaneo con le sole pagine scelte, mandato a Gemini al posto dell'originale. Nel
prompt c'è la **corrispondenza delle pagine** (pagina 3 dell'estratto = pagina 45
dell'originale); sulle pagine non viene stampato nulla.

**Prova (anteprima)** ✅
Durante la configurazione, l'estrazione vera su poche pagine (al massimo 6, almeno una per
PDF), per vedere le righe che uscirebbero prima di indicizzare tutto.

**Indicizzazione** ✅
L'estrazione di tutte le pagine da indicizzare di tutti i PDF del progetto, **una pagina per
richiesta**. Gemini riceve primer, specifica (con le note del PDF) e la pagina come
**immagine + testo nativo** (modo di input predefinito). Il lavoro è **riprendibile**: le
pagine fatte non si rifanno.

**Risultati** ✅
- **CSV unico** del progetto, con la colonna del file d'origine e della pagina;
- **CSV per PDF**;
- **anomalie**: righe con problemi (colonne obbligatorie vuote, formati non rispettati...);
- **pagina di ricerca**: un file HTML autonomo per cercare nel CSV unico.

🔜 Nella scheda di ogni PDF si vedrà in quali progetti è usato e come procede la sua
indicizzazione.

---

## 5. Dalla biblioteca al progetto ✅

Regola: **ciò che descrive si legge in diretta, ciò che comanda si copia.**

| Informazione della biblioteca | Come passa al progetto | Dove serve |
|---|---|---|
| descrizione del PDF | in diretta | progetto guidato; prompt della configurazione; vista del progetto |
| dati presenti | in diretta | progetto guidato; copertura delle colonne |
| mappa delle pagine (collezioni e commenti) | in diretta | pagine proposte in configurazione; contesto per il primer |
| pagine da indicizzare (collezioni scelte) | **copia**, cambia solo con *Aggiorna dalla biblioteca* | regole di estrazione di quel PDF |
| tag e cartella | in diretta | interfaccia |

Le informazioni descrittive non entrano nelle regole, quindi correggerle non rende vecchi i
risultati. Le pagine invece sì: *Aggiorna dalla biblioteca* mostra le differenze e dice quali
PDF andrebbero rifatti. Il progetto **non scrive** nella biblioteca.

---

## 6. Parole che si somigliano

**I "tag"** — tre cose diverse:

| Termine | Dove | Liberi? | A cosa servono |
|---|---|---|---|
| tag del catalogo | scheda | sì | filtrare e ritrovare i cataloghi |
| tag di pagina | collezioni | no, elenco fisso | dire cosa c'è in un intervallo di pagine |

**I testi liberi** — chi li scrive e a cosa servono:

| Termine | Dove | Descrive o comanda? | Entra nelle regole? |
|---|---|---|---|
| descrizione | scheda del catalogo | descrive il PDF | no |
| commento | collezione di pagine | descrive quelle pagine | no |
| osservazioni | analisi (Gemini) | dubbi e note di Gemini | no |
| descrizione del progetto | impostazioni globali | descrive la tabella | no (contesto) |
| note per il PDF | progetto | comanda l'estrazione di quel PDF | **sì** |
| istruzioni | specifica | comandano l'estrazione di tutti i PDF | **sì** |
| indicazioni | prompt di analisi / progetto guidato | dicono a Gemini cosa cercare | no |
| obiettivo / altre indicazioni | configurazione guidata | cosa vuoi ottenere, a parole | indirettamente |

**Le "pagine"** — cinque selezioni diverse:

| Selezione | Dove si sceglie | A cosa serve |
|---|---|---|
| pagine da inviare | analisi (scheda) | cosa guarda Gemini per proporre la scheda |
| pagine d'esempio | collezione *pagina d'esempio* | pagine rappresentative, proposte in configurazione |
| pagine mostrate a Gemini | configurazione guidata (max 40) | cosa guarda Gemini per scrivere primer e specifica |
| pagine della prova | configurazione guidata (max 6) | dove si prova l'estrazione |
| pagine da indicizzare | progetto, per PDF (dalle collezioni *dati*) | cosa viene estratto davvero |

**I dati e le colonne**:

| Termine | Dove | Conta nell'indicizzazione? |
|---|---|---|
| dati presenti | scheda del catalogo | no: inventario, serve a capire e a proporre progetti |
| colonne della tabella richiesta | progetto guidato | no: diventano le colonne del progetto |
| colonne del progetto | impostazioni globali | sì, tramite la specifica che deve rispettarle |
| colonne della specifica | specifica | **sì**, sono quelle usate davvero |

**Le "proposte" di Gemini** — niente viene applicato da solo:

| Passaggio | Cosa propone | Dove si decide |
|---|---|---|
| analisi | una scheda per un catalogo | proposta nella scheda, voce per voce |
| progetto guidato | la tabella e i cataloghi da unire | report, candidato per candidato |
| configurazione guidata | primer, specifica, note, pagine della prova | versioni, correzioni a parole, salvataggio |

---

## 7. Regole di sicurezza dei prompt ✅

- **Indicazioni** (modificabili dall'utente) e **cornice** (fissa, scritta dal programma) sono
  separate: la cornice contiene il **formato della risposta** e prevale sulle indicazioni.
- Ogni risposta strutturata viene **convalidata in modo rigoroso**: se la struttura non torna
  (campi mancanti o in più, tipi sbagliati, valori inesistenti) è **non valida**, non viene
  usata e resta in archivio con la risposta originale.
- Dentro una risposta valida, le singole voci inutilizzabili (es. pagine fuori dal documento)
  vengono scartate e **segnalate**.
