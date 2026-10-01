# Guía de Activación y Escalabilidad de Gemini API (Pay-As-You-Go)

Esta guía documenta el procedimiento oficial para escalar la API de Google Gemini desde el nivel gratuito (Free Tier) al nivel de pago por uso (**Pay-As-You-Go**), asegurando la capacidad operativa para **250+ usuarios concurrentes** en PromData.

---

## 1. Comparativa de Cuotas: Free Tier vs. Pay-As-You-Go

| Métrica | Free Tier (Nivel Gratuito) | Pay-As-You-Go (Producción) | Impacto en PromData |
|---|:---:|:---:|---|
| **RPM** (Peticiones / minuto) | 5 | **1,000 a 2,000** | Permite atender ráfagas simultáneas de decenas de usuarios por segundo. |
| **TPM** (Tokens / minuto) | 50,000 | **4,000,000** | Procesa reportes ejecutivos complejos sin truncamiento ni rechazos. |
| **RPD** (Peticiones / día) | **20 (Límite crítico)** | **ILIMITADO** | Elimina el bloqueo diario tras 20 consultas. |
| **Costo por 1M tokens** | Gratuito | **~$0.10 a $0.15 USD** | ~\$0.00015 USD por análisis completo. |

---

## 2. Procedimiento de Activación Paso a Paso

1. **Ingresar a Google AI Studio:**
   - Navega a [Google AI Studio](https://aistudio.google.com/).
   - Inicia sesión con la cuenta de Google vinculada a tu proyecto.

2. **Acceder a la sección de Facturación (Billing):**
   - En la esquina inferior izquierda o en la configuración de la clave, haz clic en **"Get API key"** o **"Plan & Billing"**.
   - Haz clic en el botón **"Set up billing"** (Configurar facturación) o **"Upgrade to Pay-as-you-go"**.

3. **Vincular Proyecto de Google Cloud (GCP):**
   - Selecciona tu cuenta de facturación existente o ingresa los datos de una tarjeta de crédito/débito.
   - Vincula el proyecto GCP (por ejemplo, `promdata-enterprise` o tu proyecto corporativo).

4. **Verificación de Cuotas Ampliadas:**
   - Una vez asociada la facturación, dirígete a la consola de Google Cloud $\rightarrow$ **APIs & Services** $\rightarrow$ **Generative Language API** $\rightarrow$ **Quotas**.
   - Confirma que la cuota de **Generate Content API Requests per minute** se haya elevado a $\ge 1,000$ RPM.

---

## 3. Gobernanza y Margen de Seguridad en PromData

PromData incorpora la variable de entorno:
```bash
GEMINI_RPM_GOVERNOR=800
```
Esta directiva establece un techo conservador del **80% de la cuota máxima permitida (1,000 RPM)**, garantizando que ráfagas intensas de tráfico nunca alcancen el límite duro de Google y evitando errores HTTP 429 (`RESOURCE_EXHAUSTED`).

Adicionalmente, las consultas analíticas pesadas (sumas, filtros, agrupaciones) se resuelven en **DuckDB-WASM local y C++**, consumiendo 0 tokens de IA.
