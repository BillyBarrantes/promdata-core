import test_format_override_guard as format_override_guard
import test_observability_transport_guard as observability_transport_guard
import test_phase6_cloud_import_contract as phase6_cloud_import_contract
import test_phase6_cloud_listing_contract as phase6_cloud_listing_contract
import test_phase6_cloud_sync_job_contract as phase6_cloud_sync_job_contract
import test_phase6_connector_contract as phase6_connector_contract
import test_phase6_enterprise_telemetry_contract as phase6_enterprise_telemetry_contract
import test_phase6_oauth_flow_contract as phase6_oauth_flow_contract
import test_phase6_watchdog_contract as phase6_watchdog_contract
import test_phase1_explainability_contract as phase1_explainability_contract
import test_phase2_advanced_chart_contract as phase2_advanced_chart_contract
import test_phase2_visual_data_governance_contract as phase2_visual_data_governance_contract
import test_phase2_memory_isolation_contract as phase2_memory_isolation_contract
import test_phase2_visual_governance_contract as phase2_visual_governance_contract
import test_phase7_document_rag_contract as phase7_document_rag_contract


def test_phase6_connector_contract() -> None:
    phase6_connector_contract.run_assertions()


def test_phase6_oauth_flow_contract() -> None:
    phase6_oauth_flow_contract.run()


def test_phase6_cloud_listing_contract() -> None:
    phase6_cloud_listing_contract.run()


def test_phase6_cloud_import_contract() -> None:
    phase6_cloud_import_contract.run()


def test_phase6_cloud_sync_job_contract() -> None:
    phase6_cloud_sync_job_contract.run()


def test_phase6_watchdog_contract() -> None:
    phase6_watchdog_contract.run()


def test_phase6_enterprise_telemetry_contract() -> None:
    phase6_enterprise_telemetry_contract.run_assertions()


def test_phase7_document_rag_contract() -> None:
    phase7_document_rag_contract.run()


def test_phase1_explainability_contract() -> None:
    phase1_explainability_contract.run()


def test_phase2_visual_governance_contract() -> None:
    phase2_visual_governance_contract.run()


def test_phase2_memory_isolation_contract() -> None:
    phase2_memory_isolation_contract.run()


def test_phase2_advanced_chart_contract() -> None:
    phase2_advanced_chart_contract.run()


def test_phase2_visual_data_governance_contract() -> None:
    phase2_visual_data_governance_contract.run()


def test_format_override_guard_contract() -> None:
    format_override_guard.run_assertions()


def test_observability_transport_guard_contract() -> None:
    observability_transport_guard.run_assertions()
