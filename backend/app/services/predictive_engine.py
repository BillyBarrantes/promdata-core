import pandas as pd
import numpy as np
import logging
from typing import Dict, List, Optional, Union
import warnings

# Suppress statsmodels warnings about frequency/index
warnings.filterwarnings("ignore")

# Intentamos importar librerías científicas, con fallback suave.
try:
    from statsmodels.tsa.holtwinters import ExponentialSmoothing
    from sklearn.ensemble import IsolationForest
except ImportError:
    ExponentialSmoothing = None
    IsolationForest = None

logger = logging.getLogger(__name__)

class PredictiveEngine:
    """
    Motor de Inteligencia Predictiva (Fase 2).
    Provee capacidades de Forecasting (Series de Tiempo) y Detección de Anomalías.
    Diseñado para fallar suavemente (Soft Fail) si las librerías no están presentes o los datos son insuficientes.
    """

    @staticmethod
    def is_available() -> bool:
        """Verifica si las librerías necesarias (statsmodels, sklearn) están instaladas."""
        return ExponentialSmoothing is not None and IsolationForest is not None

    @staticmethod
    def forecast_series(
        df: pd.DataFrame, 
        date_col: str, 
        value_col: str, 
        periods: int = 3, 
        frequency: str = 'M',
        aggregation_method: str = 'sum'
    ) -> List[Dict]:
        """
        Genera un pronóstico simple usando Holt-Winters (Exponential Smoothing).
        Retorna una lista de diccionarios con la serie original + pronóstico.
        
        Args:
            df (pd.DataFrame): DataFrame con datos históricos.
            date_col (str): Nombre de la columna de fecha.
            value_col (str): Nombre de la columna de valor numérico.
            periods (int): Número de periodos a pronosticar (por defecto 3 meses/semanas).
            frequency (str): Frecuencia de la serie ('M' mensual, 'W' semanal, 'D' diaria).
            aggregation_method (str): 'sum' (Ventas/Flujo), 'last' (Stock/Balance), 'mean' (Promedios).
            
        Returns:
            List[Dict]: Lista de objetos {date, value, type='history'|'forecast', lower_ci, upper_ci}.
        """
        if not PredictiveEngine.is_available():
            logger.warning("PredictiveEngine: statsmodels/sklearn no instalados.")
            return []

        try:
            # 1. Preparación de Datos
            if df.empty or date_col not in df.columns or value_col not in df.columns:
                return []

            pdf = df.copy()
            # Asegurar datetime
            pdf[date_col] = pd.to_datetime(pdf[date_col])
            # Ordenar y setear índice
            pdf = pdf.sort_values(date_col).set_index(date_col)
            
            # 2. Agregación Espacial (Suma de Todas las Filas en una Misma Fecha)
            # [SAFE LOGIC REVISION]
            # El archivo del usuario es "Transactional" (Filas aditivas por lotes/ubicaciones).
            # La "Naive Sum" (4.4M) es la correcta.
            # La lógica anterior "Last per SKU" (600k) eliminaba datos válidos.
            # Volvemos a la suma diaria simple, PERO mantenemos la detección de fecha inteligente.
            daily_series = pdf.groupby(date_col)[value_col].sum()
            
            # 3. Re-Muestreo Temporal (Flow vs Balance)
            # [SMART FIX] Detección de Patrón de Fecha (Inicio vs Fin de Mes)
            # Si el archivo original usa días > 20, asumimos 'M' (Month End).
            # Si usa días < 5 (ej: 01), asumimos 'MS' (Month Start).
            if frequency == 'M':
                sample_days = pdf.index.day.unique()[:5] # Muestra de días
                avg_day = sum(sample_days) / len(sample_days) if len(sample_days) > 0 else 1
                
                if avg_day > 20:
                    resample_freq = 'M' # Fin de Mes (30/31)
                else:
                    resample_freq = 'MS' # Inicio de Mes (01)
            else:
                resample_freq = frequency

            resampler = daily_series.resample(resample_freq)
            
            # [CRITICAL DECISION]
            # Si el usuario pide explícitamente 'last' (Stock), lo respetamos SOLO si NO destruye los datos.
            # Pero para este caso "Transactional", SUM es lo correcto incluso para Stock.
            # La asignación de 'aggregation_method' en analysis_tasks ahora prioriza 'sum' por seguridad.
            if aggregation_method == 'last':
                series = resampler.last().fillna(method='ffill')
            elif aggregation_method == 'mean':
                series = resampler.mean().fillna(method='ffill')
            else: 
                # Flujo Default y Stock Transactional
                series = resampler.sum().fillna(0)
            
            # Validar longitud mínima
            if len(series) < 4:
                return []

            # 4. Modelo (Holt-Winters con Tendencia Aditiva)
            seasonal_periods = 12 if frequency == 'M' else 4 # Mensual o Trimestral assumption
            
            if len(series) >= 2 * seasonal_periods:
                model = ExponentialSmoothing(
                    series, 
                    trend='add', 
                    seasonal='add', 
                    seasonal_periods=seasonal_periods,
                    initialization_method="estimated"
                ).fit()
            else:
                model = ExponentialSmoothing(
                    series, 
                    trend='add', 
                    seasonal=None,
                    initialization_method="estimated"
                ).fit()

            # 5. Pronóstico
            forecast = model.forecast(periods)
            
            # 6. Formateo de Salida
            output = []
            
            # Histórico
            for date, val in series.items():
                output.append({
                    "date": date.strftime("%Y-%m-%d"),
                    "value": round(val, 2),
                    "type": "history"
                })
                
            # Pronóstico
            for i, (date, val) in enumerate(forecast.items()):
                 uncertainty = 0.05 * (i + 1)
                 output.append({
                    "date": date.strftime("%Y-%m-%d"),
                    "value": round(val, 2),
                    "type": "forecast",
                    "lower_ci": round(val * (1 - uncertainty), 2),
                    "upper_ci": round(val * (1 + uncertainty), 2)
                })
                
            return output

        except Exception as e:
            logger.error(f"PredictiveEngine Error en forecast_series: {str(e)}")
            # En caso de error, no rompemos el flujo, retornamos vacío
            return []

    @staticmethod
    def detect_anomalies(df: pd.DataFrame, value_col: str, contamination: float = 0.05) -> pd.DataFrame:
        """
        Detecta anomalías en una columna numérica usando Isolation Forest.
        Retorna el DataFrame original con una columna extra 'is_anomaly' (bool).
        """
        if not PredictiveEngine.is_available() or df.empty or value_col not in df.columns:
            return df

        try:
            pdf = df.copy()
            # Limpieza básica: rellenar nulos con 0 o media
            data_to_fit = pdf[[value_col]].fillna(0)
            
            if len(data_to_fit) < 5:
                # Muy pocos datos para anomalías confiables
                return df
            
            # Isolation Forest
            iso = IsolationForest(contamination=contamination, random_state=42)
            preds = iso.fit_predict(data_to_fit) # 1 = normal, -1 = anomalía
            
            pdf['is_anomaly'] = preds == -1
            pdf['anomaly_score'] = iso.decision_function(data_to_fit)
            
            return pdf

        except Exception as e:
            logger.error(f"PredictiveEngine Error en detect_anomalies: {str(e)}")
            return df

    @staticmethod
    def generate_recommendations(hard_facts: dict, context: str = "", polarity: str = "neutral") -> list:
        """
        🎯 [FASE 3] Motor Prescriptivo.
        Analiza los hard_facts de cualquier análisis y genera recomendaciones accionables.
        [FASE 3C] Ahora acepta 'context' (ej: título del análisis) para recomendaciones temáticas.
        [FASE 3D] Ahora acepta 'polarity' (favorable/unfavorable/neutral) para interpretar tendencias correctamente.
        Retorna una lista de strings con insights prioritizados.
        """
        if not hard_facts or not isinstance(hard_facts, dict):
            return []
        
        recommendations = []
        # [FASE 3C] Prefijo temático para contextualizar
        ctx_prefix = f"[{context}] " if context else ""
        
        # 1. TENDENCIA — [FASE 3D] Interpretación según polaridad
        trend = hard_facts.get('trend', '')
        growth = hard_facts.get('overall_growth_pct', 0)
        
        if polarity == "unfavorable":
            # Polaridad DESFAVORABLE: bajar es bueno, subir es malo
            if trend == 'Decreciente' and growth < -10:
                recommendations.append(
                    f"{ctx_prefix}✅ Reducción significativa ({growth:.1f}%). "
                    "La operación está logrando disminuir esta métrica. Continuar con las estrategias actuales de control."
                )
            elif trend == 'Decreciente':
                recommendations.append(
                    f"{ctx_prefix}✅ Tendencia a la baja ({growth:.1f}%). "
                    "Resultado positivo. Considerar acciones adicionales para acelerar la reducción."
                )
            elif trend == 'Creciente' and growth > 10:
                recommendations.append(
                    f"{ctx_prefix}⚠️ Aumento preocupante ({growth:.1f}%). "
                    "Investigar causas y activar planes de contención: promociones de liquidación, controles de calidad, alertas tempranas."
                )
            elif trend == 'Creciente':
                recommendations.append(
                    f"{ctx_prefix}🟡 Tendencia al alza ({growth:.1f}%). Monitorear de cerca para evitar acumulación."
                )
        else:
            # Polaridad FAVORABLE o NEUTRAL: subir es bueno (o neutro), bajar es preocupante
            if trend == 'Decreciente' and growth < -10:
                recommendations.append(
                    f"{ctx_prefix}⚠️ Tendencia negativa sostenida ({growth:.1f}%). "
                    "Investigar causas raíz: cambios en demanda, estacionalidad o pérdida de clientes."
                )
            elif trend == 'Decreciente':
                recommendations.append(
                    f"{ctx_prefix}📉 Tendencia ligeramente decreciente ({growth:.1f}%). Monitorear en los próximos periodos."
                )
        
        # 2. OUTLIERS DETECTADOS
        total_outliers = hard_facts.get('total_outliers', 0)
        if total_outliers > 5:
            recommendations.append(
                f"{ctx_prefix}🔴 Se detectaron {total_outliers} valores atípicos. "
                "Revisar si corresponden a errores de carga, promociones especiales o eventos extraordinarios."
            )
        elif total_outliers > 0:
            recommendations.append(
                f"{ctx_prefix}🟡 {total_outliers} valor(es) atípico(s) detectado(s). Verificar integridad de datos."
            )
        
        # 3. YoY NEGATIVO
        yoy_avg = hard_facts.get('yoy_avg_pct', None)
        if yoy_avg is not None and yoy_avg < -5:
            recommendations.append(
                f"{ctx_prefix}📉 Crecimiento interanual promedio negativo ({yoy_avg:.1f}%). "
                "Evaluar cambios en estrategia comercial o factores macroeconómicos."
            )
        
        # 4. ALTA VOLATILIDAD (Peak vs Trough)
        peak = hard_facts.get('peak_value', 0)
        trough = hard_facts.get('trough_value', 0)
        if trough > 0 and peak > 2.5 * trough:
            ratio = peak / trough
            recommendations.append(
                f"{ctx_prefix}📊 Alta volatilidad detectada (Pico/Valle = {ratio:.1f}x). "
                "Considerar estrategias de estabilización: inventario de seguridad, diversificación."
            )
        
        # 5. DATASET PEQUEÑO
        total_periods = hard_facts.get('total_periods', 0)
        if 0 < total_periods < 6:
            recommendations.append(
                f"{ctx_prefix}⚠️ Análisis basado en solo {total_periods} periodos. "
                "Los resultados pueden no ser representativos. Recopilar más datos históricos."
            )
        
        # 6. CORRELACIÓN FUERTE
        correlation = hard_facts.get('correlation', None)
        if correlation is not None and abs(correlation) > 0.7:
            direction = "positiva" if correlation > 0 else "inversa"
            recommendations.append(
                f"{ctx_prefix}🔗 Correlación {direction} fuerte ({correlation:.2f}). "
                "Explorar posible causalidad para optimizar decisiones."
            )
        
        # 7. TENDENCIA POSITIVA (celebrar)
        if trend == 'Creciente' and growth > 20:
            recommendations.append(
                f"{ctx_prefix}🚀 Crecimiento sólido del {growth:.1f}%. "
                "Identificar los drivers de éxito y replicar en otras áreas."
            )
        
        return recommendations
