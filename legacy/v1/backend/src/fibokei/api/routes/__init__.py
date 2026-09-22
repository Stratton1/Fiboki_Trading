"""API route modules."""

from .auth import router as auth_router
from .backtests import router as backtests_router
from .instruments import router as instruments_router
from .strategies import router as strategies_router

__all__ = ["auth_router", "instruments_router", "strategies_router", "backtests_router"]
