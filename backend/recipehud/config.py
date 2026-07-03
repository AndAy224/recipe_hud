from pathlib import Path

from pydantic_settings import BaseSettings

REPO_ROOT = Path(__file__).resolve().parents[2]


class Config(BaseSettings):
    """Process-level configuration (env vars with RECIPEHUD_ prefix).

    Runtime-tunable settings (timeouts, schedules, volume...) live in the
    settings table instead — see settings_store.py.
    """

    model_config = {"env_prefix": "RECIPEHUD_"}

    host: str = "0.0.0.0"
    port: int = 8000
    db_path: Path = REPO_ROOT / "data" / "recipehud.db"
    frontend_dir: Path = REPO_ROOT / "frontend"
    # auto | wlopm | x11 | mock
    display_backend: str = "auto"
    debug: bool = False
    # Where the kiosk's Home button / launcher lives, as seen from the Pi itself.
    launcher_url: str = "http://localhost:8000/"


CONFIG = Config()
