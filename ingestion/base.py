from abc import ABC, abstractmethod

class BaseIngestor(ABC):
    """Interfaccia astratta per qualsiasi pipeline di Ingestion."""

    @abstractmethod
    def process_document(self, file_path: str) -> dict:
        """
        Processa un documento (PDF, testo, ecc.) e lo salva nel database vettoriale.
        Restituisce un dizionario con le statistiche dell'operazione.
        """
        pass