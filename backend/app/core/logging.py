import logging

LOG_FORMAT = "ts=%(asctime)s level=%(levelname)s logger=%(name)s msg=%(message)s"


def quiet_url_loggers() -> None:
    """httpx/httpcore log full request URLs at INFO; the Slack webhook URL is a secret."""
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, force=True)
    quiet_url_loggers()
