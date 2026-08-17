"""Instancia compartida del limitador de peticiones (slowapi).

Backend en memoria — no hay Redis en este proyecto y no hace falta para una
sola instancia. Vive en su propio módulo (no en cada router) porque
`app/main.py` necesita la misma instancia para registrar el middleware y el
exception handler.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
