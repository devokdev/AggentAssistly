"""Message bus module for decoupled channel-agent communication."""

from prj3bot.bus.events import InboundMessage, OutboundMessage
from prj3bot.bus.queue import MessageBus

__all__ = ["MessageBus", "InboundMessage", "OutboundMessage"]

