# Sports Predictions

Sistema de análisis de fútbol: probabilidades por partido, estadísticas, detección de value bets y backtesting.

```
sports-predictions/
├── prediction-engine/   Python: datos, modelos, backtesting, API
│   ├── src/prediction_engine/
│   │   ├── cli.py              comandos (uv run predict ...)
│   │   ├── config.py           lee el archivo .env
│   │   ├── db.py               conexión y creación de tablas
│   │   ├── leagues.py          catálogo de ligas
│   │   └── ingestion/          descarga y guardado de datos
│   ├── sql/schema.sql          tablas de la base de datos
│   └── tests/
├── dashboard/           Next.js (fase posterior)
├── docker-compose.yml   Postgres local
└── .env.example         plantilla de claves
```

## Instalación (Mac) — solo la primera vez

1. **uv** (administrador de proyectos Python):
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```
   Cierra y vuelve a abrir la terminal. Verifica con `uv --version`.

2. **Docker Desktop**: ábrelo y espera a que diga que está corriendo.

3. **Archivo de claves**:
   ```bash
   cd ~/Desktop/Trabajos/sports-predictions
   cp .env.example .env
   ```

## Uso diario

Todo desde la carpeta del proyecto:

```bash
cd ~/Desktop/Trabajos/sports-predictions

# 1. Encender la base de datos
docker compose up -d

# 2. Entrar al motor Python
cd prediction-engine
uv sync                      # instala librerías (rápido si ya están)

# 3. Comandos
uv run predict check-db      # probar conexión
uv run predict init-db       # crear tablas (se puede repetir sin problema)
uv run predict leagues       # ver ligas disponibles
uv run predict ingest --leagues SP1 --from-season 2024   # prueba pequeña
uv run predict ingest --tier 1                            # todas las ligas nivel 1 desde 2015
uv run predict ingest --tier 2                            # Brasil, Argentina, etc.

uv run predict backtest --leagues SP1                     # probar el modelo contra cuotas reales
uv run predict backtest --tier 1 --bookmaker pinnacle     # todas, contra Pinnacle
uv run predict match --league SP1 --home Barcelona --away "Real Madrid"

uv run pytest                # correr pruebas
```

### Cómo leer el backtest

- **model_logloss vs market_logloss**: error del modelo vs. el de las casas. Más bajo = mejor.
  Si el del modelo es más alto, la casa sabe más que el modelo.
- **bets / hit_rate / avg_odds**: cuántas apuestas de valor encontró, % acertadas y cuota promedio.
- **profit_units / roi**: ganancia apostando 1 unidad fija en cada una. ROI +3% = gana 3 de cada 100 apostados.
- El detalle queda en `prediction-engine/data/backtests/` (se abre con Excel).

Para apagar la base de datos: `docker compose down` (los datos se conservan).
Para borrar todo y empezar de cero: `docker compose down -v`.

## Ver la base de datos

Con TablePlus, DBeaver o similar:

| Campo    | Valor         |
|----------|---------------|
| Host     | localhost     |
| Puerto   | 5433          |
| Usuario  | predictions   |
| Clave    | predictions   |
| Base     | predictions   |

## Fuentes de datos

- **football-data.co.uk**: resultados y cuotas históricas (gratis). Datos: "football-data.co.uk".
- **API-Football**: Colombia, Arabia Saudita, próximos partidos (fase siguiente).
- **API de cuotas**: value bets y surebets (fase siguiente).

> Herramienta de análisis estadístico. No garantiza resultados.
