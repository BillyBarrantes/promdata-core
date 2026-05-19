import re
import unicodedata


def normalize_prompt_rules(text: str) -> str:
    """Normaliza texto para reglas determinísticas sin depender de acentos ni puntuación."""
    raw_text = str(text or "")
    normalized = unicodedata.normalize("NFKD", raw_text)
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    normalized = re.sub(r"[^\w\s]", " ", normalized.lower())
    return re.sub(r"\s+", " ", normalized).strip()


def detect_format_override_from_prompt(prompt_text: str) -> dict:
    """
    Detecta restricciones explícitas de formato para ESTA solicitud.
    No reemplaza el comportamiento visual por defecto; solo activa un kill switch
    temporal cuando el usuario lo ordena de forma inequívoca.
    """
    normalized = normalize_prompt_rules(prompt_text)
    if not normalized:
        return {"enabled": False}

    strict_table_markers = (
        "solo tabla",
        "solo una tabla",
        "solo en tabla",
        "unicamente tabla",
        "tabla unicamente",
        "tabla solamente",
        "solo datos",
        "solo los datos",
        "datos crudos",
        "datos en bruto",
        "raw data",
        "tabla simple",
        "tabla sencilla",
        "sin grafico",
        "sin graficos",
        "sin chart",
        "sin charts",
        "without chart",
        "without charts",
    )

    if not any(marker in normalized for marker in strict_table_markers):
        return {"enabled": False}

    return {
        "enabled": True,
        "renderer": "tabla_datos",
        "single_plan": True,
        "reason": "restriccion explicita de formato tabular",
        "translator_instruction": (
            "KILL SWITCH DE FORMATO ACTIVO SOLO PARA ESTA SOLICITUD: el usuario exige salida tabular "
            "sin graficos. NO generes triple vista automatica. Genera EXACTAMENTE 1 plan. "
            "NO reutilices memoria previa para imponer formato visual. La salida final sera SOLO TABLA."
        ),
    }
