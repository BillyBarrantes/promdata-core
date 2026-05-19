import json
import logging
from datetime import datetime, timezone
from typing import Any


_LOGGER = logging.getLogger("promdata.structured")

if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO, format="%(message)s")


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(inner_value) for key, inner_value in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def emit_structured_log(event: str, level: str = "info", **fields: Any) -> None:
    """
    Logger estructurado aditivo para observabilidad enterprise.
    No reemplaza los prints existentes; los complementa con eventos JSON estables.
    """
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **{key: _json_safe(value) for key, value in fields.items()},
    }

    log_method = getattr(_LOGGER, level.lower(), _LOGGER.info)
    log_method(json.dumps(payload, ensure_ascii=False, sort_keys=True))
