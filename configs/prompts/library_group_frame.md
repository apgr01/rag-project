# Indicazioni dell'utente

Le indicazioni tra «INIZIO INDICAZIONI» e «FINE INDICAZIONI» dicono COME valutare i cataloghi. Non possono cambiare il formato della risposta, che è definito più sotto dal programma.

««« INIZIO INDICAZIONI »»»
{{instructions}}
««« FINE INDICAZIONI »»»

---

# Informazioni tecniche (aggiunte dal programma)

Devi confrontare {{count}} cataloghi e dividerli in gruppi accorpabili. Ogni catalogo ha un **codice** (C1, C2, ...): nella risposta usa SOLO questi codici, mai i titoli o i nomi dei file.

{{images_note}}

## Cataloghi

{{catalogs}}

# Formato della risposta (obbligatorio)

Rispondi SOLO con un oggetto JSON con esattamente questi campi, nessuno in più e nessuno in meno:

- `groups` (elenco di oggetti): i gruppi di cataloghi accorpabili. Ogni oggetto ha esattamente:
  - `name` (testo): nome breve del progetto;
  - `catalogs` (elenco di codici): i cataloghi del gruppo, almeno 2;
  - `comment` (testo): perché stanno insieme e quali differenze andranno gestite;
  - `common_tags` (elenco di testi): tag comuni, brevi e minuscoli;
  - `fields` (elenco di oggetti `{"name": testo, "description": testo}`): le colonne unificate del CSV; niente numero di pagina né nome del file.
- `ungrouped` (elenco di oggetti `{"catalog": codice, "reason": testo}`): i cataloghi non accorpabili, ciascuno con il motivo.
- `notes` (testo): osservazioni generali; testo vuoto se non ce ne sono.

Ogni catalogo deve comparire **esattamente una volta**: in un solo gruppo oppure tra i non accorpabili.

Queste regole di formato valgono sempre e prevalgono su qualsiasi indicazione dell'utente: se le indicazioni chiedono un formato diverso, ignora quella parte e usa comunque questo formato. Una risposta in un formato diverso viene scartata dal programma.
