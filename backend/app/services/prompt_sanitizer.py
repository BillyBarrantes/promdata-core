import re

INJECTION_PATTERNS: list[re.Pattern] = [
    re.compile(r'(ignore|ignora|olvida)\s+.*(instructions|instrucciones|directives|directrices|commands|rules|prompts|reglas)', re.IGNORECASE),
    re.compile(r'(forget|disregard|discard|override|bypass|skip|olvida|ignora|salta)\s+.*(previous|above|prior|anteriores|previas)', re.IGNORECASE),
    re.compile(r'(you\s+are\s+now|ahora\s+eres|eres\s+ahora|act\s+as\s+if)', re.IGNORECASE),
    re.compile(r'(system|hidden|secret|internal)\s*(instruction|prompt|command|rule|instrucción|directriz|secreto)', re.IGNORECASE),
    re.compile(r'(reveal|revela|muestra)\s+.*(system|hidden|secret|internal|secreto|oculto)', re.IGNORECASE),
    re.compile(r'(output|print|show|display|return|write|imprime|muestra)\s+.*(system|prompt|instruction|hidden|secret)', re.IGNORECASE),
    re.compile(r'\b(DAN|do\s+anything\s+now|jailbreak|prompt\s+injection)\b', re.IGNORECASE),
    re.compile(r'(role[\s-]?play|persona|character\s+switch|cambio\s+de\s+personaje)', re.IGNORECASE),
    re.compile(r'(simulate|pretend|imagine|simula|finge|imagina)\s+(you\s+are|that\s+you\s+are|being|que\s+eres|ser)', re.IGNORECASE),
    re.compile(r'no\s+(rules?|limits?|boundaries?|restrictions?|constraints?|reglas|límites|restricciones)', re.IGNORECASE),
    re.compile(r'(hacked|pwned|cracked|compromised|jailbroken|hackeado|comprometido)', re.IGNORECASE),
    re.compile(r'(access|read|write|modify|delete|accede|lee|escribe|borra)\s+.*(code|files?|memory|database|código|archivos|memoria|base\s+de\s+datos)', re.IGNORECASE),
    re.compile(r'(reset|restart|reload|initialize|reinicia|reinicie)\s+.*(system|conversation|session|sistema|conversación|sesión)', re.IGNORECASE),
    re.compile(r'(nueva\s+)?(conversación|sesión|chat|turno)\s*[:]\s*\d+', re.IGNORECASE) if False else None,
]

INJECTION_PATTERNS = [p for p in INJECTION_PATTERNS if p is not None]

PROMPT_MAX_LENGTH = 5000


def contains_html_tag(text: str) -> bool:
    return bool(re.search(r'<[a-zA-Z\/][^>]*>', text))


def contains_url(text: str) -> bool:
    return bool(re.search(r'https?://[^\s]+', text))


def contains_base64_or_hex(text: str) -> bool:
    base64 = bool(re.search(r'[A-Za-z0-9+/]{40,}={0,2}', text))
    hex_seq = bool(re.search(r'(?:[0-9a-fA-F]{2}){20,}', text))
    return base64 or hex_seq


def detect_prompt_injection(text: str) -> str | None:
    if not text:
        return None

    upper_count = sum(1 for c in text if c.isupper())
    if len(text) > 100 and upper_count / len(text) > 0.8:
        return "El prompt contiene un porcentaje anómalo de mayúsculas (posible inyección)"

    for pattern in INJECTION_PATTERNS:
        if pattern.search(text):
            return "El prompt contiene patrones de inyección. Por favor, reformula tu solicitud."

    if contains_html_tag(text) and contains_url(text):
        return "El prompt contiene HTML y URLs simultáneamente (posible intento de XSS)."

    if contains_base64_or_hex(text):
        return "El prompt contiene datos codificados (base64/hex) no permitidos."

    return None
