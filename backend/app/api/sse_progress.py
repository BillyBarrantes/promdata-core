# En: backend/app/api/sse_progress.py
"""
SSE Endpoint para streaming en tiempo real del progreso de las tareas (Pub/Sub).
"""
import asyncio
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from app.core.redis_client import get_pubsub_client
from app.core.config import settings

router = APIRouter()

async def sse_generator(request: Request, task_id: str):
    """
    Generador SSE que se suscribe al canal de Redis de una task
    y emite eventos al frontend.
    """
    pubsub = get_pubsub_client()
    if not pubsub:
        yield f"event: error\ndata: {{\"error\": \"Redis no disponible\"}}\n\n"
        return

    channel = f"{getattr(settings, 'TASK_PROGRESS_CHANNEL_PREFIX', 'task_progress')}:{task_id}"
    pubsub.subscribe(channel)

    try:
        # Timeout safety
        for _ in range(150): # Máximo 15 segundos (150 iteraciones x 0.1s)
            if await request.is_disconnected():
                break
                
            message = pubsub.get_message(ignore_subscribe_messages=True)
            if message and message['type'] == 'message':
                data = message['data']
                if isinstance(data, bytes):
                    data = data.decode('utf-8')
                
                yield f"data: {data}\n\n"
                
                # Check for termination events
                if (
                    '"status": "success"' in data
                    or '"status": "completed"' in data
                    or '"status": "failed"' in data
                    or '"status": "timeout"' in data
                ):
                    break
            
            await asyncio.sleep(0.1)
    finally:
        pubsub.unsubscribe(channel)
        pubsub.close()

@router.get("/tasks/{task_id}/stream", tags=["Tasks"])
async def stream_task_progress(task_id: str, request: Request):
    """
    Endpoint de Server-Sent Events (SSE) para recibir el progreso de una tarea
    en tiempo real a través de Redis Pub/Sub.
    """
    # Graceful degradation si pubsub no está activo
    if not get_pubsub_client():
        raise HTTPException(status_code=503, detail="Streaming de progreso no disponible actualmente")
        
    return StreamingResponse(
        sse_generator(request, task_id),
        media_type="text/event-stream"
    )
