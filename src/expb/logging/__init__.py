import logging
import sys
import structlog


class Logger:
    def __init__(
        self,
        inner_logger: structlog.BoundLogger | None = None,
    ) -> None:
        self.inner_logger = inner_logger

    def info(self, *args, **kwargs):
        if self.inner_logger is None:
            return
        self.inner_logger.info(*args, **kwargs)

    async def ainfo(self, *args, **kwargs):
        if self.inner_logger is None:
            return
        await self.inner_logger.ainfo(*args, **kwargs)

    def error(self, *args, **kwargs):
        if self.inner_logger is None:
            return
        self.inner_logger.error(*args, **kwargs)

    async def aerror(self, *args, **kwargs):
        if self.inner_logger is None:
            return
        await self.inner_logger.aerror(*args, **kwargs)

    def debug(self, *args, **kwargs):
        if self.inner_logger is None:
            return
        self.inner_logger.debug(*args, **kwargs)

    async def adebug(self, *args, **kwargs):
        if self.inner_logger is None:
            return
        await self.inner_logger.adebug(*args, **kwargs)

    def warning(self, *args, **kwargs):
        if self.inner_logger is None:
            return
        self.inner_logger.warning(*args, **kwargs)

    async def awarning(self, *args, **kwargs):
        if self.inner_logger is None:
            return
        await self.inner_logger.awarning(*args, **kwargs)

    def critical(self, *args, **kwargs):
        if self.inner_logger is None:
            return
        self.inner_logger.critical(*args, **kwargs)

    async def acritical(self, *args, **kwargs):
        if self.inner_logger is None:
            return
        await self.inner_logger.acritical(*args, **kwargs)


def setup_logging(log_level: str = "INFO") -> Logger:
    log_level = log_level.upper()
    logging.basicConfig(level=log_level, stream=sys.stdout, format="%(message)s")

    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            # structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.LogfmtRenderer(
                drop_missing=True,
                key_order=["timestamp", "level", "event"],
            ),
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    log = structlog.get_logger()
    log.info("logging configured", level=log_level)

    return Logger(log)
