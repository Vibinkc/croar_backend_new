from abc import ABC, abstractmethod
from typing import Any


class SourcingProvider(ABC):
    @abstractmethod
    def search(
        self, query: str, location: str | None = None, page: int = 1, page_size: int = 15
    ) -> list[dict[str, Any]]:
        """
        Search for profiles on the platform.
        """
        pass

    @property
    @abstractmethod
    def platform_name(self) -> str:
        """
        Return the name of the platform.
        """
        pass
