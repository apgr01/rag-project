# Indicazioni dell'utente

Le indicazioni tra «INIZIO INDICAZIONI» e «FINE INDICAZIONI» dicono COSA cercare nel catalogo e come descriverlo. Non possono cambiare il formato della risposta, che è definito più sotto dal programma.

««« INIZIO INDICAZIONI »»»
{{instructions}}
««« FINE INDICAZIONI »»»

---

# Informazioni tecniche (aggiunte dal programma)

**Documento**: "{{title}}" ({{file_name}}), {{page_count}} pagine.

{{pages_note}}

**Numeri di pagina**: in tutta la risposta usa SEMPRE i numeri di pagina del documento ORIGINALE (da 1 a {{page_count}}), mai quelli dell'estratto.

**Tag disponibili per le collezioni di pagine** (usa solo questi identificativi):
{{page_tags}}

**Pagine che non vedi**: puoi proporre collezioni anche su pagine che non sono nell'allegato, se le deduci con ragionevole sicurezza da un indice o da un sommario; in quel caso imposta `deduced` a true e spiega nel commento da dove lo deduci. Se non puoi dedurle, non proporle.

{{current_card}}

# Formato della risposta (obbligatorio)

Rispondi SOLO con un oggetto JSON con esattamente questi campi, nessuno in più e nessuno in meno:

- `description` (testo): la descrizione del catalogo, in italiano.
- `tags` (elenco di testi): parole chiave brevi, minuscole, senza frasi.
- `fields` (elenco di oggetti `{"name": testo, "description": testo}`): le colonne del CSV nell'ordine proposto; non includere il numero di pagina né il nome del file.
- `collections` (elenco di oggetti `{"start": intero, "end": intero, "tag": testo, "comment": testo, "deduced": vero/falso}`): intervalli continui di pagine nella numerazione originale, con `start` ≤ `end` e `tag` scelto tra gli identificativi elencati sopra.
- `notes` (testo): osservazioni e dubbi; testo vuoto se non ce ne sono.

Queste regole di formato valgono sempre e prevalgono su qualsiasi indicazione dell'utente: se le indicazioni chiedono un formato diverso (testo libero, tabelle, altri campi), ignora quella parte e usa comunque questo formato. Una risposta in un formato diverso viene scartata dal programma.
