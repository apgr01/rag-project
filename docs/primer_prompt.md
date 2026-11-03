# Prompt per generare un primer

Il **primer** è il contesto che accompagna ogni immagine di pagina inviata a Gemini.
Poiché il modello vede una pagina alla volta, senza indice né pagine adiacenti, il primer
gli fornisce le "istruzioni per l'uso" del documento.

Per crearne uno: apri una chat con un assistente AI che accetti PDF, allega il documento
completo e incolla il prompt qui sotto. Rivedi il risultato, rispondi agli eventuali dubbi
che il modello segnala, e salva il primer finale in `configs/primers/<nome>.md`.

> Nella roadmap è prevista la generazione automatica della bozza del primer direttamente
> dal programma (vedi `docs/ROADMAP.md`).

---

```
Ho un sistema che passa ad un AI le pagine di un pdf sottoforma di immagini, UNA pagina alla volta, SENZA vedere le altre pagine né l'indice generale, e che le chiede di descrivere il contenuto della pagina — quindi ha bisogno di un contesto scritto a parte che gli spieghi come è fatto il documento nel complesso, prima di interpretare la pagina singola.

Ti allego il documento PDF completo. Il tuo compito è analizzarlo, e scrivere questo contesto — lo chiamo "primer" — che verrà dato a Gemini insieme a ogni singola immagine di pagina.

Prima di tutto, guarda il documento con mente aperta: non presumere che si tratti di un tipo di documento specifico (catalogo, manuale, report, ecc.) finché non lo capisci da quello che vedi. Descrivi quello che c'è realmente, con parole tue, invece di far rientrare il contenuto in categorie predefinite.

Il primer deve permettere all'AI di interpretare correttamente una pagina isolata, anche quando quella pagina da sola non basterebbe a capire il significato di certi codici, sigle o riferimenti. Scrivilo coprendo questi punti:

1. **Cosa è il documento**: tipo, dominio/settore, lingua, a chi sembra rivolto — dedotto da quello che vedi, non assunto a priori.

2. **Natura del contenuto**: descrivi con parole tue com'è fatta visivamente una pagina tipica di questo documento, e come questo cambia da una sezione all'altra. Non forzare la descrizione in categorie fisse: se una sezione è un ibrido di cose diverse, dillo. Se il documento ha aspetti visivamente insoliti (layout particolari, elementi ricorrenti che non sono né testo né immagine né tabella classica), segnalali.

3. **Struttura macro**: come è organizzato il documento, indice dei capitoli/sezioni nell'ordine in cui appaiono, cosa contiene ciascuno, e — se puoi stimarlo dalla numerazione — a che pagine iniziano e finiscono le sezioni principali.

4. **Convenzioni ricorrenti**: formati di codici o riferimenti, struttura tipica delle tabelle se ce ne sono, simboli, sigle, abbreviazioni che si ripetono nel documento e il loro significato dove riesci a dedurlo dal contesto.

5. **Cosa NON è deducibile da una singola pagina isolata** ma serve per interpretarla — questa è la parte più importante: pensa a cosa l'AI rischierebbe di fraintendere o non poter dedurre vedendo solo quella pagina, senza indice né pagine adiacenti, e scrivilo esplicitamente.

Non limitarti a un riassunto generico del contenuto: l'obiettivo è dare all'AI le "istruzioni per l'uso" del documento, non un abstract.

Se il documento è molto lungo e alcune sezioni sono ripetitive (es. tante schede prodotto con lo stesso layout), non è necessario che tu descriva ogni singola variazione — concentrati su pattern e convenzioni generali, e segnala esplicitamente se noti eccezioni degne di nota rispetto al pattern dominante.

Se su qualche punto non riesci a essere sicuro (es. non è chiaro se una convenzione vale per tutto il documento o solo per una sezione), dimmelo esplicitamente invece di inventare — te lo confermo io.

Alla fine scrivimi il primer in un blocco Markdown pronto da salvare in un file, in italiano, con un titolo che identifichi il template (es. "# Primer — [nome/tipo del documento]").
```
