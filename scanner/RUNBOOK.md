# Ejecución programada del escáner

Panel: https://claude.ai/artifact/1kdfMqoSP1dSTCZTRxMT8L

Pasos de cada ejecución (los sigue la tarea programada):

1. Volcar el estado anterior del panel:
   `ArtifactData list` de la colección `markets` con `out_dir=<tmp>/prev`.
   Anota la `version` de cada documento.
2. Ejecutar `python3 scanner/scan.py --prev <tmp>/prev --out <tmp>/out`.
3. Escribir en un solo `ArtifactData batch`:
   - `set markets/<ID>` con `file_path=<tmp>/out/markets/<ID>.json` e `if_version` de la versión leída
     (sin `if_version` si el documento no existía).
   - `set meta/status` con `file_path=<tmp>/out/status.json` (leer antes su versión con `get`).
4. Si `<tmp>/out/alerts.json` no está vacío, el resumen final de la ejecución es el
   contenido de `alerts.md` (eso dispara la notificación). Si está vacío, terminar con
   "Sin avisos nuevos".

No se hace commit de los datos generados.
