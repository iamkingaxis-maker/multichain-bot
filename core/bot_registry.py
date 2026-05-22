from __future__ import annotations
import logging
from dataclasses import dataclass, field
from pathlib import Path
from core.bot_config import BotConfig


logger = logging.getLogger(__name__)


@dataclass
class BotRegistry:
    configs: list[BotConfig] = field(default_factory=list)

    @classmethod
    def from_directory(cls, dir_path) -> "BotRegistry":
        dir_path = Path(dir_path)
        if not dir_path.exists():
            return cls(configs=[])

        configs: list[BotConfig] = []
        seen: set[str] = set()
        for path in sorted(dir_path.glob("*.json")):
            try:
                cfg = BotConfig.from_json(path)
            except Exception as e:
                logger.warning(
                    "[BotRegistry] skipped malformed config %s: %s",
                    path.name, e,
                )
                continue
            if cfg.bot_id in seen:
                raise ValueError(
                    f"duplicate bot_id={cfg.bot_id} (file: {path.name})"
                )
            seen.add(cfg.bot_id)
            configs.append(cfg)
        return cls(configs=configs)
