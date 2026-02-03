"""
Structured logging configuration for Polymarket Bot.
"""
import logging
import sys
from datetime import datetime
from typing import Optional
from pythonjsonlogger import jsonlogger


class CustomJsonFormatter(jsonlogger.JsonFormatter):
    """Custom JSON formatter with additional fields."""
    
    def add_fields(self, log_record, record, message_dict):
        super().add_fields(log_record, record, message_dict)
        log_record['timestamp'] = datetime.utcnow().isoformat()
        log_record['level'] = record.levelname
        log_record['module'] = record.module
        log_record['function'] = record.funcName


def setup_logger(
    name: str = "polymarket_bot",
    level: int = logging.INFO,
    json_format: bool = False
) -> logging.Logger:
    """
    Configure and return a logger instance.
    
    Args:
        name: Logger name
        level: Logging level
        json_format: If True, output JSON logs (good for production)
    
    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    # Remove existing handlers
    logger.handlers = []
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    
    if json_format:
        formatter = CustomJsonFormatter(
            '%(timestamp)s %(level)s %(name)s %(message)s'
        )
    else:
        formatter = logging.Formatter(
            '%(asctime)s | %(levelname)-8s | %(module)s:%(funcName)s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
    
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    return logger


# Global logger instance
log = setup_logger()


class LogContext:
    """Context manager for adding context to log messages."""
    
    def __init__(self, logger: logging.Logger, **context):
        self.logger = logger
        self.context = context
        
    def info(self, msg: str, **extra):
        self.logger.info(msg, extra={**self.context, **extra})
        
    def warning(self, msg: str, **extra):
        self.logger.warning(msg, extra={**self.context, **extra})
        
    def error(self, msg: str, **extra):
        self.logger.error(msg, extra={**self.context, **extra})
        
    def debug(self, msg: str, **extra):
        self.logger.debug(msg, extra={**self.context, **extra})
