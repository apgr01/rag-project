from abc import ABC, abstractmethod

class BaseQueryEngine(ABC):
    """Interfaccia astratta per qualsiasi motore di risposta alle domande."""

    @abstractmethod
    def answer_query(self, query: str) -> dict:
        """
        Riceve la domanda dell'utente e restituisce la risposta 
        insieme ai metadati delle fonti utilizzate.
        """
        pass