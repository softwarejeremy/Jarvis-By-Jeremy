"""Gasto acumulado, por día.

`core.coste_usd` y el evento `COST_UPDATE` sólo cuentan lo gastado en esta
sesión del proceso: el SDK manda el **acumulado de la sesión actual**, no el
incremento del turno. `max_budget_usd` corta ahí, por sesión — así que diez
sesiones de $2 no avisan de nada, aunque ese mismo día se hayan gastado $20.

Aquí se lleva la cuenta por día, en `~/.jarvis/gasto.json`. Para eso hace
falta convertir el acumulado de sesión en un incremento: se recuerda el
último total visto de *esta* sesión, y sólo se suma la diferencia — sumar el
acumulado tal cual doblaría el gasto en cada turno.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..events import Event, EventBus


class Gasto:
    """Lee y escribe el registro de gasto diario."""

    def __init__(self, archivo: Path) -> None:
        self.archivo = Path(archivo)
        # El total de ESTA sesión la última vez que se vio: la diferencia con
        # el siguiente aviso es lo que de verdad se ha gastado desde entonces.
        self._ultimo_total_sesion = 0.0

    def escuchar(self, bus: EventBus) -> None:
        """Se suscribe al bus: cada `COST_UPDATE` suma su incremento a hoy."""

        def _al_evento(evento: Event) -> None:
            from ..events import EventType

            if evento.type is not EventType.COST_UPDATE:
                return
            total_sesion = float(evento.data.get("total_usd") or 0.0)
            incremento = total_sesion - self._ultimo_total_sesion
            self._ultimo_total_sesion = total_sesion
            if incremento > 0:
                self._sumar_a_hoy(incremento)

        bus.on(_al_evento)

    def _leer(self) -> dict[str, float]:
        if not self.archivo.is_file():
            return {}
        try:
            return json.loads(self.archivo.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def _escribir(self, datos: dict[str, float]) -> None:
        self.archivo.parent.mkdir(parents=True, exist_ok=True)
        self.archivo.write_text(json.dumps(datos, indent=2, sort_keys=True), encoding="utf-8")

    def _sumar_a_hoy(self, cantidad: float) -> None:
        datos = self._leer()
        hoy = date.today().isoformat()
        datos[hoy] = round(datos.get(hoy, 0.0) + cantidad, 6)
        self._escribir(datos)

    # ── lectura ─────────────────────────────────────────────────────────
    def hoy(self) -> float:
        return self._leer().get(date.today().isoformat(), 0.0)

    def mes_actual(self) -> float:
        prefijo = date.today().strftime("%Y-%m")
        return sum(v for dia, v in self._leer().items() if dia.startswith(prefijo))

    def ultimos_dias(self, n: int) -> dict[str, float]:
        """Los últimos `n` días con gasto registrado, más reciente primero."""
        datos = self._leer()
        return dict(sorted(datos.items(), reverse=True)[:n])
