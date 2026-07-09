import json
import pandas as pd
import numpy as np
import datetime

class ChartFactory:
    """
    Motor de generación de gráficos ECharts 'Unicorn Edition'.
    Soporta: Barras, Líneas, Pastel, Cascada, Pareto, Heatmap, Gantt, Embudo, Medidor.
    Incluye: Normalización inteligente de datos y estética profesional.
    """
    
    # Paleta "Modern Intelligence" (Inspirada en Rill/Evidence/Tremor)
    # Azul Profundo (Primario), Cian (Secundario), Violeta (KPIs), Esmeralda (Positivo), Rosa (Atención), Ámbar (Neutro)
    COLORS = ['#2563eb', '#06b6d4', '#8b5cf6', '#10b981', '#f43f5e', '#f59e0b', '#64748b', '#818cf8']
    @staticmethod
    def _normalize_data_polymorphic(data, limit=50):
        """
        Cerebro Polimórfico V2 (Con Espía y Búsqueda Profunda).
        """
        # 🕵️ ESPÍA: Ayuda a ver en logs qué llega realmente
        try:
            print(f"🕵️ [CHART FACTORY] Input Raw: {str(data)[:100]}...")
        except: pass

        normalized = []
        if not data: return []

        if isinstance(data, pd.DataFrame):
            data = data.to_dict(orient='records')

        if isinstance(data, list):
            for item in data:
                # A. Dato Simple
                if isinstance(item, (int, float, str, np.number)):
                    try:
                        val = float(item)
                        normalized.append({"name": str(val), "value": val})
                    except: continue
                
                # B. Diccionario / Objeto
                elif isinstance(item, dict):
                    # B.1 Formato ECharts Perfecto
                    if 'value' in item:
                        try: val = float(item['value'])
                        except: val = 0
                        entry = {"value": val, "name": str(item.get('name', 'N/A'))}
                        if 'itemStyle' in item: entry['itemStyle'] = item['itemStyle']
                        if 'label' in item: entry['label'] = item['label']
                        if 'extra_info' in item: entry['extra_info'] = item['extra_info']
                        normalized.append(entry)
                    
                    # B.2 Búsqueda Inteligente y B.3 "LAST RESORT" (LA MEJORA CLAVE)
                    else:
                        name, val = ChartFactory._get_smart_keys(item)
                        
                        # --- INICIO MEJORA CRÍTICA ---
                        # Si get_smart_keys falló (val=0) y hay otros datos, buscamos desesperadamente un número.
                        if val == 0:
                            for v in item.values():
                                if isinstance(v, (int, float, np.number)) and not isinstance(v, bool):
                                    val = float(v)
                                    break
                        # --- FIN MEJORA CRÍTICA ---
                        
                        entry = {"name": name, "value": val}
                        if 'extra_info' in item: entry['extra_info'] = item['extra_info']
                        normalized.append(entry)

        # Limitador
        if len(normalized) > limit:
            top_n = sorted(normalized, key=lambda x: x['value'], reverse=True)[:limit]
            others_val = sum(d['value'] for d in normalized if d not in top_n)
            top_n.append({"name": "OTROS", "value": others_val, "itemStyle": {"color": "#94a3b8"}})
            return top_n
            
        return normalized
    
    @staticmethod
    def _apply_accessible_label_contrast(option_dict):
        """
        Mejora de legibilidad quirúrgica:
        aplica contraste suave SOLO a heatmaps para evitar ruido visual global.
        """
        if not isinstance(option_dict, dict):
            return option_dict

        series = option_dict.get("series")
        series_list = series if isinstance(series, list) else [series] if series else []
        for serie in series_list:
            if not isinstance(serie, dict):
                continue

            if str(serie.get("type", "")).lower() != "heatmap":
                continue

            label = serie.get("label")
            if isinstance(label, dict) and label.get("show"):
                label.setdefault("color", "#334155")
                label.setdefault("fontWeight", 500)
                label.setdefault("fontSize", 11)
                label.setdefault("textBorderColor", "rgba(248,250,252,0.9)")
                label.setdefault("textBorderWidth", 1)

        return option_dict

    @staticmethod
    def _sanitize_for_json(option_dict):
        """Limpia tipos de datos no serializables (fechas, nans)."""
        option_dict = ChartFactory._apply_accessible_label_contrast(option_dict)

        def default_serializer(obj):
            if isinstance(obj, (pd.Timestamp, datetime.date, datetime.datetime)):
                return obj.isoformat()
            if pd.isna(obj): return None
            if isinstance(obj, (np.int64, np.int32)): return int(obj)
            if isinstance(obj, (np.float64, np.float32)): return float(obj)
            return str(obj)
        return json.loads(json.dumps(option_dict, default=default_serializer))

    @staticmethod
    def _normalize_axis_value(value):
        if isinstance(value, (pd.Timestamp, datetime.datetime, datetime.date)):
            ts = pd.Timestamp(value)
            if ts.hour == 0 and ts.minute == 0 and ts.second == 0:
                return ts.strftime("%Y-%m-%d")
            return ts.strftime("%Y-%m-%d %H:%M")
        if pd.isna(value):
            return "N/A"
        text = str(value).strip()
        if len(text) >= 10 and text[:4].isdigit() and text[4] in "-/" and text[7] in "-/":
            parsed = pd.to_datetime(text, errors='coerce')
            if pd.notna(parsed):
                if parsed.hour == 0 and parsed.minute == 0 and parsed.second == 0:
                    return parsed.strftime("%Y-%m-%d")
                return parsed.strftime("%Y-%m-%d %H:%M")
        return text

    @staticmethod
    def _infer_heatmap_columns(df, x_label=None, y_label=None):
        normalized_lookup = {str(col).strip().lower(): col for col in df.columns}

        resolved_x = normalized_lookup.get(str(x_label or "").strip().lower()) if x_label else None
        resolved_y = normalized_lookup.get(str(y_label or "").strip().lower()) if y_label else None

        non_extra_columns = [col for col in df.columns if str(col) != 'extra_info']
        if resolved_x is None and non_extra_columns:
            resolved_x = non_extra_columns[0]
        if resolved_y is None:
            fallback_candidates = [col for col in non_extra_columns if col != resolved_x]
            resolved_y = fallback_candidates[0] if fallback_candidates else None

        value_col = None
        for col in non_extra_columns:
            if col in {resolved_x, resolved_y}:
                continue
            numeric_series = pd.to_numeric(df[col], errors='coerce')
            if numeric_series.notna().any():
                value_col = col
                break

        return resolved_x, resolved_y, value_col

    @staticmethod
    def _get_smart_keys(item):
        """
        Inteligencia para encontrar 'name' y 'value' bajo cualquier alias.
        V8: Fallback universal — si no reconoce la clave, busca la primera string
        en el dict como dimensión y el primer número como métrica.
        """
        # 1. Detectar Valor (Métrica) — por alias conocidos
        val = 0
        val_found_key = None
        for key in ['value', 'valor', 'total', 'cantidad', 'stock', 'monto', 'qty', 'count']:
            if key in item:
                try: val = float(item[key])
                except: val = 0
                val_found_key = key
                break
        
        # 2. Detectar Nombre (Dimensión) — por alias conocidos
        name = None
        name_found_key = None
        for key in ['name', 'nombre', 'category', 'categoria', 'producto', 'sku', 'item', 
                     'fecha', 'date', 'periodo', 'label', 'dimension', 'grupo', 'segmento']:
            if key in item:
                name = str(item[key])
                name_found_key = key
                break
        
        # 3. 🛡️ [V8] FALLBACK UNIVERSAL: Si no encontramos por alias, usamos heurística
        if name is None:
            # Buscar la primera clave cuyo valor sea string o no sea estadístico
            stat_keys = {'min', 'q1', 'median', 'q3', 'max', 'mean', 'std', 'sum', 'avg', 'count'}
            for k, v in item.items():
                if k.lower() in stat_keys or k == val_found_key:
                    continue
                # Si el valor es string/no-numérico, es probablemente la dimensión
                if isinstance(v, str):
                    name = v
                    break
                # Si es numérico pero no es una métrica ya encontrada, podría ser un ID/código
                if val_found_key and k != val_found_key:
                    try:
                        float(v)
                        # Es numérico — podría ser un código de almacén como "130"
                        name = str(v)
                        break
                    except (ValueError, TypeError):
                        continue
        
        # 4. Sanitización final — nunca devolver None, NaN o vacío
        if name is None or str(name).lower() in ('nan', 'none', ''):
            name = "N/A"
        else:
            # Limpiar floats que son IDs (130.0 → "130")
            try:
                num = float(name)
                if num == int(num):
                    name = str(int(num))
            except (ValueError, TypeError):
                pass
        
        # 5. Fallback valor: si no encontramos métrica por alias, buscar primer numérico
        if val == 0 and val_found_key is None:
            for k, v in item.items():
                if k == name_found_key:
                    continue
                try:
                    val = float(v)
                    if val != 0:
                        break
                except (ValueError, TypeError):
                    continue
                
        return name, val

    @staticmethod
    def _get_base_option(title):
        """Configuración base para TODOS los gráficos (Estilo unificado)."""
        return {
            'title': {
                'show': False, # APAGADO para evitar duplicidad con el Dashboard Card
                'text': title,
                'left': 'center'
            },
            'tooltip': {
                'trigger': 'axis',
                'confine': True,
                'backgroundColor': 'rgba(255, 255, 255, 0.95)',
                'borderColor': '#eee',
                'borderWidth': 1,
                'textStyle': {'color': '#333'}
            },
            'legend': {
                'show': True,
                'type': 'scroll', 
                'bottom': 0,
                'padding': [15, 0, 0, 0]
            },
            'grid': {
                'left': '3%',
                'right': '4%',
                'bottom': '12%', 
                'top': '10%',
                'containLabel': True # ¡CRÍTICO! Esto asegura que se vean los números del eje Y
            },
            'color': ChartFactory.COLORS
        }

    # --- GRÁFICOS BÁSICOS ---

    @staticmethod
    def build_bar_chart(title, data, horizontal=True, currency_meta=None, barmode=None):
        """Barras Inteligentes (Acepta todo). Soporta multi-dimension (apiladas/lado-a-lado)."""
        if not data: return ChartFactory._get_base_option(title)
        
        is_multi_series = barmode is not None and isinstance(data, list) and isinstance(data[0], dict) and len(data[0].keys()) > 2
        
        if is_multi_series:
            # Flujo Multi-Dimensional (Pivot Data)
            categories = [str(d.get("name", "N/A")) for d in data]
            
            # Extraer nombres de las series
            series_names = []
            for k in data[0].keys():
                key_str = str(k)
                if key_str not in ['name', 'extra_info'] and not key_str.startswith('_'):
                    series_names.append(k)
                    
            option = ChartFactory._get_base_option(title)
            option["tooltip"]["trigger"] = "axis"
            option["tooltip"]["axisPointer"] = {"type": "shadow"}
            
            if horizontal:
                option["xAxis"] = {"type": "value"}
                option["yAxis"] = {"type": "category", "data": categories, "axisLabel": {"interval": 0}}
                option["grid"]["left"] = "10%" 
            else:
                x_label_opts = {"interval": 0}
                if len(categories) > 5: x_label_opts["rotate"] = 45
                option["xAxis"] = {"type": "category", "data": categories, "axisLabel": x_label_opts}
                option["yAxis"] = {"type": "value"}
                
            option["series"] = []
            for i, s_name in enumerate(series_names):
                s_data = []
                for d in data:
                    try:
                        s_data.append(float(d.get(s_name, 0)))
                    except:
                        s_data.append(0.0)
                        
                serie_def = {
                    "name": str(s_name),
                    "type": "bar",
                    "data": s_data,
                    "itemStyle": {"color": ChartFactory.COLORS[i % len(ChartFactory.COLORS)]}
                }
                
                if barmode == "stacked":
                    serie_def["stack"] = "total"
                    
                option["series"].append(serie_def)

            if currency_meta:
                 sym = currency_meta.get('symbol', '')
                 if sym:
                     target_axis = "xAxis" if horizontal else "yAxis"
                     option[target_axis]["axisLabel"] = {"formatter": f"{sym} {{value}}"}
                     option["tooltip"]["valueFormatter"] = f"(val) => '{sym} ' + val"
            
            return ChartFactory._sanitize_for_json(option)
            
        # --- Flujo Clásico (Uni-Dimensional) ---
        clean_data = ChartFactory._normalize_data_polymorphic(data)
        
        if horizontal: clean_data.reverse()
            
        categories = [d['name'] for d in clean_data]
        
        option = ChartFactory._get_base_option(title)
        
        unit_suffix = ""
        if len(clean_data) > 0 and 'extra_info' in clean_data[0]:
            unit_suffix = clean_data[0]['extra_info'].get('unit_suffix', '')

        if horizontal:
            option["xAxis"] = {"type": "value"}
            option["yAxis"] = {"type": "category", "data": categories, "axisLabel": {"interval": 0}}
            option["grid"]["left"] = "5%" 
            if unit_suffix and not (currency_meta and currency_meta.get('symbol')):
                 option["xAxis"]["axisLabel"] = {"formatter": f"{{value}}{unit_suffix}"}
        else:
            x_label_opts = {"interval": 0}
            if len(categories) > 10:
                x_label_opts["rotate"] = 45
            option["xAxis"] = {"type": "category", "data": categories, "axisLabel": x_label_opts}
            option["yAxis"] = {"type": "value"}
            if unit_suffix and not (currency_meta and currency_meta.get('symbol')):
                 option["yAxis"]["axisLabel"] = {"formatter": f"{{value}}{unit_suffix}"}

        option["series"] = [{
            "name": title,
            "type": "bar",
            "data": clean_data,
            "label": {"show": True, "position": "right" if horizontal else "top"}
        }]
        if currency_meta:
             sym = currency_meta.get('symbol', '')
             if sym:
                 target_axis = "xAxis" if horizontal else "yAxis"
                 option[target_axis]["axisLabel"] = {"formatter": f"{sym} {{value}}"}
                 option["tooltip"]["valueFormatter"] = f"(val) => '{sym} ' + val"
                 
        elif unit_suffix:
             option["tooltip"]["valueFormatter"] = f"(val) => val + '{unit_suffix}'"

        return ChartFactory._sanitize_for_json(option)

    @staticmethod
    def build_line_chart(title, data, currency_meta=None, area=False, barmode=None):
        """Líneas Inteligentes (Soporte Forecast + Anomalías + Multi-Serie)."""
        if not data: return ChartFactory._get_base_option(title)
        
        is_multi_series = barmode is not None and isinstance(data, list) and isinstance(data[0], dict) and len(data[0].keys()) > 2
        
        if is_multi_series:
            # Flujo Multi-Dimensional (Pivot Data)
            categories = [str(d.get("name", "N/A")) for d in data]
            
            # Extraer nombres de las series
            series_names = []
            for k in data[0].keys():
                key_str = str(k)
                if key_str not in ['name', 'extra_info'] and not key_str.startswith('_'):
                    series_names.append(k)
                    
            option = ChartFactory._get_base_option(title)
            option["tooltip"]["trigger"] = "axis"
            
            option["xAxis"] = {"type": "category", "data": categories, "boundaryGap": False}
            option["yAxis"] = {"type": "value", "splitLine": {"show": True, "lineStyle": {"type": "dashed", "color": "#e5e7eb"}}}
            
            option["series"] = []
            for i, s_name in enumerate(series_names):
                s_data = []
                for d in data:
                    try:
                        s_data.append(float(d.get(s_name, 0)))
                    except:
                        s_data.append(0.0)
                        
                serie_def = {
                    "name": str(s_name),
                    "type": "line",
                    "data": s_data,
                    "itemStyle": {"color": ChartFactory.COLORS[i % len(ChartFactory.COLORS)]},
                    "smooth": True,
                    "connectNulls": True,
                    "showSymbol": len(data) < 20,
                    "symbolSize": 8
                }
                option["series"].append(serie_def)

            if currency_meta:
                 sym = currency_meta.get('symbol', '')
                 if sym:
                     option["yAxis"]["axisLabel"] = {"formatter": f"{sym} {{value}}"}
                     option["tooltip"]["valueFormatter"] = f"(val) => val ? '{sym} ' + val : ''"
            
            return ChartFactory._sanitize_for_json(option)

        clean_data = ChartFactory._normalize_data_polymorphic(data, limit=2000)
        
        # [FASE 2] Split History vs Forecast via extra_info
        hist_data = []
        fcst_data = []
        
        for d in clean_data:
            extra = d.get('extra_info', {})
            if extra and extra.get('type') == 'forecast':
                fcst_data.append(d)
            else:
                hist_data.append(d)
        
        # Conexión: El forecast debe nacer del último punto histórico
        if hist_data and fcst_data:
             fcst_data.insert(0, hist_data[-1])
        
        # Definir Categorías (Eje X) usando la unión ordenada
        full_data = hist_data + fcst_data[1:] if (hist_data and fcst_data) else clean_data
        categories = [d['name'] for d in full_data]
        
        # 🔴 [PHASE 2] Detect anomaly points from extra_info
        anomaly_marks = []
        for d in clean_data:
            extra = d.get('extra_info', {})
            if extra and extra.get('is_anomaly'):
                anomaly_marks.append({
                    'name': '⚠ Anomalía',
                    'coord': [d['name'], d['value']],
                    'itemStyle': {'color': '#ef4444'},
                    'symbol': 'pin',
                    'symbolSize': 40,
                    'label': {'show': True, 'formatter': '⚠'}
                })
        
        series_list = []
        
        # Función auxiliar para alinear datos con el eje X compartido
        def extract_vals(dataset, all_cats):
            val_map = {d['name']: d['value'] for d in dataset}
            return [val_map.get(cat, None) for cat in all_cats]

        if fcst_data:
             series_list.append({
                 "name": "Historia", "type": "line", 
                 "data": extract_vals(hist_data, categories),
                 "itemStyle": {"color": "#2563eb"}, "smooth": True,
                 "connectNulls": True,
                 "symbol": "none"
             })
             series_list.append({
                 "name": "Proyección", "type": "line", 
                 "data": extract_vals(fcst_data, categories),
                 "itemStyle": {"color": "#f59e0b"}, 
                 "lineStyle": {"type": "dashed", "width": 3},
                 "smooth": True, "connectNulls": True,
                 "symbol": "circle", "symbolSize": 6
             })
        else:
             # Modo Clásico (Una sola serie)
             main_series = {
                 "name": title, "type": "line", 
                 "data": [d['value'] for d in clean_data],
                 "smooth": True,
                 "connectNulls": True,
                 "showSymbol": len(clean_data) < 20,
                 "symbolSize": 8,
                 "itemStyle": {"color": "#2563eb"},
                 "label": {"show": False},
                 # 📊 [PHASE 2] Built-in peak & trough markers
                 "markPoint": {
                     "data": [
                         {"type": "max", "name": "Pico", "itemStyle": {"color": "#10b981"}},
                         {"type": "min", "name": "Valle", "itemStyle": {"color": "#f59e0b"}}
                     ]
                 }
             }
             if area:
                 main_series["areaStyle"] = {
                    "color": {
                        "type": "linear",
                        "x": 0,
                        "y": 0,
                        "x2": 0,
                        "y2": 1,
                        "colorStops": [
                            {"offset": 0, "color": "rgba(59, 130, 246, 0.5)"},
                            {"offset": 1, "color": "rgba(59, 130, 246, 0.0)"},
                        ],
                    }
                 }
             # 🔴 Add anomaly markers if detected
             if anomaly_marks:
                 main_series["markPoint"]["data"].extend(anomaly_marks)
             series_list.append(main_series)

        option = ChartFactory._get_base_option(title)
        
        option.update({
            "xAxis": {"type": "category", "data": categories, "boundaryGap": False},
            "yAxis": {"type": "value", "splitLine": {"show": True, "lineStyle": {"type": "dashed", "color": "#e5e7eb"}}},
            "series": series_list
        })

        # 🛡️ [VISUAL SAFETY] Extracción de Sufijo de Unidad
        unit_suffix = ""
        if len(clean_data) > 0 and 'extra_info' in clean_data[0]:
            unit_suffix = clean_data[0]['extra_info'].get('unit_suffix', '')

        if currency_meta:
             sym = currency_meta.get('symbol', '')
             if sym:
                 option["yAxis"]["axisLabel"] = {"formatter": f"{sym} {{value}}"}
                 option["tooltip"]["valueFormatter"] = f"(val) => val ? '{sym} ' + val : ''"
        elif unit_suffix:
             option["yAxis"]["axisLabel"] = {"formatter": f"{{value}}{unit_suffix}"}
             option["tooltip"]["valueFormatter"] = f"(val) => val ? val + '{unit_suffix}' : ''"

        return ChartFactory._sanitize_for_json(option)

    @staticmethod
    def _is_multiseries_pie_data(data) -> bool:
        """Detect multi-series pivot data that a Pie chart cannot render properly."""
        if not isinstance(data, list) or len(data) == 0:
            return False
        first = data[0]
        if not isinstance(first, dict):
            return False
        numeric_keys = {k for k, v in first.items() if isinstance(v, (int, float))}
        return len(numeric_keys) >= 2

    @staticmethod
    def create_chart(chart_type, title, data, x_label=None, y_label=None, currency_meta=None, barmode=None):
        """
        Dispatcher Centralizado (Factory Method).
        """
        # ── Geometric fallback: Pie is 2D (name + value). Multi-series data
        #     needs a 3rd dimension → redirect to stacked bar.
        #     Smart Table conversion is handled at the orchestrator level.
        if chart_type == 'pie' and ChartFactory._is_multiseries_pie_data(data):
            print(f"🔄 [CHART FACTORY] Pie → Stacked Bar (multi-series data detected)")
            chart_type = 'bar'
            barmode = barmode or 'stacked'

        factory = ChartFactory()
        if chart_type == 'bar': return factory.build_bar_chart(title, data, currency_meta=currency_meta, barmode=barmode)
        elif chart_type == 'line': return factory.build_line_chart(title, data, currency_meta=currency_meta, barmode=barmode)
        elif chart_type == 'pie': return factory.build_pie_chart(title, data, currency_meta=currency_meta)
        elif chart_type == 'heatmap': return factory._build_heatmap_chart(title, data, x_label, y_label)
        elif chart_type == 'waterfall': return factory._build_waterfall_chart(title, data)
        elif chart_type == 'funnel': return factory.build_funnel_chart(title, data)
        elif chart_type == 'gauge': return factory.build_gauge_chart(title, data)
        elif chart_type == 'scatter': return factory._build_scatter_chart(title, data, x_label, y_label)
        elif chart_type == 'bubble': return factory.build_bubble_chart(title, data, x_label, y_label)
        elif chart_type == 'combo': return factory.build_combo_chart(title, data, currency_meta=currency_meta)
        elif chart_type == 'treemap': return factory._build_treemap_chart(title, data)
        elif chart_type == 'boxplot': return factory.build_boxplot(title, data)
        elif chart_type == 'gantt': return factory.build_gantt_chart(title, data)
        elif chart_type == 'histogram': return factory.build_histogram_chart(title, data)
        else:
            return factory.build_bar_chart(title, data, currency_meta=currency_meta, barmode=barmode)

    # --- REFACTOR PIE CHART (TOP 5 + OTROS) ---
    @staticmethod
    def build_pie_chart(title, data, currency_meta=None):
        """Donut Inteligente (Top 5 + Otros)."""
        clean_data = ChartFactory._normalize_data_polymorphic(data, limit=50) 
        
        # Ordenar descendente
        clean_data = sorted(clean_data, key=lambda x: x['value'], reverse=True)

        # Lógica de Agrupación (Más de 6 rebanadas -> Top 5 + Otros)
        if len(clean_data) > 6:
            top_5 = clean_data[:5]
            others_val = sum(d['value'] for d in clean_data[5:])
            top_5.append({"name": "OTROS", "value": others_val, "itemStyle": {"color": "#94a3b8"}})
            clean_data = top_5

        option = ChartFactory._get_base_option(title)
        option["tooltip"]["trigger"] = "item"
        option["series"] = [{
            "name": title,
            "type": "pie",
            "radius": ["40%", "70%"],
            "avoidLabelOverlap": True,
            "minShowLabelAngle": 4,
            "itemStyle": {"borderRadius": 5, "borderColor": "#fff", "borderWidth": 2},
            "data": clean_data, 
            "labelLine": {
                "show": True,
                "length": 12,
                "length2": 8,
                "smooth": 0.2,
                "lineStyle": {"color": "rgba(148, 163, 184, 0.85)", "width": 1},
            },
            "label": {
                "show": True,
                "formatter": "{b}\n{c}",
                "position": "outside",
                "fontSize": 12,
                "lineHeight": 16,
                "distanceToLabelLine": 4,
            },
            "emphasis": {"label": {"show": True, "fontWeight": "bold"}}
        }]
        # 🛡️ [VISUAL SAFETY] Extracción de Sufijo de Unidad
        unit_suffix = ""
        if len(clean_data) > 0 and 'extra_info' in clean_data[0]:
            unit_suffix = clean_data[0]['extra_info'].get('unit_suffix', '')

        if currency_meta:
             sym = currency_meta.get('symbol', '')
             if sym:
                 option["tooltip"]["valueFormatter"] = f"(val) => '{sym} ' + val"
        
        # Si NO hay moneda pero SÍ hay sufijo
        elif unit_suffix:
             option["tooltip"]["valueFormatter"] = f"(val) => val + '{unit_suffix}'"

        return ChartFactory._sanitize_for_json(option)

    # --- NUEVOS CONSTRUCTORES PRIVADOS ---

    def _build_heatmap_chart(self, title, data, x_label=None, y_label=None):
        """Mapa de Calor (X, Y, Intensidad)."""
        df = pd.DataFrame(data)
        if len(df.columns) < 3:
            return {"error": "Heatmap requiere 3 columnas"}

        x_col, y_col, val_col = ChartFactory._infer_heatmap_columns(df, x_label, y_label)
        if x_col is None or y_col is None or val_col is None:
            return {"error": "Heatmap requiere dos ejes categóricos y una intensidad numérica."}

        df = df[[x_col, y_col, val_col]].copy()
        df[val_col] = pd.to_numeric(df[val_col], errors='coerce')
        df = df.dropna(subset=[val_col])
        if df.empty:
            return {"error": "Heatmap sin datos numéricos válidos."}

        df[x_col] = df[x_col].apply(ChartFactory._normalize_axis_value)
        df[y_col] = df[y_col].apply(ChartFactory._normalize_axis_value)
        df = df.groupby([x_col, y_col], as_index=False)[val_col].sum()

        # Cap de cardinalidad para evitar heatmaps saturados o ilegibles
        max_x, max_y = 12, 12
        if df[x_col].nunique(dropna=True) > max_x:
            parsed_x = pd.to_datetime(df[x_col], errors='coerce')
            if float(parsed_x.notna().mean()) >= 0.7:
                df = df.assign(__x_dt=parsed_x).dropna(subset=['__x_dt'])
                keep_x = sorted(df['__x_dt'].unique())[-max_x:]
                df = df[df['__x_dt'].isin(keep_x)]
                df[x_col] = df['__x_dt'].dt.strftime('%Y-%m-%d')
                df = df.drop(columns=['__x_dt'])
            else:
                top_x = (
                    df.groupby(x_col)[val_col]
                    .sum()
                    .abs()
                    .sort_values(ascending=False)
                    .head(max_x)
                    .index
                )
                df = df[df[x_col].isin(top_x)]

        if df[y_col].nunique(dropna=True) > max_y:
            top_y = (
                df.groupby(y_col)[val_col]
                .sum()
                .abs()
                .sort_values(ascending=False)
                .head(max_y)
                .index
            )
            df = df[df[y_col].isin(top_y)]

        if df.empty:
            return {"error": "Heatmap sin celdas válidas después del ajuste de legibilidad."}

        # Ensure unique categories
        x_values = [ChartFactory._normalize_axis_value(value) for value in df[x_col].tolist()]
        y_values = [ChartFactory._normalize_axis_value(value) for value in df[y_col].tolist()]
        x_cats = list(dict.fromkeys(x_values))
        y_cats = list(dict.fromkeys(y_values))
        
        echarts_data = []
        for _, row in df.iterrows():
            try:
                x_val = ChartFactory._normalize_axis_value(row[x_col])
                y_val = ChartFactory._normalize_axis_value(row[y_col])
                val = float(pd.to_numeric(row[val_col], errors='coerce'))
                
                if x_val in x_cats and y_val in y_cats:
                    x_idx = x_cats.index(x_val)
                    y_idx = y_cats.index(y_val)
                    echarts_data.append([x_idx, y_idx, val])
            except: continue
            
        option = ChartFactory._get_base_option(title)
        option['grid']['top'] = '15%'
        option['grid']['bottom'] = '15%'
        
        option['xAxis'] = {'type': 'category', 'data': x_cats, 'splitArea': {'show': True}}
        option['yAxis'] = {'type': 'category', 'data': y_cats, 'splitArea': {'show': True}}
        
        vals = [d[2] for d in echarts_data]
        val_min = min(vals) if vals else 0
        val_max = max(vals) if vals else 100
        
        option['visualMap'] = {
            'min': val_min, 'max': val_max,
            'calculable': True, 'orient': 'horizontal',
            'left': 'center', 'bottom': '0%',
            'inRange': {'color': ['#f0f9ff', '#0ea5e9', '#1e3a8a']} 
        }
        
        show_labels = len(echarts_data) <= 60
        option['series'] = [{
            'name': title, 'type': 'heatmap', 'data': echarts_data,
            'label': {
                'show': show_labels,
                'formatter': '{c}',
                'color': '#334155',
                'fontWeight': 500,
                'fontSize': 11,
                'textBorderColor': 'rgba(248,250,252,0.9)',
                'textBorderWidth': 1
            },
            'itemStyle': {'emphasis': {'shadowBlur': 10, 'shadowColor': 'rgba(0, 0, 0, 0.5)'}}
        }]
        return ChartFactory._sanitize_for_json(option)

    def _build_scatter_chart(self, title, data, x_label=None, y_label=None):
        """Scatter Plot (Correlación)."""
        clean_data = []
        grouped_series = {}
        x_label_key = str(x_label or "").strip().lower()
        y_label_key = str(y_label or "").strip().lower()

        if isinstance(data, list):
            for index, item in enumerate(data):
                if isinstance(item, dict):
                    record = {str(key).strip().lower(): value for key, value in item.items() if key != 'extra_info'}
                    x_candidate = record.get(x_label_key) if x_label_key else None
                    y_candidate = record.get(y_label_key) if y_label_key else None
                    if x_candidate is None:
                        x_candidate = record.get('x_value')
                    if y_candidate is None:
                        y_candidate = record.get('y_value')
                    x_numeric = pd.to_numeric(x_candidate, errors='coerce') if x_candidate is not None else None
                    y_numeric = pd.to_numeric(y_candidate, errors='coerce') if y_candidate is not None else None
                    if x_numeric is not None and y_numeric is not None and pd.notna(x_numeric) and pd.notna(y_numeric):
                        point_name = str(
                            record.get('raw_name')
                            or record.get('name')
                            or record.get('label')
                            or f'Punto {index + 1}'
                        )
                        point_payload = {
                            "name": point_name,
                            "raw_name": point_name,
                            "value": [float(x_numeric), float(y_numeric)],
                        }
                        series_name = record.get('series') or record.get('category')
                        if isinstance(series_name, str) and series_name.strip():
                            grouped_series.setdefault(series_name.strip(), []).append(point_payload)
                        else:
                            clean_data.append(point_payload)
                        continue

                    vals = [
                        float(value)
                        for value in record.values()
                        if isinstance(value, (int, float, np.number))
                    ]
                    if len(vals) >= 2:
                        clean_data.append({
                            "name": f"Punto {index + 1}",
                            "raw_name": f"Punto {index + 1}",
                            "value": vals[:2],
                        })
                elif isinstance(item, (list, tuple)) and len(item) >= 2:
                     clean_data.append({
                         "name": f"Punto {index + 1}",
                         "raw_name": f"Punto {index + 1}",
                         "value": list(item[:2]),
                     })

        option = ChartFactory._get_base_option(title)
        option['xAxis'] = {'type': 'value', 'name': x_label or 'X', 'scale': True}
        option['yAxis'] = {'type': 'value', 'name': y_label or 'Y', 'scale': True}

        if grouped_series:
            option['series'] = []
            for series_index, (series_name, points) in enumerate(grouped_series.items()):
                option['series'].append({
                    'name': series_name,
                    'symbolSize': 10,
                    'data': points,
                    'type': 'scatter',
                    'itemStyle': {'color': ChartFactory.COLORS[series_index % len(ChartFactory.COLORS)]}
                })
            if clean_data:
                option['series'].append({
                    'name': 'Otros',
                    'symbolSize': 10,
                    'data': clean_data,
                    'type': 'scatter',
                    'itemStyle': {'color': ChartFactory.COLORS[len(grouped_series) % len(ChartFactory.COLORS)]}
                })
        else:
            option['series'] = [{
                'symbolSize': 10,
                'data': clean_data,
                'type': 'scatter',
                'itemStyle': {'color': ChartFactory.COLORS[0]}
            }]
        return ChartFactory._sanitize_for_json(option)

    @staticmethod
    def build_bubble_chart(title, data, x_label=None, y_label=None):
        """Bubble Chart (Correlación + tercera magnitud)."""
        clean_data = []

        if isinstance(data, list):
            for index, item in enumerate(data):
                if isinstance(item, dict):
                    numeric_values = [float(v) for v in item.values() if isinstance(v, (int, float))]
                    if len(numeric_values) >= 3:
                        label = str(item.get('name', item.get('label', f'Punto {index + 1}')))
                        clean_data.append({
                            'name': label,
                            'value': numeric_values[:3],
                            'symbolSize': max(10, min(42, float(numeric_values[2]))),
                        })
                        continue
                    extra = item.get('extra_info', {}) if isinstance(item.get('extra_info'), dict) else {}
                    size = extra.get('bubble_size', extra.get('size'))
                    if len(numeric_values) >= 2 and isinstance(size, (int, float)):
                        label = str(item.get('name', item.get('label', f'Punto {index + 1}')))
                        clean_data.append({
                            'name': label,
                            'value': [numeric_values[0], numeric_values[1], float(size)],
                            'symbolSize': max(10, min(42, float(size))),
                        })
                elif isinstance(item, (list, tuple)) and len(item) >= 3:
                    numeric_values = [float(v) for v in item[:3] if isinstance(v, (int, float))]
                    if len(numeric_values) == 3:
                        clean_data.append({
                            'name': f'Punto {index + 1}',
                            'value': numeric_values,
                            'symbolSize': max(10, min(42, float(numeric_values[2]))),
                        })

        option = ChartFactory._get_base_option(title)
        option['xAxis'] = {'type': 'value', 'name': x_label or 'X', 'scale': True}
        option['yAxis'] = {'type': 'value', 'name': y_label or 'Y', 'scale': True}
        option['tooltip']['trigger'] = 'item'
        option['series'] = [{
            'type': 'scatter',
            'data': clean_data,
            'itemStyle': {'color': ChartFactory.COLORS[0]}
        }]
        return ChartFactory._sanitize_for_json(option)

    @staticmethod
    def build_histogram_chart(title, data, bins=8):
        """Histogram (Distribución numérica)."""
        numeric_values = []

        if isinstance(data, list):
            for item in data:
                if isinstance(item, (int, float)) and not isinstance(item, bool):
                    numeric_values.append(float(item))
                    continue
                if isinstance(item, (list, tuple)):
                    for value in item:
                        if isinstance(value, (int, float)) and not isinstance(value, bool):
                            numeric_values.append(float(value))
                    continue
                if isinstance(item, dict):
                    values = [v for k, v in item.items() if k != 'extra_info']
                    numeric_candidates = [
                        float(v)
                        for v in values
                        if isinstance(v, (int, float, np.number)) and not isinstance(v, bool)
                    ]
                    if numeric_candidates:
                        numeric_values.extend(numeric_candidates)

        if not numeric_values:
            return ChartFactory._get_base_option(title)

        counts, edges = np.histogram(np.array(numeric_values), bins=max(5, min(int(bins), 12)))
        labels = []
        for idx in range(len(edges) - 1):
            labels.append(f"{edges[idx]:.1f} - {edges[idx + 1]:.1f}")

        option = ChartFactory._get_base_option(title)
        option['tooltip']['trigger'] = 'axis'
        option['xAxis'] = {'type': 'category', 'data': labels, 'axisLabel': {'interval': 0, 'rotate': 30}}
        option['yAxis'] = {'type': 'value', 'name': 'Frecuencia'}
        option['series'] = [{
            'name': 'Frecuencia',
            'type': 'bar',
            'data': counts.astype(int).tolist(),
            'barMaxWidth': 40,
            'itemStyle': {'color': ChartFactory.COLORS[0], 'borderRadius': [4, 4, 0, 0]}
        }]
        return ChartFactory._sanitize_for_json(option)

    @staticmethod
    def build_waterfall_chart(title, data):
        factory = ChartFactory()
        return factory._build_waterfall_chart(title, data)

    def _build_waterfall_chart(self, title, data):
        """Waterfall (Finanzas)."""
        # Reusing the logic but ensuring it matches the private signature
        # data = [{'name': 'Ingresos', 'value': 100}, ...]
        
        x_data = []
        val_data = []
        
        for item in data:
            n, v = ChartFactory._get_smart_keys(item)
            x_data.append(n)
            val_data.append(v)

        base_data = []
        display_vals = []
        colors = []
        current_sum = 0
        
        for val in val_data:
            if val >= 0:
                base_data.append(current_sum)
                display_vals.append(val)
                colors.append(ChartFactory.COLORS[3]) # Verde (Positivo - index 3 is #10b981)
                current_sum += val
            else:
                current_sum += val
                base_data.append(current_sum)
                display_vals.append(abs(val))
                colors.append(ChartFactory.COLORS[4]) # Rojo (Negativo - index 4 is #f43f5e)
        
        # Total Final
        x_data.append("Total")
        base_data.append(0)
        display_vals.append(current_sum)
        colors.append(ChartFactory.COLORS[0]) # Azul

        option = ChartFactory._get_base_option(title)
        option['tooltip']['trigger'] = 'axis'
        option['xAxis'] = {'type': 'category', 'data': x_data}
        option['yAxis'] = {'type': 'value'}
        
        option['series'] = [
            {
                'name': 'Base',
                'type': 'bar',
                'stack': 'all',
                'itemStyle': {'borderColor': 'transparent', 'color': 'rgba(0,0,0,0)'},
                'data': base_data,
                'tooltip': {'show': False}
            },
            {
                'name': 'Valor',
                'type': 'bar',
                'stack': 'all',
                'data': [{'value': v, 'itemStyle': {'color': c}} for v, c in zip(display_vals, colors)],
                'label': {'show': True, 'position': 'top'}
            }
        ]
        return ChartFactory._sanitize_for_json(option)

    def _build_treemap_chart(self, title, data):
        """Treemap (Jerarquías)."""
        # data = [{name: 'A', value: 100}, ...]
        clean_data = ChartFactory._normalize_data_polymorphic(data, limit=20)
        
        option = ChartFactory._get_base_option(title)
        option['tooltip']['trigger'] = 'item'
        
        option['series'] = [{
            'type': 'treemap',
            'name': title,
            'data': clean_data,
            'breadcrumb': {'show': False},
            'itemStyle': {'borderColor': '#fff'}
        }]
        return ChartFactory._sanitize_for_json(option)

    @staticmethod
    def build_boxplot(title, data, currency_meta=None, outliers=None):
        """Boxplot (Variabilidad/Distribución)."""
        # data expected: List of dicts with keys matching Ibis output OR normalized list
        # Ibis Output: [{dimension: 'Cat A', min: 10, q1: 20, median: 30, q3: 40, max: 50}, ...]
        
        clean_data = []
        categories = []
        
        # 1. Normalización Específica para Boxplot
        if isinstance(data, pd.DataFrame):
            data = data.to_dict(orient='records')
            
        for item in data:
            # Intentar extraer los 5 estadísticos
            try:
                # Buscamos dimensión (name)
                name, _ = ChartFactory._get_smart_keys(item)
                
                # Extracción robusta de estadísticas
                # Keys esperadas: min, q1, median, q3, max (case-insensitive)
                current_stats = {}
                for k, v in item.items():
                    current_stats[k.lower()] = v
                
                # Orden ECharts: [min, Q1, median, Q3, max]
                stats = [
                    float(current_stats.get('min', 0)),
                    float(current_stats.get('q1', 0)),
                    float(current_stats.get('median', 0)),
                    float(current_stats.get('q3', 0)),
                    float(current_stats.get('max', 0))
                ]
                
                # Validación básica: Si todo es 0, probablemente falló la extracción
                if sum(stats) == 0: continue

                categories.append(name)
                clean_data.append(stats)
            except (ValueError, TypeError):
                continue

        option = ChartFactory._get_base_option(title)
        
        option['xAxis'] = {
            'type': 'category', 
            'data': categories, 
            'boundaryGap': True,
            'nameGap': 30,
            'splitArea': {'show': False},
            'splitLine': {'show': False}
        }
        option['yAxis'] = {
            'type': 'value', 
            'name': 'Valor',
            'splitArea': {'show': True}
        }
        
        # Currency Formatting
        y_axis_label = '{value}'
        
        if currency_meta:
            symbol = currency_meta.get('symbol', '$')
            y_axis_label = f'{symbol} {{value}}'
            option['yAxis']['axisLabel'] = {'formatter': y_axis_label}

        # 🔴 [PHASE 2] Process outlier points into ECharts scatter format
        scatter_data = []
        if outliers:
            # Map outlier category names to their index in categories[]
            cat_index_map = {str(c): i for i, c in enumerate(categories)}
            for o in outliers:
                cat_idx = cat_index_map.get(str(o.get('name', '')))
                if cat_idx is not None:
                    scatter_data.append([cat_idx, float(o.get('value', 0))])

        option['series'] = [{
            'name': title,
            'type': 'boxplot',
            'data': clean_data,
            'itemStyle': {
                'color': '#fff',
                'borderColor': '#2563eb', # Azul PromData
                'borderWidth': 1.5
            },
            'tooltip': {'formatter': None} # Default ECharts tooltip for boxplot is good
        },
        {
            'name': 'Outlier', 
            'type': 'scatter', 
            'data': scatter_data,
            'itemStyle': {
                'color': '#ef4444'  # Rojo para anomalías
            },
            'symbolSize': 8,
            'tooltip': {
                'formatter': '{b}: {c}'
            }
        }]
        return ChartFactory._sanitize_for_json(option)

    # --- C O M B O   C H A R T   ( D U A L   A X I S ) ---
    @staticmethod
    def build_combo_chart(title, data, currency_meta=None):
        """
        Combo Chart (Dual Axis): Barras para metrica primaria + Linea para metrica secundaria.
        Ordena descendentemente por la metrica primaria para legibilidad gerencial.
        """
        if not data or not isinstance(data, list):
            return ChartFactory._get_base_option(title)

        metric_keys = []
        for k in data[0].keys():
            if k not in ('name', 'extra_info') and not k.startswith('_'):
                try:
                    float(data[0][k])
                    metric_keys.append(k)
                except (ValueError, TypeError):
                    continue
            if len(metric_keys) >= 2:
                break

        if len(metric_keys) < 2:
            return ChartFactory.build_bar_chart(title, data, currency_meta=currency_meta)

        primary_metric = metric_keys[0]
        secondary_metric = metric_keys[1]

        sorted_data = sorted(data, key=lambda d: float(d.get(primary_metric, 0)), reverse=True)
        categories = [str(d.get("name", "N/A")) for d in sorted_data]

        option = ChartFactory._get_base_option(title)
        option["tooltip"]["trigger"] = "axis"

        x_label_opts = {"interval": 0}
        if len(categories) > 10:
            x_label_opts["rotate"] = 45
        option["xAxis"] = {"type": "category", "data": categories, "axisLabel": x_label_opts}

        option["yAxis"] = [
            {"type": "value", "name": primary_metric.replace("_", " ").title(), "position": "left"},
            {"type": "value", "name": secondary_metric.replace("_", " ").title(), "position": "right"},
        ]

        bar_data = [float(d.get(primary_metric, 0)) for d in sorted_data]
        line_data = [float(d.get(secondary_metric, 0)) for d in sorted_data]

        option["series"] = [
            {"name": primary_metric.replace("_", " ").title(), "type": "bar", "data": bar_data,
             "yAxisIndex": 0, "itemStyle": {"color": ChartFactory.COLORS[0]}},
            {"name": secondary_metric.replace("_", " ").title(), "type": "line", "data": line_data,
             "yAxisIndex": 1, "itemStyle": {"color": ChartFactory.COLORS[1]}, "smooth": True},
        ]

        if currency_meta:
            sym = currency_meta.get('symbol', '')
            if sym:
                option["yAxis"][0]["axisLabel"] = {"formatter": f"{sym} {{value}}"}

        return ChartFactory._sanitize_for_json(option)

    @staticmethod
    def build_pareto_chart(title, data, currency_meta=None):
        """
        Pareto (80/20 Rule).
        Combines Bars (Absolute Value) + Line (Cumulative Percentage).
        """
        # 1. Normalize & Sort descending
        clean_data = ChartFactory._normalize_data_polymorphic(data)
        clean_data.sort(key=lambda x: x['value'], reverse=True)
        
        if not clean_data: return ChartFactory._get_base_option(title)
        
        # 2. Calculate Cumulative Percentage
        total_val = sum(d['value'] for d in clean_data)
        cumulative = 0
        
        x_data = []
        bar_data = []
        line_data = []
        
        for item in clean_data:
            val = item['value']
            cumulative += val
            percentage = (cumulative / total_val) * 100 if total_val > 0 else 0
            
            x_data.append(item['name'])
            bar_data.append(val)
            line_data.append(round(percentage, 1))
            
        # 3. Build Dual Axis Chart
        option = ChartFactory._get_base_option(title)
        
        option["xAxis"] = {
            "type": "category",
            "data": x_data,
            "axisPointer": {"type": "shadow"}
        }
        
        # Dual Y Axis
        option["yAxis"] = [
            {
                "type": "value",
                "name": "Valor",
                "position": "left",
                "axisLine": {"show": True, "lineStyle": {"color": ChartFactory.COLORS[0]}},
                "splitLine": {"show": True, "lineStyle": {"type": "dashed", "color": "#e5e7eb"}}
            },
            {
                "type": "value",
                "name": "Acumulado %",
                "min": 0,
                "max": 100,
                "position": "right",
                "axisLine": {"show": True, "lineStyle": {"color": ChartFactory.COLORS[1]}},
                "axisLabel": {"formatter": "{value} %"},
                "splitLine": {"show": False}
            }
        ]
        
        # Series
        option["series"] = [
            {
                "name": "Valor",
                "type": "bar",
                "data": bar_data,
                "itemStyle": {"color": ChartFactory.COLORS[0]}
            },
            {
                "name": "Acumulado %",
                "type": "line",
                "yAxisIndex": 1, 
                "data": line_data,
                "itemStyle": {"color": ChartFactory.COLORS[1]},
                "symbol": "circle",
                "symbolSize": 6,
                "smooth": True,
                "markLine": {
                    "data": [{"yAxis": 80, "label": {"formatter": "80%"}}],
                    "lineStyle": {"color": "#f43f5e", "type": "dashed"}
                }
            }
        ]
        
        # Currency Formatting
        if currency_meta:
            sym = currency_meta.get('symbol', '')
            if sym:
                option["yAxis"][0]["axisLabel"] = {"formatter": f"{sym} {{value}}"}
                option["tooltip"]["valueFormatter"] = f"(val) => '{sym} ' + val"

        return ChartFactory._sanitize_for_json(option)

    @staticmethod
    def build_funnel_chart(title, data, currency_meta=None):
        """Funnel (Conversión)."""
        # data expected: [{name: 'Stage', value: 100, conversion_rate: 100%}, ...]
        
        clean_data = ChartFactory._normalize_data_polymorphic(data)
        clean_data = [
            row for row in clean_data
            if isinstance(row, dict) and isinstance(row.get('value'), (int, float)) and row.get('value', 0) > 0
        ]
        
        # Ordenar (Funnel debe ser descendente visualmente)
        clean_data.sort(key=lambda x: x['value'], reverse=True)

        # Cap visual para evitar funnels ilegibles en alta cardinalidad
        max_stages = 15
        if len(clean_data) > max_stages:
            head_count = max(max_stages - 1, 1)
            top = clean_data[:head_count]
            tail_sum = float(sum(item.get('value', 0) for item in clean_data[head_count:]))
            if tail_sum > 0:
                top.append({"name": "OTROS", "value": tail_sum})
            clean_data = top
        
        option = ChartFactory._get_base_option(title)
        option['legend']['show'] = len(clean_data) <= 8
        
        # Formateadores
        tooltip_fmt = "{b}: {c} ({d}%)"
        label_fmt = "{b}: {c}"
        
        if currency_meta:
            symbol = currency_meta.get('symbol', '$')
            tooltip_fmt = f"{{b}}: {symbol} {{c}} ({{d}}%)"
            label_fmt = f"{{b}}: {symbol} {{c}}"
            
        option['tooltip']['trigger'] = 'item'
        option['tooltip']['formatter'] = tooltip_fmt
        
        option['series'] = [{
            'name': title,
            'type': 'funnel',
            'left': '10%',
            'top': 28,
            'bottom': 28,
            'width': '80%',
            'min': 0,
            'max': clean_data[0]['value'] if clean_data else 100,
            'minSize': '0%',
            'maxSize': '100%',
            'sort': 'descending',
            'itemStyle': {
                'borderColor': '#fff',
                'borderWidth': 1
            },
            'label': {
                'show': True,
                'position': 'outside',
                'formatter': label_fmt,
                'fontSize': 11,
            },
            'data': clean_data
        }]
        
        return ChartFactory._sanitize_for_json(option)

    @staticmethod
    def build_heatmap_chart(title, data):
        """Heatmap (Correlación/Densidad)."""
        return ChartFactory()._build_heatmap_chart(title, data)
    # (Se mantienen build_bar_chart, build_line_chart, etc. abajo si no fueron reemplazados por create_chart logic)

    @staticmethod
    def build_barplot_legacy_wrapper(title, data):
        return ChartFactory.build_bar_chart(title, data)


    @staticmethod
    def build_gauge_chart(title, value, min_val=0, max_val=100, suffix="%"):
        """Medidor (KPI único)."""
        # Acepta valor directo o dict
        if isinstance(value, dict): _, value = ChartFactory._get_smart_keys(value)
        if isinstance(value, list) and value: _, value = ChartFactory._get_smart_keys(value[0])
        
        try: value = float(value)
        except: value = 0

        option = ChartFactory._get_base_option(title)
        option["series"] = [{
            "type": "gauge",
            "startAngle": 180, "endAngle": 0,
            "min": min_val, "max": max_val,
            "radius": "100%", "center": ["50%", "75%"],
            "progress": {"show": True, "width": 18, "itemStyle": {"color": "#10b981"}},
            "axisLine": {"lineStyle": {"width": 18}},
            "axisTick": {"show": False},
            "detail": {"fontSize": 20, "offsetCenter": [0, "-20%"], "formatter": f"{{value}}{suffix}"},
            "data": [{"value": value, "name": title}]
        }]
        return ChartFactory._sanitize_for_json(option)

    @staticmethod
    def build_dual_axis_chart(title, categories, bar_data, line_data, bar_name="Volumen", line_name="Variación"):
        """
        Gráfico Combinado (Eje Dual): Barras (Izq) + Línea (Der).
        Ideal para comparar Magnitud (Stock) vs Intensidad (Variación %).
        """
        safe_cats = [str(c) for c in categories]
        
        # Limpieza de datos Barras
        safe_bars = []
        if isinstance(bar_data, list):
            safe_bars = [float(x) if pd.notnull(x) else 0 for x in bar_data]
            
        # Limpieza de datos Línea
        safe_line = []
        if isinstance(line_data, list):
            safe_line = [float(x) if pd.notnull(x) else 0 for x in line_data]

        option = ChartFactory._get_base_option(title)
        
        # Configuración de Doble Eje Y
        option["yAxis"] = [
            {
                "type": "value", 
                "name": bar_name,
                "position": "left",
                "axisLine": {"show": True, "lineStyle": {"color": ChartFactory.COLORS[0]}},
                "splitLine": {"show": True, "lineStyle": {"type": "dashed", "color": "#e5e7eb"}}
            },
            {
                "type": "value", 
                "name": line_name,
                "position": "right",
                "axisLine": {"show": True, "lineStyle": {"color": ChartFactory.COLORS[1]}},
                "axisLabel": {"formatter": "{value} %"},
                "splitLine": {"show": False}
            }
        ]
        
        option["xAxis"] = {"type": "category", "data": safe_cats, "axisPointer": {"type": "shadow"}}
        option["tooltip"]["trigger"] = "axis"
        
        option["series"] = [
            {
                "name": bar_name,
                "type": "bar",
                "data": safe_bars,
                "itemStyle": {"color": ChartFactory.COLORS[0], "borderRadius": [4, 4, 0, 0]}
            },
            {
                "name": line_name,
                "type": "line",
                "yAxisIndex": 1, # ¡Clave! Usa el eje derecho
                "data": safe_line,
                "smooth": True,
                "symbolSize": 8,
                "itemStyle": {"color": ChartFactory.COLORS[4]}, # Color de contraste (ej. Rosa/Rojo para variación)
                "label": {"show": True, "position": "top", "formatter": "{c}%"}
            }
        ]
        
        return ChartFactory._sanitize_for_json(option)

    @staticmethod
    def build_gantt_chart(title, data):
        """Gantt (Planificación)."""
        # data = [{'category': 'Lote1', 'start_date': '2021-01-01', 'end_date': '2021-02-01'}]
        if not data: return {"error": "Sin datos Gantt"}
        df = pd.DataFrame(data)
        
        # Normalizar columnas
        col_map = {'inicio': 'start_date', 'fin': 'end_date', 'categoria': 'category', 'tarea': 'category'}
        df = df.rename(columns=col_map)
        
        req = ['category', 'start_date', 'end_date']
        if not all(c in df.columns for c in req): return {"error": "Faltan columnas Gantt"}

        df['start_date'] = pd.to_datetime(df['start_date'])
        df['end_date'] = pd.to_datetime(df['end_date'])
        
        df = df.sort_values(['category', 'start_date'])
        categories = df['category'].unique().tolist()
        
        series_data = []
        for _, row in df.iterrows():
            try:
                cat_idx = categories.index(row['category'])
                start = row['start_date'].timestamp() * 1000
                end = row['end_date'].timestamp() * 1000
                series_data.append([cat_idx, start, end, row['category']])
            except: continue

        option = ChartFactory._get_base_option(title)
        option['tooltip']['formatter'] = "Detalle: <br/>{b}"
        
        option['xAxis'] = {'type': 'time', 'position': 'top'}
        option['yAxis'] = {'type': 'category', 'data': categories}
        
        option['series'] = [{
            'type': 'custom',
            'renderItem': 'renderGanttItem', # Frontend hook
            'itemStyle': {'opacity': 0.8, 'color': '#3b82f6'},
            'data': series_data
        }]
        return ChartFactory._sanitize_for_json(option)
