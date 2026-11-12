"""ragpdf — elaborazione vision-first di documenti PDF con Gemini.

Il pacchetto offre due modalità d'uso, che condividono la stessa infrastruttura
(workspace, primer, rendering delle pagine, client Gemini):

- **indicizzazione**: estrae dalle pagine righe strutturate secondo una specifica
  scritta dall'utente e produce un CSV;
- **RAG**: descrive le pagine, le indicizza in un database vettoriale e risponde
  a domande in linguaggio naturale.
"""

__version__ = "0.2.0"
