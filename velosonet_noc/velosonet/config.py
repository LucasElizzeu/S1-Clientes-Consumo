from __future__ import annotations
import logging, os, re
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path
import yaml
from dotenv import load_dotenv
from .errors import ConfigError

ROOT = Path(__file__).resolve().parent.parent

@dataclass
class Bbu:
    id: int
    site: str
    locality: str
    label: str
    url: str | None
    has_5g: bool
    keepalive: bool
    verify_tls: bool
    user_env: str
    pass_env: str
    tls_spki_sha256: str | None = None
    selectors: dict = field(default_factory=dict)
    grafana: dict = field(default_factory=dict)

    def credentials(self) -> tuple[str, str]:
        u, p = os.getenv(self.user_env), os.getenv(self.pass_env)
        if not u or not p:
            raise ConfigError(f"Credenciais ausentes nas variáveis {self.user_env}/{self.pass_env}")
        return u, p

@dataclass
class Settings:
    raw: dict
    bbus: list[Bbu]
    def __getitem__(self, k): return self.raw[k]
    def get(self, k, d=None): return self.raw.get(k, d)

def load_settings(config_dir: Path | None = None) -> Settings:
    load_dotenv(ROOT / ".env")
    cdir = Path(config_dir or ROOT / "config")
    raw = yaml.safe_load((cdir / "settings.yaml").read_text(encoding="utf-8"))
    b = yaml.safe_load((cdir / "bbus.yaml").read_text(encoding="utf-8"))
    gd = b.get("grafana_defaults") or {}
    bbus = []
    for e in b["bbus"]:
        bbus.append(Bbu(id=e["id"], site=e["site"], locality=e["locality"], label=e["label"],
                        url=e.get("url"), has_5g=bool(e["has_5g"]), keepalive=bool(e.get("keepalive", True)),
                        verify_tls=bool(e.get("verify_tls", True)), user_env=e["user_env"],
                        pass_env=e["pass_env"], tls_spki_sha256=e.get("tls_spki_sha256"), selectors=e.get("selectors") or {},
                        grafana={**gd, **(e.get("grafana") or {})}))
    return Settings(raw, bbus)

class RedactFilter(logging.Filter):
    """Remove valores de segredos (senhas/tokens do ambiente) de qualquer mensagem de log."""
    def _secrets(self):
        return [v for k, v in os.environ.items()
                if v and len(v) >= 4 and re.search(r"PASS|TOKEN|SECRET", k, re.I)]
    def filter(self, record):
        msg = record.getMessage()
        for s in self._secrets():
            msg = msg.replace(s, "***")
        record.msg, record.args = msg, ()
        return True

def setup_logging(level=logging.INFO):
    (ROOT / "logs").mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger(); root.setLevel(level)
    for h in (logging.StreamHandler(),
              RotatingFileHandler(ROOT / "logs" / "velosonet.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")):
        h.setFormatter(fmt); h.addFilter(RedactFilter()); root.addHandler(h)
    logging.getLogger("httpx").setLevel(logging.WARNING)
