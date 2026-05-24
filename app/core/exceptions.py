class DropiSalesError(Exception):
    """Base exception para el proyecto."""


class AgentError(DropiSalesError):
    """Error en la ejecución de un agente."""

    def __init__(self, agent: str, message: str) -> None:
        self.agent = agent
        super().__init__(f"[{agent}] {message}")


class DropiScrapingError(AgentError):
    """Error al interactuar con el panel de Dropi."""


class CampaignCreationError(AgentError):
    """Error al crear una campaña publicitaria."""


class ResearchError(AgentError):
    """Error durante la investigación de productos."""


class ConfigurationError(DropiSalesError):
    """Configuración faltante o inválida."""


class RateLimitError(DropiSalesError):
    """Rate limit alcanzado en una API externa."""

    def __init__(self, service: str, retry_after: int | None = None) -> None:
        self.service = service
        self.retry_after = retry_after
        msg = f"Rate limit en {service}"
        if retry_after:
            msg += f" — reintentar en {retry_after}s"
        super().__init__(msg)
