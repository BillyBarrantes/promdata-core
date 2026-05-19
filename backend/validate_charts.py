import sys
import os
import json
import pandas as pd
from datetime import datetime

sys.path.append(os.path.join(os.getcwd(), 'app'))

try:
    from app.services.chart_factory import ChartFactory
except ImportError:
    sys.path.append(os.getcwd())
    from app.services.chart_factory import ChartFactory

def test_chart_factory():
    print("Testing ChartFactory...")
    
    # Test 2: Stacked Bar (New Contract)
    categories = ["Jun", "Jul"]
    series_data = {"Lote A": [10, 20], "Lote B": [5, 15]}
    option_bar = ChartFactory.build_stacked_bar_chart("Stock Evolution", categories, series_data)
    if len(option_bar['series']) == 2 and option_bar['xAxis']['data'] == categories:
        print("PASS: Stacked Bar build successful with dict.")
    else:
        print("FAIL: Stacked Bar build mismatch.")

    # Test 3: Stacked Bar (Fallback List)
    option_bar_list = ChartFactory.build_stacked_bar_chart("Simple Bar", categories, [10, 20])
    if option_bar_list['series'][0]['name'] == "General":
        print("PASS: Stacked Bar handled fallback list.")
    else:
        print("FAIL: Stacked Bar failed list fallback.")

    # Test 4: Pie Chart (Contract)
    pie_data = [{"name": "Risk A", "value": 50}, {"name": "Risk B", "value": 30}]
    option_pie = ChartFactory.build_pie_chart("Risk Mix", pie_data)
    if len(option_pie['series'][0]['data']) == 2:
        print("PASS: Pie Chart build successful.")
    else:
        print("FAIL: Pie Chart build mismatch.")

    # Test 5: Bar Chart (Horizontal Ranking)
    bar_data = [{"name": "SKU 1", "value": 100}, {"name": "SKU 2", "value": 150}]
    option_h_bar = ChartFactory.build_bar_chart("Ranking Title", bar_data, horizontal=True)
    if option_h_bar['yAxis']['type'] == 'category' and len(option_h_bar['series'][0]['data']) == 2:
        print("PASS: Horizontal Bar Chart build successful.")
    else:
        print("FAIL: Horizontal Bar Chart build mismatch.")

if __name__ == "__main__":
    test_chart_factory()
