# Glossario di ragpdf

Le parole usate nel programma, nell'interfaccia e nella documentazione, con il significato
preciso di ciascuna. Serve a non confondere cose che si somigliano (tag, note, pagine...).

**Legenda dello stato**

- ✅ **esiste oggi** e funziona così;
- ⚠️ **esiste oggi ma cambierebbe** con la proposta in discussione;
- 🆕 **proposto**, non ancora realizzato.

---

## 1. Il quadro in una figura

```
                 BIBLIOTECA                                   PROGETTO (= una tabella)
   ┌──────────────────────────────────────┐        ┌──────────────────────────────────────────┐
   │ catalogo (PDF) ── scheda              │        │ impostazioni globali 🆕                  │
   │   descrizione, tag, collezioni        │        │   descrizione, riga, colonne             │
   │   modalità di indicizzazione 🆕       │ crea   │ PDF del progetto (rimandano alla scheda) │
   │                                       │ ─────► │   + pagine e note proprie del progetto   │
   │ ✨ analisi  → proposta per la scheda  │        │ configurazione guidata ✨ → primer +     │
   │ ✨ raggruppamento → report di gruppi  │        │   specifica                              │
   └──────────────────────────────────────┘        │ indicizzazione ✨ → risultati (CSV)      │
                                                    └──────────────────────────────────────────┘
   ✨ = passaggio che usa Gemini (sempre con conferma, il prompt è visibile prima dell'invio)
```

In breve: **la biblioteca descrive i PDF**, **il progetto decide cosa estrarre** e lo estrae.

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
Un'operazione lunga che chiama Gemini (analisi, raggruppamento, configurazione,
indicizzazione). Mostra l'avanzamento e i **messaggi** (sovraccarico, quote), si può
**fermare**, e intanto si può giocare a **Gemini Snake**.

---

## 3. La biblioteca

**Biblioteca** ✅
L'elenco di tutti i PDF, indipendente dai progetti. I PDF *vivono* qui: i progetti li usano
ma non li possiedono. Togliere un PDF da un progetto o eliminare un progetto non cancella il
PDF; un PDF si elimina dalla biblioteca solo se nessun progetto lo usa.

**Cartella** ✅
Dove sta un catalogo nella biblioteca (es. `Motori/Lombardini`). Ogni catalogo sta in **una
sola** cartella. Serve solo a tenere ordine.

**Tag del catalogo** ✅
Etichette **libere** e **multiple** (es. `motori`, `lombardini`, `da studiare`). Servono a
filtrare e ritrovare i cataloghi in tutte le cartelle. *Non confonderli con i tag di pagina.*

**Scheda (del catalogo)** ✅
La pagina di un catalogo con tutto ciò che si sa di lui: titolo, cartella, tag, descrizione,
campi ⚠️ (o modalità 🆕), collezioni di pagine, analisi archiviate. È **descrittiva**: dice
com'è fatto il PDF, non dà ordini all'estrazione.

**Descrizione (del catalogo)** ✅
Testo libero su cosa contiene il PDF e com'è organizzato (marca, prodotti, sezioni,
particolarità). Scritta a mano o proposta dall'analisi. *Non è una nota per l'estrazione*
(vedi *note per il PDF*).

**Campi (del catalogo)** ✅ ⚠️
Oggi: un elenco di colonne desiderate (nome + descrizione) per quel PDF. Servono solo a
proporre l'obiettivo e al raggruppamento; **non contano mai nell'indicizzazione**.
Con la proposta verrebbero sostituiti dalle *modalità di indicizzazione*.

**Modalità di indicizzazione** 🆕
Un modo di trasformare un PDF in tabella: **cosa è una riga + colonne + pagine da cui
estrarre**. Esempio sullo stesso catalogo:
- modalità "per motore": una riga per motore, colonne motore/cilindri/alesaggio, pagine 9-250;
- modalità "per componente": una riga per codice ricambio, colonne motore/codice/descrizione,
  pagine 251-300.

È **facoltativa** (un PDF può non averne) e serve a **pianificare e raggruppare**: decide in
quali progetti potrà finire il PDF. Come i campi, **non** conta nell'indicizzazione: lì conta
solo il progetto.

**Collezione di pagine** ✅ ⚠️
Un **intervallo continuo** di pagine con **un tag di pagina** e un **commento** facoltativo
(es. *pagine 5-8, Legenda, "simboli delle versioni"*). Una pagina può stare in più collezioni.
Può essere scritta a mano, proposta da Gemini o **dedotta** da Gemini dall'indice senza vedere
quelle pagine. Insieme, le collezioni sono la **mappa** del catalogo.
Con la proposta descriverebbero solo il **contenuto**; le pagine da indicizzare starebbero
nella modalità (o nel progetto).

**Tag di pagina** ✅
L'elenco **fisso** di etichette per le collezioni (fisso perché il programma le usa): *da
indicizzare, pagina d'esempio, copertina, indice, introduzione, legenda/simboli, tabelle
tecniche, disegni/immagini, avvertenze/note, pubblicità/vuota, altro*.

**Visualizzatore** ✅
Nella scheda: miniature a sinistra, pagina grande a destra, frecce per sfogliare,
*Apri il PDF ↗* per aprire l'originale nel browser alla pagina mostrata. Serve a creare le
collezioni.

**Analisi (con Gemini)** ✅
Gemini guarda alcune pagine scelte (o il PDF intero) e propone **una scheda**: descrizione,
tag, campi, collezioni, osservazioni. Il risultato è una **proposta**.

**Proposta** ✅
Il risultato di un'analisi: **non viene applicato da solo**. Si sceglie voce per voce cosa
copiare nella scheda. Resta nell'**archivio delle analisi** con uno stato: *da rivedere*,
*applicata*, *scartata* o *non valida*.

**Raggruppamento (con Gemini)** ✅ ⚠️
Gemini confronta le schede di più cataloghi e propone quali **accorpare in un progetto**
(cioè in un'unica tabella). Il risultato è un **report** con:
- **gruppi**: nome del progetto, cataloghi, commento, **tag comuni**, **colonne unificate**;
- **non accorpabili**: cataloghi rimasti fuori, ciascuno con il motivo.

Nel report si spostano cataloghi tra i gruppi, si assegnano tag e si **creano i progetti**.
Oggi gli elementi da raggruppare sono i PDF; con la proposta sarebbero le coppie
**PDF × modalità** (lo stesso PDF può finire in due gruppi con modalità diverse, mai due
volte nello stesso).

**Colonne unificate** ✅ ⚠️
Le colonne proposte dal raggruppamento per un gruppo, valide per tutti i suoi cataloghi.
Oggi diventano un *suggerimento* per la configurazione del progetto creato; con la proposta
diventerebbero le **colonne del progetto** (vincolanti).

---

## 4. Il progetto

**Progetto** ✅
**Una tabella**: uno o più PDF con struttura compatibile, estratti con le **stesse regole**,
che producono **un CSV unico**. Lo stesso PDF può stare in più progetti (per esempio uno "per
motore" e uno "per componente"): i risultati restano separati.

**Impostazioni globali del progetto** 🆕
Cosa deve uscire dal progetto, decise dall'utente (o arrivate dal raggruppamento):
**descrizione**, **cosa è una riga**, **colonne** (nome + descrizione), **altre indicazioni**.
Sono la fonte autorevole: la specifica deve rispettarle.
Oggi ✅ c'è solo un elenco di colonne suggerite (dal raggruppamento) e l'*obiettivo* a parole.

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

**Obiettivo** ✅ ⚠️
Oggi: la casella "Cosa vuoi ottenere" della configurazione guidata, a parole. Con la proposta
verrebbe sostituita dalle impostazioni globali (resterebbe solo per le *altre indicazioni*).

**PDF del progetto** ✅ ⚠️
I PDF che il progetto usa. Per ciascuno il progetto ha le sue **impostazioni per PDF**. Con la
proposta, aprendo un PDF dal progetto si vedrebbe la sua **scheda della biblioteca**.

**Impostazioni per PDF (nel progetto)** ✅
Valgono solo dentro quel progetto:
- **pagine da indicizzare** di quel PDF;
- **note per il PDF**: istruzioni di estrazione in più, solo per lui (es. "in questo catalogo
  la versione è sotto il nome del motore"). Possono essere proposte da Gemini in
  configurazione.

**Note per il PDF** vs **descrizione** ✅
La *descrizione* (biblioteca) dice **com'è** il PDF; le *note* (progetto) dicono **come
estrarre** da lui in quel progetto. Le note entrano nelle regole, la descrizione no.

**Primer** ✅
Testo che spiega a Gemini **com'è fatto il documento** (struttura, convenzioni, sigle, cosa
non si capisce da una pagina isolata). Viene dato insieme a **ogni** pagina durante
l'indicizzazione. Oggi ce n'è uno per progetto.

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
1. scegli le *pagine mostrate a Gemini* e l'obiettivo;
2. Gemini propone primer, specifica, note per i PDF e fa una *prova*;
3. tu correggi **a parole**, e Gemini produce una nuova versione;
4. *Salva configurazione* quando il risultato ti convince.

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

---

## 5. Parole che si somigliano

**I "tag"** — tre cose diverse:

| Termine | Dove | Liberi? | A cosa servono |
|---|---|---|---|
| tag del catalogo | scheda | sì | filtrare e ritrovare i cataloghi |
| tag di pagina | collezioni | no, elenco fisso | dire cosa c'è in un intervallo di pagine |
| tag comuni | gruppo del raggruppamento | sì | tag da dare insieme ai cataloghi del gruppo |

**I testi liberi** — chi li scrive e a cosa servono:

| Termine | Dove | Descrive o comanda? | Entra nelle regole? |
|---|---|---|---|
| descrizione | scheda del catalogo | descrive il PDF | no |
| commento | collezione di pagine | descrive quelle pagine | no |
| osservazioni | analisi / raggruppamento (Gemini) | dubbi e note di Gemini | no |
| commento del gruppo | raggruppamento | perché stanno insieme | no |
| note per il PDF | progetto | comanda l'estrazione di quel PDF | **sì** |
| istruzioni | specifica | comandano l'estrazione di tutti i PDF | **sì** |
| indicazioni | prompt di analisi / raggruppamento | dicono a Gemini cosa cercare | no |
| obiettivo ⚠️ | configurazione guidata | cosa vuoi ottenere, a parole | indirettamente |

**Le "pagine"** — cinque selezioni diverse:

| Selezione | Dove si sceglie | A cosa serve |
|---|---|---|
| pagine da inviare | analisi (scheda) | cosa guarda Gemini per proporre la scheda |
| pagine d'esempio | collezione *pagina d'esempio* | pagine rappresentative, proposte in configurazione e nel raggruppamento |
| pagine mostrate a Gemini | configurazione guidata (max 40) | cosa guarda Gemini per scrivere primer e specifica |
| pagine della prova | configurazione guidata (max 6) | dove si prova l'estrazione |
| pagine da indicizzare | collezione *da indicizzare* → progetto ⚠️ | cosa viene estratto davvero |

**Le "colonne"**:

| Termine | Dove | Conta nell'indicizzazione? |
|---|---|---|
| campi ⚠️ / colonne della modalità 🆕 | scheda del catalogo | no (pianificazione, raggruppamento) |
| colonne unificate | gruppo del raggruppamento | no (diventano colonne del progetto) |
| colonne del progetto 🆕 | impostazioni globali | sì, tramite la specifica che deve rispettarle |
| colonne della specifica | specifica | **sì**, sono quelle usate davvero |

**Le "proposte" di Gemini** — niente viene applicato da solo:

| Passaggio | Cosa propone | Dove si decide |
|---|---|---|
| analisi | una scheda per un catalogo | proposta nella scheda, voce per voce |
| raggruppamento | gruppi di cataloghi → progetti | report, gruppo per gruppo |
| configurazione guidata | primer, specifica, note, pagine della prova | versioni, correzioni a parole, salvataggio |

---

## 6. Regole di sicurezza dei prompt ✅

- **Indicazioni** (modificabili dall'utente) e **cornice** (fissa, scritta dal programma) sono
  separate: la cornice contiene il **formato della risposta** e prevale sulle indicazioni.
- Ogni risposta strutturata viene **convalidata in modo rigoroso**: se la struttura non torna
  (campi mancanti o in più, tipi sbagliati, valori inesistenti) è **non valida**, non viene
  usata e resta in archivio con la risposta originale.
- Dentro una risposta valida, le singole voci inutilizzabili (es. pagine fuori dal documento)
  vengono scartate e **segnalate**.
