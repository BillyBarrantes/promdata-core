# Usar una imagen oficial de Python
FROM python:3.11-slim

# Establecer el directorio de trabajo dentro del contenedor
WORKDIR /code

# Copiar el archivo de dependencias
COPY ./requirements.txt /code/requirements.txt

# Instalar las dependencias
RUN pip install --no-cache-dir --upgrade -r /code/requirements.txt

# Copiar todo el código de la aplicación
COPY ./app /code/app

# Arrancar la API leyendo el puerto del entorno (Cloud Run inyecta 8080, local usa 8000)
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}