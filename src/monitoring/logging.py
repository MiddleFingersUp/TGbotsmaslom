"""Structured logging configuration with secrets and PII filtering."""

import logging
import sys
from typing import Any

import structlog

# Sensitive field names to redact in logs
SENSITIVE_KEYS = {
    "token",
    "secret",
    "password",
    "key",
    "api_key",
    "authorization",
    "webhook_secret",
    "phone",
    "contact_phone",
}


def censor_secrets(
    logger: Any, method_name: str, event_dict: structlog.types.EventDict
) -> structlog.types.EventDict:
    """Processor that redacts known secrets and sensitive keys."""
    for key, value in list(event_dict.items()):
        key_lower = key.lower()
        if any(secret_term in key_lower for secret_term in SENSITIVE_KEYS) and value is not None:
            event_dict[key] = "[REDACTED]"
    return event_dict


def setup_logging(log_level: str = "INFO", json_logs: bool = True) -> None:
    """Configure structlog and standard logging."""
    level = getattr(logging, log_level.upper(), logging.INFO)

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        censor_secrets,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    renderer: structlog.types.Processor
    if json_logs:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=shared_processors
        + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)


def get_logger(name: str = "tgbots") -> structlog.stdlib.BoundLogger:
    """Return a configured structured logger."""
    bound_logger: structlog.stdlib.BoundLogger = structlog.stdlib.get_logger(name)
    return bound_logger
