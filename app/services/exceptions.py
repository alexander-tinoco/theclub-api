"""Excepciones compartidas entre servicios."""


class DataIntegrityError(Exception):
    """Un invariante que debería sostenerse siempre no se cumplió (p. ej. un
    usuario autenticado sin wallet o sin seed pair activo — ambos se crean en
    el registro, en la Fase 4 y la Fase 5). Nunca debería pasar en
    circunstancias normales: si pasa, es un bug en otra parte del sistema, no
    algo que el cliente pueda provocar. Se usa en vez de `assert` porque
    Python descarta los `assert` con `-O`/`PYTHONOPTIMIZE` — un invariante de
    negocio no debería depender de cómo se invoque el intérprete.
    """
