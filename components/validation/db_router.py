"""Phase 1 validation layer - Database path router.

Centralizes database path selection by trading mode (paper, live, replay).
"""

import os
from pathlib import Path
from typing import Optional


# Default base directory for all trade databases
DEFAULT_DB_BASE: Path = Path("research")

# Database filenames
PAPER_DB_NAME: str = "paper_trades.db"
LIVE_DB_NAME: str = "live_trades.db"
REPLAY_DB_NAME: str = "replay_trades.db"

# Environment variable for mode override
TRADE_MODE_ENV: str = "TRADE_MODE"

# Valid modes
VALID_MODES: tuple = ("paper", "live", "replay")


class DatabaseRouter:
    """Router for selecting database path by trading mode.
    
    Provides:
    - get_db_path(mode): Returns Path for specified mode
    - get_current_mode(): Returns current mode from env or default
    - get_current_db_path(): Returns path for current mode
    """
    
    def __init__(
        self,
        base_dir: Optional[Path] = None,
        default_mode: str = "paper",
    ) -> None:
        """Initialize database router.
        
        Args:
            base_dir: Base directory for all databases. Defaults to research/.
            default_mode: Default mode if TRADE_MODE env not set. Defaults to paper.
        """
        self._base_dir: Path = base_dir or DEFAULT_DB_BASE
        self._default_mode: str = default_mode
        
        # Ensure base directory exists
        self._base_dir.mkdir(parents=True, exist_ok=True)
    
    def get_db_path(self, mode: Optional[str] = None) -> Path:
        """Get database path for specified mode.
        
        Args:
            mode: Trading mode (paper, live, replay). Defaults to current mode.
            
        Returns:
            Path to trades.db for specified mode.
            
        Raises:
            ValueError: If mode is not valid.
        """
        if mode is None:
            mode = self.get_current_mode()
        
        mode = mode.lower()
        if mode not in VALID_MODES:
            raise ValueError(f"Invalid mode '{mode}'. Must be one of: {VALID_MODES}")
        
        if mode == "paper":
            return self._base_dir / PAPER_DB_NAME
        elif mode == "live":
            return self._base_dir / LIVE_DB_NAME
        else:
            return self._base_dir / REPLAY_DB_NAME
    
    def get_current_mode(self) -> str:
        """Get current trading mode from environment or default.
        
        Returns:
            Current mode string (paper, live, replay).
        """
        mode = os.environ.get(TRADE_MODE_ENV, self._default_mode).lower()
        if mode not in VALID_MODES:
            # Fallback to default if env is invalid
            return self._default_mode
        return mode
    
    def get_current_db_path(self) -> Path:
        """Get database path for current mode.
        
        Returns:
            Path to trades.db for current mode.
        """
        return self.get_db_path(self.get_current_mode())
    
    def set_mode(self, mode: str) -> None:
        """Set trading mode in environment.
        
        Args:
            mode: Trading mode (paper, live, replay).
            
        Raises:
            ValueError: If mode is not valid.
        """
        mode = mode.lower()
        if mode not in VALID_MODES:
            raise ValueError(f"Invalid mode '{mode}'. Must be one of: {VALID_MODES}")
        os.environ[TRADE_MODE_ENV] = mode
    
    def get_all_db_paths(self) -> dict:
        """Get paths for all mode databases.
        
        Returns:
            Dict mapping mode name to Path.
        """
        return {
            "paper": self.get_db_path("paper"),
            "live": self.get_db_path("live"),
            "replay": self.get_db_path("replay"),
        }


# Global router instance
_router: Optional[DatabaseRouter] = None


def get_db_router() -> DatabaseRouter:
    """Get or create global database router.
    
    Returns:
        Global DatabaseRouter instance.
    """
    global _router
    if _router is None:
        _router = DatabaseRouter()
    return _router


def get_db_path(mode: Optional[str] = None) -> Path:
    """Convenience function to get database path.
    
    Args:
        mode: Trading mode. Defaults to current mode.
        
    Returns:
        Path to trades.db for specified mode.
    """
    return get_db_router().get_db_path(mode)


def get_current_mode() -> str:
    """Convenience function to get current mode.
    
    Returns:
        Current mode string.
    """
    return get_db_router().get_current_mode()


def set_trade_mode(mode: str) -> None:
    """Convenience function to set trading mode.
    
    Args:
        mode: Trading mode (paper, live, replay).
    """
    get_db_router().set_mode(mode)