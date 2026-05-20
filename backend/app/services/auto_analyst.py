import google.generativeai as genai
import json
import warnings
import pandas as pd
import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.linear_model import LinearRegression
from app.core.config import settings

genai.configure(api_key=settings.GEMINI_API_KEY)

warnings.filterwarnings("ignore")

class AutoAnalyst:
    @staticmethod
    def analyze(df: pd.DataFrame, top_n: int = 5, currency_meta: dict = {}) -> dict:
        # A. INFERENCIA DE CONTEXTO (DYNAMIC DOMAIN)
        context = AutoAnalyst._analyze_data_context(df, currency_meta)
        intent = context.get('strategic_intent', 'General Analysis')
        rec_chart = context.get('recommended_chart', 'bar')
        
        currency_symbol = currency_meta.get('symbol', '')
        currency_suffix = f" ({currency_symbol})" if currency_symbol else ""

        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
        date_cols = df.select_dtypes(include=['datetime', 'datetimetz']).columns.tolist()
        cat_cols = df.select_dtypes(include=['object', 'category', 'string']).columns.tolist()

        facts = {
            "contexto": context,
            "dimensiones": {"filas": len(df), "columnas": len(df.columns)},
            "anomalias": [],
            "tendencias": [],
            "pareto": [],     # Puede mutar a Treemap/Pie
            "distribuciones": [],
            "analisis_avanzado": [] # Heatmap, Scatter, Waterfall
        }

        # 1. Pareto / Jerarquía (Treemap vs Bar vs Pie)
        if numeric_cols and cat_cols:
            if rec_chart == 'treemap':
                facts["pareto"] = AutoAnalyst._calculate_treemap(df, cat_cols, numeric_cols, context, currency_suffix)
            else:
                facts["pareto"] = AutoAnalyst._calculate_pareto(df, cat_cols, numeric_cols, context, top_n, currency_suffix)

        # 2. Tendencias (Evolución Temporal)
        if date_cols and numeric_cols:
            facts["tendencias"] = AutoAnalyst._analyze_trends(df, date_cols[0], numeric_cols, currency_suffix)

        # 3. Distribuciones (Categorías Clave)
        if cat_cols:
            facts["distribuciones"] = AutoAnalyst._analyze_distributions(df, cat_cols, numeric_cols)

        # 4. Anomalías
        if numeric_cols and len(df) > 50:
            facts["anomalias"] = AutoAnalyst._detect_anomalies(df, numeric_cols)

        # 5. Análisis Avanzado (Waterfall, Heatmap, Scatter)
        # Solo si el LLM lo sugirió y tenemos los datos para ello
        if rec_chart == 'waterfall':
             facts["analisis_avanzado"] = AutoAnalyst._analyze_financial_flow(df, cat_cols, numeric_cols, currency_suffix)
        elif rec_chart == 'heatmap' and len(cat_cols) >= 2:
             facts["analisis_avanzado"] = AutoAnalyst._analyze_heatmap(df, cat_cols, numeric_cols)
        elif rec_chart == 'scatter' and len(numeric_cols) >= 2:
             facts["analisis_avanzado"] = AutoAnalyst._analyze_scatter(df, numeric_cols)

        # 6. Diagnóstico de Riesgo
        if date_cols and numeric_cols:
             facts["diagnosticos"] = AutoAnalyst._diagnose_risk(df, date_cols[0], numeric_cols, context)

        return facts

    @staticmethod
    def _analyze_data_context(df: pd.DataFrame, currency_meta: dict = {}) -> dict:
        """
        SEMANTIC PROFILER (Tabula Rasa Engine).
        Mira 3 filas y decide si es Retail, Salud, Banca, etc.
        """
        try:
            # Muestra pequeña (Headers + 3 rows) convertida a CSV string
            sample_csv = df.head(3).to_csv(index=False)
            
            # Instrucción de Moneda (Si el DataEngine la detectó)
            currency_instruction = ""
            if currency_meta and currency_meta.get('symbol'):
                currency_instruction = f"IMPORTANT: The file uses the currency '{currency_meta['symbol']}' ({currency_meta['code']}). Use this symbol in your text."
            
            model = genai.GenerativeModel(
                model_name=settings.AI_MODEL_NAME,  # Centralizado desde config.py
                generation_config={"response_mime_type": "application/json", "temperature": 0.0}
            )

            prompt = f"""
            ACT AS A DATA STRATEGIST SEER.
            Analyze this data sample.
            {currency_instruction}
            
            YOU HAVE ACCESS TO ADVANCED VISUALIZATIONS:
            - 'waterfall': For Financial Flow, P&L, Cost Evolution.
            - 'heatmap': For Density, Geographic Distribution, Correlation Matrix.
            - 'scatter': For Correlation (2 numeric vars).
            - 'treemap': For Hierarchies.
            - 'bar'/'line': Safe defaults.
            
            DATA SAMPLE:
            {sample_csv}

            RETURN JSON format:
            {{
                "detected_domain": "e.g. Healthcare, Retail, Fintech",
                "strategic_intent": "e.g. Correlation, Financial Flow, Hierarchy, Trend",
                "recommended_chart": "waterfall, heatmap, scatter, treemap, bar, or line",
                "detected_role": "e.g. Chief Medical Officer, Store Manager, CFO, Logistics Manager, HR Director",
                "quantity_term": "e.g. Patients, Units, Transactions, Pallets, Employees",
                "risk_concept": "e.g. High Stay, Overstock, Fraud, Dead Stock, Churn",
                "entity_name": "e.g. Patient, Product, Account, SKU, Employee"
            }}
            """
            
            response = model.generate_content(prompt)
            data = json.loads(response.text)
            data['recommended_chart'] = data.get('recommended_chart', 'bar').lower()
            return data
        except Exception:
            # Fallback seguro por si Gemini falla o no hay internet
            return {
                "detected_domain": "General Business",
                "detected_role": "Analyst",
                "quantity_term": "Unidades/Valor",
                "risk_concept": "Estancamiento",
                "entity_name": "Ítem"
            }

    @staticmethod
    def _calculate_pareto(df, cat_cols, num_cols, context, top_n=5, currency_suffix=""):
        # ... (Tu lógica existente de Pareto, asegúrate de mantener chart_data) ...
        # Lógica simplificada para brevedad, pero MANTENIENDO chart_data
        target_cat = next((c for c in cat_cols if 1 < df[c].nunique() < 500), cat_cols[0])
        target_num = max(num_cols, key=lambda c: df[c].var()) if num_cols else num_cols[0]
        
        # --- TERMINOLOGÍA DINÁMICA (Context Aware) ---
        # Si Gemini dijo que contamos "Pacientes", usamos "Pacientes".
        metric_label = "Valor"
        
        # 1. Intento por Contexto (Prioridad)
        qty_term = context.get('quantity_term', 'Unidades')
        
        # 2. Heurística (Backup)
        if any(x in target_num.lower() for x in ['unid', 'qty', 'cant', 'stock', 'pzas', 'count']):
             metric_label = qty_term
        
        # Inyección de Moneda si aplica
        if currency_suffix and "unid" not in target_num.lower():
            metric_label += currency_suffix
        # --------------------------------------------------------

        grouped = df.groupby(target_cat)[target_num].sum().sort_values(ascending=False)
        
        # --- OBEDIENCIA NUMÉRICA (Top N real) ---
        top_data = [{"name": str(idx), "value": float(val)} for idx, val in grouped.head(top_n).items()]
        # ----------------------------------------
        
        return [{
            "tipo": "pareto",
            "dimension": target_cat,
            "metrica": f"{target_num} ({metric_label})",
            "chart_data": top_data
        }]

    @staticmethod
    def _analyze_trends(df, date_col, num_cols, currency_suffix=""):
        trends = []
        target_num = num_cols[0]
        
        # Agrupación por Mes para gráfico limpio
        df_temp = df.copy()
        df_temp[date_col] = pd.to_datetime(df_temp[date_col], errors='coerce')
        df_temp = df_temp.dropna(subset=[date_col]).sort_values(date_col)
        
        # Resample mensual para el gráfico
        monthly = df_temp.set_index(date_col)[target_num].resample('ME').sum().fillna(0)
        
        # Datos para el gráfico de LÍNEA
        chart_data = [{"name": str(idx.strftime('%Y-%m')), "value": float(val)} for idx, val in monthly.items()]

        trends.append({
            "tipo": "tendencia",
            "dimension": date_col,
            "metrica": target_num,
            "mensaje": f"Análisis temporal sobre {date_col}",
            "chart_data": chart_data # DATOS LISTOS PARA ECHARTS
        })
        return trends

    @staticmethod
    def _analyze_distributions(df, cat_cols, num_cols):
        dists = []
        target_num = num_cols[0] if num_cols else None
        
        # Buscamos columnas categóricas "pequeñas" (ej: Almacén, Estado) ideales para Pie/Barras
        small_cats = [c for c in cat_cols if 2 <= df[c].nunique() <= 15]
        
        for cat in small_cats[:2]: # Analizamos máximo 2 para no saturar
            if target_num:
                grouped = df.groupby(cat)[target_num].sum().sort_values(ascending=False)
            else:
                grouped = df[cat].value_counts() # Si no hay números, contamos filas

            chart_data = [{"name": str(idx), "value": float(val)} for idx, val in grouped.items()]
            
            dists.append({
                "tipo": "distribucion",
                "dimension": cat,
                "metrica": target_num or "Conteo",
                "chart_data": chart_data
            })
        return dists

    @staticmethod
    def _detect_anomalies(df, num_cols):
        # Mantenemos lógica de anomalías (sin cambios gráficos por ahora)
        data = df[num_cols].fillna(0)
        model = IsolationForest(contamination=0.01, random_state=42)
        df['anomaly'] = model.fit_predict(data)
        anomalies = df[df['anomaly'] == -1]
        
        if not anomalies.empty:
            return [{"cantidad": len(anomalies), "mensaje": "Anomalías detectadas"}]
        return []

    @staticmethod
    def _diagnose_risk(df, date_col, num_cols, context):
        """
        Pilar 3 Big Data: Analítica Diagnóstica (Context Aware).
        Detecta 'Riesgo de Estancamiento' adaptado al dominio.
        """
        diagnostics = []
        
        # 1. Identificar columna de Activo (Asset)
        # Buscamos 'stock', 'saldo', 'inventario', 'on_hand', pero también 'casos', 'headcount'
        # Si no encontramos keywords de stock clásicas, confiamos en la numérica principal
        risk_concept = context.get('risk_concept', 'Estancamiento')
        
        stock_col = next((c for c in num_cols if any(x in c.lower() for x in ['stock', 'saldo', 'inventario', 'on_hand', 'active', 'pendientes'])), None)
        
        if not stock_col: 
            return []

        # 2. Calcular Variación Mensual y Stock Actual
        try:
            df_curr = df.copy()
            df_curr[date_col] = pd.to_datetime(df_curr[date_col], errors='coerce')
            df_curr = df_curr.set_index(date_col).resample('ME')[stock_col].sum().fillna(0)
            
            if len(df_curr) < 2: return []

            current_stock = df_curr.iloc[-1]
            prev_stock = df_curr.iloc[-2]
            variation = current_stock - prev_stock
            avg_stock = df_curr.mean()

            # 3. Regla de Obsolescencia / Estancamiento
            # Si el Nivel actual es alto (> promedio) Y la variación es mínima (< 5%)
            if current_stock > avg_stock and abs(variation) < (0.05 * current_stock):
                diagnostics.append({
                    "tipo": "riesgo_estancamiento",
                    "mensaje": f"⚠️ ALERTA {context.get('detected_domain', 'NEGOCIO').upper()}: {risk_concept} detectado en '{stock_col}'.",
                    "detalle": f"El nivel actual de {context.get('quantity_term', 'unidades')} ({current_stock:,.0f}) es alto, pero la variación es casi nula ({variation:,.0f}). Posible {risk_concept.lower()}."
                })
                
        except Exception as e:
            pass # Diagnóstico es "nice to have", no debe romper el flujo

        return diagnostics

    @staticmethod
    def _calculate_treemap(df, cat_cols, num_cols, context, currency_suffix=""):
        # Lógica para Treemap (Alternativa a Pareto)
        target_cat = next((c for c in cat_cols if 1 < df[c].nunique() < 50), cat_cols[0])
        target_num = max(num_cols, key=lambda c: df[c].var()) if num_cols else num_cols[0]
        
        grouped = df.groupby(target_cat)[target_num].sum().sort_values(ascending=False).head(20) # Top 20 para treemap
        data = [{"name": str(idx), "value": float(val)} for idx, val in grouped.items()]
        
        return [{
            "tipo": "treemap",
            "title": f"Jerarquía de {target_num}{currency_suffix}",
            "chart_data": data
        }]

    @staticmethod
    def _analyze_financial_flow(df, cat_cols, num_cols, currency_suffix=""):
        # Waterfall
        # Intentamos encontrar una columna de "Concepto" y una de "Valor"
        # Heurística: Columna categórica con más de 3 valores únicos pero menos de 20
        concept_col = next((c for c in cat_cols if 3 <= df[c].nunique() <= 20), None)
        val_col = num_cols[0] if num_cols else None

        if not concept_col or not val_col: return []

        # Agrupar y sumar
        grouped = df.groupby(concept_col)[val_col].sum()
        # En Waterfall financiero, el orden importa. Aquí no podemos adivinar el orden P&L, 
        # así que ordenamos por valor absoluto o mantenemos original si el DF traía orden.
        # Asumimos que el usuario subió los datos en orden si es un reporte financiero.
        
        # Convert to list preserving extraction order roughly
        data = [{"name": str(idx), "value": float(val)} for idx, val in grouped.items()]
        
        return [{
            "tipo": "waterfall",
            "title": f"Flujo Financiero: {val_col}{currency_suffix}",
            "chart_data": data
        }]

    @staticmethod
    def _analyze_heatmap(df, cat_cols, num_cols):
        # Heatmap requiere 2 cat + 1 num
        if len(cat_cols) < 2 or not num_cols: return []
        
        x_col = cat_cols[0]
        y_col = cat_cols[1]
        val_col = num_cols[0]
        
        # Pivot básico para obtener los datos
        # Data format: list of dicts or list of lists
        # ChartFactory espera: list of dicts for conversion
        # O podemos devolver raw rows
        
        # Agrupamos por X, Y
        grouped = df.groupby([x_col, y_col])[val_col].sum().reset_index()
        
        # Limit rows to avoid crash
        if len(grouped) > 200: grouped = grouped.head(200)
        
        # Chart factory espera una lista para convertir a DF.
        return [{
            "tipo": "heatmap",
            "title": f"Mapa de Calor: {val_col} por {x_col} y {y_col}",
            "chart_data": grouped.to_dict(orient='records'),
            "x_label": x_col,
            "y_label": y_col
        }]

    @staticmethod
    def _analyze_scatter(df, num_cols):
        # Scatter requiere 2 num
        if len(num_cols) < 2: return []
        
        x_col = num_cols[0]
        y_col = num_cols[1]
        
        # Sample para no explotar el gráfico
        sample = df[[x_col, y_col]].fillna(0)
        if len(sample) > 500: sample = sample.sample(500)
        
        # ChartFactory espera [[x, y], ...] o list of dicts
        # Devolvemos list of lists para eficiencia
        data = sample.values.tolist()
        
        return [{
            "tipo": "scatter",
            "title": f"Correlación: {x_col} vs {y_col}",
            "chart_data": data,
            "x_label": x_col,
            "y_label": y_col
        }]