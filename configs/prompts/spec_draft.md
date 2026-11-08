Devi scrivere una "specifica di indicizzazione" in YAML per un programma che estrae dati dalle pagine di un PDF e li salva in un CSV. Il programma invia a un modello AI ogni pagina (come immagine) insieme alla specifica; il modello restituisce le righe del CSV.

## FORMATO DELLA SPECIFICA
Ecco un modello commentato del formato, con tutti i campi disponibili:

```yaml
{{template}}
```

## RICHIESTA DELL'UTENTE
{{request}}
{{example}}{{primer}}{{pages_note}}
## COSA FARE
Scrivi la specifica che realizza la richiesta dell'utente:
- `row`: definisci con precisione cosa rappresenta una riga.
- `columns`: una colonna per ogni dato richiesto, con una `description` chiara e concreta (dove si trova il dato nella pagina, come trascriverlo). Se l'utente ha fornito un CSV d'esempio, usa ESATTAMENTE gli stessi nomi di colonna e lo stesso ordine (escluse le colonne con numero di pagina e nome del file, che il programma aggiunge da solo tramite `output.page_column` e `output.source_column`: usa per queste i nomi del CSV d'esempio, se presenti).
- Marca `context: true` le colonne il cui valore vale per tutta la pagina (intestazioni) e potrebbe non essere ripetuto sulle righe.
- Usa `required`, `type`, `pattern` solo dove sono chiaramente utili.
- `instructions`: riporta tutte le regole dell'utente e quelle che ricavi dal documento, scritte come istruzioni operative.
- `mappings`: aggiungi tabelle di conversione se la richiesta o il documento le rendono necessarie.
- `pages`: lascialo vuoto salvo indicazioni dell'utente.
- `name`: usa "{{name}}".

Rispondi SOLO con il YAML della specifica, senza spiegazioni e senza blocchi di codice. Scrivi i testi in italiano.
