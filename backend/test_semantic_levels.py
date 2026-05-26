from app.services.semantic_translator import SemanticTranslator
from app.core.config import settings
import json
from app.core.gemini_client import genai

genai.configure(api_key=settings.GEMINI_API_KEY)

def test_levels():
    columns = ["categoria", "ventas", "productos"]
    glossary = "Category, Sales, Product Name"
    topology = "categoria: Cat(5), ventas: Num, productos: Cat(100)"

    print("--- 🧪 TEST DE NIVELES SEMÁNTICOS ---")

    # Nivel 1: Básico
    prompt_basic = "Filtra por ventas mayores a 100 y muestrame las ventas por categoría"
    print(f"\n📝 Prompt Básico: '{prompt_basic}'")
    plan_basic = SemanticTranslator.translate(prompt_basic, columns, glossary, topology)
    if plan_basic:
        print(f"   👉 Resultado: {plan_basic.main_intent.visual_protocol.value}")
        print(f"   🧠 Rationale: {plan_basic.main_intent.rationale}")

    # Nivel 2: Avanzado (Trigger Keyword)
    prompt_advanced = "Analiza la densidad de mis productos por categoría"
    print(f"\n📝 Prompt Avanzado (Densidad): '{prompt_advanced}'")
    plan_advanced = SemanticTranslator.translate(prompt_advanced, columns, glossary, topology)
    if plan_advanced:
        print(f"   👉 Resultado: {plan_advanced.main_intent.visual_protocol.value}")

    # Nivel 3: Variabilidad (Boxplot)
    prompt_box = "Analiza la variabilidad de precios por categoría"
    print(f"\n📝 Prompt Boxplot: '{prompt_box}'")
    plan_box = SemanticTranslator.translate(prompt_box, columns, glossary, topology)
    if plan_box:
        print(f"   👉 Resultado: {plan_box.main_intent.visual_protocol.value}")

    # Nivel 4: Conversión (Funnel)
    prompt_funnel = "Analiza la conversión del proceso de leads a ventas"
    print(f"\n📝 Prompt Funnel: '{prompt_funnel}'")
    plan_funnel = SemanticTranslator.translate(prompt_funnel, columns, glossary, topology)
    if plan_funnel:
        print(f"   👉 Resultado: {plan_funnel.main_intent.visual_protocol.value}")

if __name__ == "__main__":
    test_levels()
