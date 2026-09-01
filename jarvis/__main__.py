"""Permite ejecutar `python -m jarvis`."""

from .main import run, salir_del_proceso

if __name__ == "__main__":
    # `salir_del_proceso` en vez de `sys.exit`: ver su docstring — con
    # `sys.exit` el intérprete se queda esperando a hilos no daemon de
    # terceros que nadie va a despertar.
    salir_del_proceso(run())
