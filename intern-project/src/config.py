"""Configuration loading and startup validation."""
import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader: KEY=VALUE lines, comments ignored, no override of real env."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass
class Settings:
    gemini_api_key: str
    gemini_model: str
    gemini_embedding_model: str
    jwt_secret: str
    jwt_expire_minutes: int
    database_path: Path
    knowledge_base_path: Path
    retrieval_top_k: int = 3

    @classmethod
    def load(cls, project_root: Path | None = None) -> "Settings":
        root = project_root or Path(__file__).resolve().parent.parent
        _load_dotenv(root / ".env")
        gemini_key = os.environ.get("GEMINI_API_KEY", "")
        jwt_secret = os.environ.get("JWT_SECRET", "")
        missing = [
            name for name, value in (
                ("GEMINI_API_KEY", gemini_key),
                ("JWT_SECRET", jwt_secret),
            ) if not value or value.startswith(("your-", "change-me"))
        ]
        if missing:
            raise RuntimeError(
                f"Missing required configuration: {', '.join(missing)}. "
                "Copy .env.example to .env and fill in real values."
            )
        if len(jwt_secret.encode("utf-8")) < 32:
            raise RuntimeError("JWT_SECRET must contain at least 32 bytes")
        expiry = int(os.environ.get("JWT_EXPIRE_MINUTES", "60"))
        if not 1 <= expiry <= 1440:
            raise RuntimeError("JWT_EXPIRE_MINUTES must be between 1 and 1440")
        db_path = Path(os.environ.get("DATABASE_PATH", "ticket_assistant.db"))
        if not db_path.is_absolute():
            db_path = root / db_path
        kb_path = Path(os.environ.get("KNOWLEDGE_BASE_PATH", "knowledge_base"))
        if not kb_path.is_absolute():
            kb_path = root / kb_path
        return cls(
            gemini_api_key=gemini_key,
            gemini_model=os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite"),
            gemini_embedding_model=os.environ.get("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001"),
            jwt_secret=jwt_secret,
            jwt_expire_minutes=int(os.environ.get("JWT_EXPIRE_MINUTES", "60")),
            database_path=db_path,
            knowledge_base_path=kb_path,
        )
