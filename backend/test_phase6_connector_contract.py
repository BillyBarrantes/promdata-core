from app.services.cloud_connectors import (
    get_cloud_connector_catalog,
    get_watchdog_runtime_status,
)


def run_assertions() -> None:
    providers = get_cloud_connector_catalog()
    provider_ids = [provider["id"] for provider in providers]

    assert provider_ids == ["google_drive", "onedrive"], (
        "Fase 6 debe exponer exactamente Google Drive y OneDrive en el catálogo base"
    )

    for provider in providers:
        assert provider["category"] == "cloud_storage", "El contrato debe mantener categoría estable"
        assert provider["auth_flow"] == "oauth2", "Los conectores cloud deben declarar OAuth2"
        assert provider["auth_start_path"].startswith("/api/v1/auth/"), "Start path OAuth inválido"
        assert provider["auth_callback_path"].startswith("/api/v1/auth/"), "Callback path OAuth inválido"
        assert provider["status"] in {"configured", "config_pending"}, "Estado inválido en catálogo"
        assert provider["watchdog_mode"] in {"webhook", "polling"}, "Modo watchdog inválido"
        assert isinstance(provider["capabilities"]["can_import"], bool), "Capacidad can_import debe ser booleana"
        assert isinstance(provider["capabilities"]["can_watch"], bool), "Capacidad can_watch debe ser booleana"

    watchdog = get_watchdog_runtime_status()
    assert isinstance(watchdog["enabled"], bool), "Watchdog.enabled debe ser booleano"
    assert isinstance(watchdog["poll_interval_seconds"], int), "Poll interval debe ser entero"
    assert watchdog["configured_provider_count"] == len(watchdog["configured_providers"]), (
        "El contador de providers configurados debe coincidir con el detalle"
    )
    assert watchdog["watchdog_provider_count"] == len(watchdog["watchdog_providers"]), (
        "El contador de watchdog providers debe coincidir con el detalle"
    )


if __name__ == "__main__":
    run_assertions()
    print("OK: phase6 connector contract")
