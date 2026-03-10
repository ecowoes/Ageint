"""
Workday Integration Monitoring Agent - Main Entry Point
"""
import uvicorn
from app.api.app import create_app
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def main():
    logger.info(
        "Starting Workday Integration Monitoring Agent",
        extra={"host": settings.APP_HOST, "port": settings.APP_PORT},
    )
    app = create_app()
    uvicorn.run(
        app,
        host=settings.APP_HOST,
        port=settings.APP_PORT,
        log_level=settings.APP_LOG_LEVEL.lower(),
        reload=settings.APP_DEBUG,
    )


if __name__ == "__main__":
    main()
