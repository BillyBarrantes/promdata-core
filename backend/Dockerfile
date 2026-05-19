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

# El comando por defecto para correr la API (esto puede ser sobreescrito en docker-compose)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]