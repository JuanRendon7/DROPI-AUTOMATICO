from app.core.exceptions import AgentError, RateLimitError  # noqa: F401 — re-export


class DropiAuthError(AgentError):
    def __init__(self, message: str = "Credenciales de Dropi inválidas") -> None:
        super().__init__("dropi", message)


class DropiAPIError(AgentError):
    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__("dropi", f"API error {status_code}: {message}")


class DropiScrapingError(AgentError):
    def __init__(self, message: str) -> None:
        super().__init__("dropi", f"Scraping error: {message}")


class DropiSessionExpiredError(AgentError):
    def __init__(self) -> None:
        super().__init__("dropi", "Sesión de Dropi expirada — se requiere re-login")


class DropiProductNotFoundError(AgentError):
    def __init__(self, dropi_id: str) -> None:
        self.dropi_id = dropi_id
        super().__init__("dropi", f"Producto {dropi_id} no encontrado en Dropi")
