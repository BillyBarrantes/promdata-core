from app.services.semantic_translator import SemanticTranslator


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run() -> None:
    _assert(
        SemanticTranslator.evaluate_continuity(
            "Quiero un heatmap del stock disponible por fecha y tipo de almacén.",
            "Quiero un embudo con las ubicaciones que concentran más stock disponible.",
        ) is False,
        "Un cambio explícito de visual no debe heredar memoria del gráfico anterior",
    )

    _assert(
        SemanticTranslator.evaluate_continuity(
            "Mantén este mismo análisis, pero cambia el gráfico actual a treemap sin perder filtros ni métrica.",
            "Quiero un gráfico de barras del stock disponible por ubicación.",
        ) is True,
        "Un reemplazo explícito del visual debe conservar la continuidad analítica",
    )

    _assert(
        SemanticTranslator.evaluate_continuity(
            "heatmap",
            "Quiero un gráfico de barras del stock disponible por ubicación.",
        ) is False,
        "Un prompt corto que solo pide un nuevo visual no debe tratarse como drill-down",
    )

    _assert(
        SemanticTranslator.is_visual_replacement_request(
            "Mantén este mismo análisis, pero cambia el gráfico actual a heatmap."
        ) is True,
        "Debe detectar instrucciones explícitas de reemplazo visual",
    )

    print("OK: phase2 memory isolation contract")


if __name__ == "__main__":
    run()
