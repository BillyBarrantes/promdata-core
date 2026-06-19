# En: backend/app/tasks/analysis_pipeline/memory_router.py
"""Knowledge/memory functions — extracted from analysis_tasks.py."""

from typing import Any
from datetime import datetime
import json
import re
import unicodedata


def get_embedding(text: str) -> list | None:
    """Genera embedding para memoria vectorial (Modelo moderno)."""
    try:
        from app.core.gemini_client import genai
        result = genai.embed_content(
            model="models/gemini-embedding-001",
            content=text,
            task_type="retrieval_document",
            title="Analysis Context"
        )
        return result['embedding']
    except Exception as e:
        return None


def guardar_insight_aprendido(supabase: Any, user_id: str, description: str, code_snippet: str, data_dna: dict) -> None:
    try:
        emb = get_embedding(description)
        if emb:
            data = {
                "user_id": user_id,
                "description": description,
                "sql_snippet": code_snippet,
                "embedding": emb,
                "created_at": datetime.now().isoformat(),
                "metadata": json.dumps(data_dna)
            }
            try:
                supabase.table('historical_insights').insert(data).execute()
            except Exception as db_e:
                pass
    except Exception as e:
        pass


def _fetch_institutional_knowledge_context(*, supabase_client: Any, user_id: str | None, query: str) -> str:
    context_block, _ = _fetch_institutional_knowledge_payload(
        supabase_client=supabase_client,
        user_id=user_id,
        query=query,
    )
    return context_block


def _fetch_institutional_knowledge_payload(*, supabase_client: Any, user_id: str | None, query: str) -> tuple[str, list[Any]]:
    normalized_query = str(query or "").strip()
    if not user_id or not normalized_query:
        return "", []

    try:
        from app.services.document_rag import resolve_user_team_id, search_knowledge_documents, build_knowledge_context_block
        from app.core.config import settings
        team_id = resolve_user_team_id(user_id=user_id, service_client=supabase_client)
        snippets = search_knowledge_documents(
            user_id=user_id,
            team_id=team_id,
            query=normalized_query,
            service_client=supabase_client,
            limit=settings.KNOWLEDGE_DEFAULT_TOP_K,
        )
        context_block = build_knowledge_context_block(snippets)
        return context_block, snippets
    except Exception as exc:
        return "", []


def _normalize_rule_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return normalized.lower().strip()


def _parse_rule_threshold(raw_value: str, scale: str | None) -> float | None:
    text = str(raw_value or "").strip().replace(" ", "")
    if not text:
        return None
    if "," in text and "." in text:
        text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        threshold = float(text)
    except ValueError:
        return None

    normalized_scale = _normalize_rule_text(scale or "")
    if normalized_scale in {"k", "mil"}:
        threshold *= 1_000
    elif normalized_scale in {"m", "mm", "millon", "millones"}:
        threshold *= 1_000_000
    return threshold


def _split_rule_sentences(text: str) -> list[str]:
    content = str(text or "").strip()
    if not content:
        return []
    return [
        sentence.strip(" -\n\t")
        for sentence in re.split(r"(?<=[\.\!\?\n])\s+", content)
        if sentence.strip()
    ]


def _extract_action_from_rule_sentence(sentence: str) -> str:
    normalized_sentence = " ".join(str(sentence or "").split())
    if not normalized_sentence:
        return ""

    action_patterns = [
        r"(?:entonces|debe|deben|debera|deberan)\s+(?P<action>.+)$",
        r"[,;:]\s*(?P<action>[^.;]+)$",
    ]
    for pattern in action_patterns:
        match = re.search(pattern, normalized_sentence, flags=re.IGNORECASE)
        if match:
            action = match.group("action").strip(" .")
            if action:
                return action[0].upper() + action[1:]
    return normalized_sentence.strip(" .")


def _extract_institutional_rules(snippets: list[Any]) -> list[dict[str, Any]]:
    if not snippets:
        return []

    rules: list[dict[str, Any]] = []
    threshold_pattern = re.compile(
        r"(?:si|cuando)\s+(?:el|la|los|las)?\s*(?P<metric>[a-zA-Z0-9áéíóúÁÉÍÓÚñÑ_/\-\s]{2,60}?)\s+"
        r"(?P<comparator>supera|supere|pasa de|pase de|excede|exceda|sobrepasa|sobrepase|rebasa|rebase|"
        r"es mayor que|sea mayor que|es menor que|sea menor que|cae por debajo de|caiga por debajo de|"
        r"baja de|baje de|sube de|suba de|>=|<=|>|<)\s*"
        r"(?P<threshold>[\d\.,]+)\s*(?P<scale>k|m|mm|mil|millones?)?",
        flags=re.IGNORECASE,
    )

    for snippet in snippets:
        snippet_content = getattr(snippet, "content", "")
        for sentence in _split_rule_sentences(snippet_content):
            match = threshold_pattern.search(sentence)
            if not match:
                continue

            threshold_value = _parse_rule_threshold(match.group("threshold"), match.group("scale"))
            if threshold_value is None:
                continue

            comparator_raw = _normalize_rule_text(match.group("comparator"))
            direction = "gt"
            if comparator_raw in {"es menor que", "sea menor que", "cae por debajo de", "caiga por debajo de", "baja de", "baje de", "<", "<="}:
                direction = "lt"

            rules.append({
                "metric": str(match.group("metric") or "").strip(),
                "direction": direction,
                "threshold": threshold_value,
                "action": _extract_action_from_rule_sentence(sentence),
                "source_sentence": sentence.strip(),
                "document_title": getattr(snippet, "document_title", "Documento institucional"),
                "document_file_name": getattr(snippet, "document_file_name", ""),
            })

    return rules


def _extract_numeric_observations(payload: Any) -> list[float]:
    import numpy as np
    observations: list[float] = []

    def _walk(value: Any) -> None:
        if isinstance(value, bool) or value is None:
            return
        if isinstance(value, (int, float)):
            observations.append(float(value))
            return
        if isinstance(value, dict):
            for key, child in value.items():
                if str(key).lower() in {"value", "total", "metric_value", "amount", "count", "stock"}:
                    _walk(child)
            return
        if isinstance(value, list):
            for item in value:
                _walk(item)

    _walk(payload)
    return [value for value in observations if np.isfinite(value)]


def _build_compliance_metric_context(*, actual_prompt: str, plan: Any) -> str:
    tokens = [str(actual_prompt or ""), str(getattr(plan, "title", "") or "")]
    intent = getattr(plan, "main_intent", None)
    if intent is not None:
        for attr in ("metric", "value_column"):
            value = getattr(intent, attr, None)
            if value:
                tokens.append(str(value))
        for value in list(getattr(intent, "metrics", None) or []):
            if value:
                tokens.append(str(value))
    return _normalize_rule_text(" ".join(tokens))


def _evaluate_institutional_compliance(*, snippets: list[Any], actual_prompt: str, plan: Any, ibis_output: dict[str, Any]) -> dict[str, Any]:
    rules = _extract_institutional_rules(snippets)
    if not rules:
        return {"matched": False}

    metric_context = _build_compliance_metric_context(actual_prompt=actual_prompt, plan=plan)
    observed_values = _extract_numeric_observations(ibis_output.get("data", []))
    if not observed_values:
        observed_values = _extract_numeric_observations(ibis_output.get("hard_facts", {}))
    if not observed_values:
        return {"matched": False, "rules_detected": len(rules)}

    best_observed = max(observed_values)
    best_match: dict[str, Any] | None = None

    for rule in rules:
        normalized_metric = _normalize_rule_text(rule.get("metric", ""))
        if normalized_metric and normalized_metric not in metric_context:
            continue

        threshold = float(rule["threshold"])
        direction = str(rule["direction"])
        matched = best_observed > threshold if direction == "gt" else best_observed < threshold
        if not matched:
            continue

        best_match = {
            "matched": True,
            "observed_value": best_observed,
            "threshold": threshold,
            "direction": direction,
            "action": str(rule["action"]),
            "rule_sentence": str(rule["source_sentence"]),
            "document_title": str(rule["document_title"]),
            "document_file_name": str(rule["document_file_name"]),
        }
        break

    if best_match:
        return best_match

    return {
        "matched": False,
        "rules_detected": len(rules),
        "observed_value": best_observed,
    }


def _force_markdown_action_block(text: str, mandated_action: str) -> str:
    content = str(text or "").strip()
    action_line = f"**Acción:** {mandated_action.strip()}"
    if not content:
        return action_line

    replacement_pattern = re.compile(
        r"\*\*Acción:\*\*.*?(?=\n\*\*|\n##|\Z)",
        flags=re.IGNORECASE | re.DOTALL,
    )
    if replacement_pattern.search(content):
        return replacement_pattern.sub(action_line, content, count=1)
    return f"{content}\n{action_line}"


def resolve_memory_for_task(supabase: Any, user_id: str | None, file_id: str, prompt: str, parent_context: str | None, dfs: dict, adn: dict) -> tuple:
    """Wrapper que orquesta memoria, glosario, reglas institucionales."""
    glossary_map = {}
    institutional_rules = []

    try:
        insight = guardar_insight_aprendido(supabase, user_id, prompt, "", adn)
    except Exception:
        pass

    memory_context = _fetch_institutional_knowledge_context(
        supabase_client=supabase, user_id=user_id, query=prompt
    )
    facts_json = _fetch_institutional_knowledge_payload(
        supabase_client=supabase, user_id=user_id, query=prompt
    )

    if parent_context:
        from app.services.analysis_memory_context import (
            apply_parent_context_to_placeholder_filters,
            build_parent_memory_context_text,
            load_parent_analysis_context,
        )
        parent_memory = load_parent_analysis_context(supabase, parent_context)
        memory_context = build_parent_memory_context_text(parent_memory, parent_context, memory_context)

    return memory_context, facts_json, glossary_map, institutional_rules
