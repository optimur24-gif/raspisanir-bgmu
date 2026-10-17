"""Manual and scheduled refresh use the same atomic source operation."""

from datetime import datetime, timezone
import logging


async def refresh_saved_groups(source, profiles):
    try:
        result = await source.refresh(profiles.paths())
    except Exception as error:
        profiles.set_metadata("last_refresh_error", {
            "at": datetime.now(timezone.utc).isoformat(), "type": type(error).__name__,
        })
        raise
    profiles.set_metadata("last_refresh", result)
    profiles.set_metadata("last_refresh_error", None)
    return result


async def morning_refresh(context):
    source, profiles = context.job.data
    try:
        await refresh_saved_groups(source, profiles)
    except Exception as error:
        logging.getLogger(__name__).warning("Утреннее обновление не выполнено (%s)", type(error).__name__)
