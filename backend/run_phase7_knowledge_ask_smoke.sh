#!/bin/zsh
set -euo pipefail

set -a
source /Users/billy/Desarrollos/PromData/backend/.env
set +a

docker run --rm \
  -e SUPABASE_URL="$SUPABASE_URL" \
  -e SUPABASE_KEY="$SUPABASE_KEY" \
  -e SUPABASE_ANON_KEY="$SUPABASE_ANON_KEY" \
  -e SUPABASE_SERVICE_ROLE_KEY="$SUPABASE_KEY" \
  -e GEMINI_API_KEY="$GEMINI_API_KEY" \
  -e CELERY_BROKER_URL="redis://host.docker.internal:6379/0" \
  -e CELERY_RESULT_BACKEND="redis://host.docker.internal:6379/0" \
  -e PYTHONPATH="/code" \
  -v /Users/billy/Desarrollos/PromData/backend/app:/code/app \
  -v /Users/billy/Desarrollos/PromData/backend/test_phase7_knowledge_ask_smoke.py:/code/test_phase7_knowledge_ask_smoke.py \
  promdata-backend-smoke \
  python /code/test_phase7_knowledge_ask_smoke.py
