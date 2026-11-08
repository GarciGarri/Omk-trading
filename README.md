# OMK Señales

Avisos de compra y venta para BTC y otros activos a partir de reglas técnicas fijas,
con historial y seguimiento automático del resultado de cada aviso.

- `scanner/scan.py`: descarga velas (Binance para cripto en 4 h, Yahoo Finance en diario
  para índices, oro, acciones y EUR/USD), calcula EMA 20/50/200, RSI 14 y ATR 14,
  evalúa las reglas y decide si cada señal tocó antes el objetivo o el stop.
- `scanner/config.json`: activos, temporalidad, gestión de riesgo y fecha de puesta en marcha.
- `app/senales.html`: el panel publicado como artefacto (avisos abiertos, gráficos,
  estadística por regla, historial y diario de operaciones).
- `scanner/RUNBOOK.md`: lo que hace la tarea programada en cada pasada.

## Reglas

| Regla | Compra | Venta |
|---|---|---|
| Cruce EMA 20/50 | EMA 20 cruza al alza la EMA 50 y precio > EMA 200 | Al revés, precio < EMA 200 |
| Retroceso RSI | Precio > EMA 200 y RSI cruza 35 al alza | Precio < EMA 200 y RSI cruza 65 a la baja |
| Ruptura 20 velas | Cierre > máximo de 20 velas, volumen > 1,5× media, EMA 50 > EMA 200 | Simétrico |

Stop = 1,5 × ATR, objetivo = 3 × ATR (2R), caducidad 30 velas. Si una vela toca stop y
objetivo a la vez se cuenta como pérdida.

Esto es una herramienta de apoyo, no asesoramiento financiero.
