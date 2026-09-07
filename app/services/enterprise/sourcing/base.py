from abc import ABC, abstractmethod
from typing import Any


class SourcingUnavailable(RuntimeError):
    """The provider could not be reached or refused the request.

    Distinct from an empty result on purpose. Both used to arrive at the UI as an empty
    list, so an expired key or an exhausted balance was reported to the recruiter as
    "nothing matched those filters" — sending them off to loosen filters that were fine.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


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
