from app.core.prompt_format_override import detect_format_override_from_prompt


def run_assertions() -> None:
    override_prompt = "Muéstrame solo una tabla simple con los nombres de los 2 almacenes principales, sin gráficos"
    result = detect_format_override_from_prompt(override_prompt)
    assert result["enabled"] is True, "Debe activar override tabular cuando el prompt lo ordena"
    assert result["renderer"] == "tabla_datos", "El override debe enrutar a tabla_datos"
    assert result["single_plan"] is True, "El override debe forzar un solo plan"

    recovery_prompt = "Ahora muéstrame un gráfico de barras de stock por almacén"
    result = detect_format_override_from_prompt(recovery_prompt)
    assert result["enabled"] is False, "El override no debe contaminar el siguiente prompt"

    default_prompt = "Top 2 almacenes principales"
    result = detect_format_override_from_prompt(default_prompt)
    assert result["enabled"] is False, "El instinto visual por defecto no debe alterarse"

    raw_data_prompt = "Quiero datos crudos del stock por almacén, sin gráficos"
    result = detect_format_override_from_prompt(raw_data_prompt)
    assert result["enabled"] is True, "La variante 'datos crudos' también debe activar override"


if __name__ == "__main__":
    run_assertions()
    print("OK: format override guard")
