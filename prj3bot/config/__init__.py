"""Configuration module for prj3bot."""

from prj3bot.config.loader import get_config_path, load_config
from prj3bot.config.schema import Config

__all__ = ["Config", "load_config", "get_config_path"]

