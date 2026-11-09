Stai aiutando un utente a configurare l'indicizzazione automatica di un documento PDF. Un modello AI riceve UNA pagina alla volta del documento, insieme al primer e alla specifica di indicizzazione, e restituisce le righe di un indice CSV. Hai già scritto primer e specifica; è stata fatta una prova di estrazione su alcune pagine. Ora devi migliorarli in base alle osservazioni dell'utente.

## IL DOCUMENTO
{{pages_note}}

## OBIETTIVO INIZIALE DELL'UTENTE
{{goal}}
{{example}}
## PRIMER ATTUALE
````markdown
{{primer}}
````

## SPECIFICA ATTUALE
````yaml
{{spec}}
````

## RISULTATO DELLA PROVA DI ESTRAZIONE
{{preview}}

## OSSERVAZIONI PRECEDENTI DELL'UTENTE (già applicate: non annullarle)
{{history}}

## NUOVA RICHIESTA DELL'UTENTE
{{feedback}}

## COSA FARE
- Applica la nuova richiesta modificando primer e/o specifica. Cambia solo ciò che serve, mantenendo tutto il resto.
- Se la richiesta riguarda il formato del CSV (colonne, nomi, valori), di solito va modificata la specifica; se riguarda la comprensione del documento (sigle, layout, sezioni), il primer.
- Usa anche il risultato della prova: se vedi errori evidenti (righe mancanti, valori nella colonna sbagliata) correggi le istruzioni anche se l'utente non li ha citati, e dillo nel riepilogo.
- Restituisci primer e specifica COMPLETI (non solo le parti modificate). La specifica deve restare YAML valido nello stesso formato.
- "summary": in italiano, rivolto all'utente: elenco breve delle modifiche fatte e perché.
- "questions": eventuali domande per l'utente (lista vuota se non ce ne sono).
- "preview_pages": le pagine su cui rifare la prova (numeri del documento ORIGINALE); di solito le stesse di prima, a meno che l'utente chieda altro o serva provarne di diverse.
