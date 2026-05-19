# En: backend/app/celery_worker.py

# Importamos la instancia central de Celery para que el worker la reconozca al arrancar.
from .celery_app import celery_app