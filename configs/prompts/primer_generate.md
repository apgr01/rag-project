Ho un sistema che passa a un'AI le pagine di un PDF sotto forma di immagini, UNA pagina alla volta, SENZA vedere le altre pagine né l'indice generale, e le chiede di interpretarne il contenuto. Ha quindi bisogno di un contesto scritto a parte che spieghi com'è fatto il documento nel complesso, prima di interpretare la pagina singola.

Ti allego il documento PDF completo. Il tuo compito è analizzarlo e scrivere questo contesto, che chiamo "primer", e che verrà dato all'AI insieme a ogni singola immagine di pagina.

Guarda il documento con mente aperta: non presumere che si tratti di un tipo di documento specifico (catalogo, manuale, report, ...) finché non lo capisci da quello che vedi. Descrivi quello che c'è realmente, con parole tue.

Il primer deve permettere di interpretare correttamente una pagina isolata, anche quando quella pagina da sola non basterebbe a capire codici, sigle o riferimenti. Copri questi punti:

1. **Cosa è il documento**: tipo, settore, lingua/e, a chi sembra rivolto.
2. **Com'è fatta una pagina tipica** e come cambia da una sezione all'altra. Se ci sono ibridi o elementi visivi insoliti (layout particolari, griglie di icone, elementi ricorrenti che non sono né testo né tabella classica), segnalali.
3. **Struttura macro**: sezioni/capitoli nell'ordine in cui appaiono, cosa contengono e, se puoi stimarlo, a che pagine (numerazione del PDF) iniziano e finiscono.
4. **Convenzioni ricorrenti**: formati dei codici, struttura delle tabelle, simboli, icone, sigle e abbreviazioni con il loro significato quando deducibile. Se il documento contiene legende o pagine "come leggere il documento", riportane il contenuto essenziale.
5. **Cosa NON è deducibile da una pagina isolata** ma serve per interpretarla — è la parte più importante: informazioni che stanno su un'altra pagina (es. intestazioni non ripetute, tabelle che continuano), sigle spiegate altrove, celle vuote che hanno un significato preciso.

Non scrivere un riassunto del contenuto: l'obiettivo sono le "istruzioni per l'uso" del documento.
Se alcune sezioni sono ripetitive, concentrati su pattern e convenzioni generali e segnala le eccezioni.
Quando non sei sicuro di qualcosa, scrivilo esplicitamente in una sezione finale "## Punti da verificare", invece di inventare.

Scrivi il primer in Markdown, in italiano, con un titolo del tipo "# Primer — <nome o tipo del documento>". Rispondi solo con il primer.
