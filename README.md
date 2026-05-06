# tram-board

A self-contained departure board for German Stadtbahn / tram stops, with:

- A **web admin UI** to set the stop by name (`Kerstingstraße Hannover`, `Schlossplatz Stuttgart`, …)
- A **live HTML preview** that mirrors the on-device LED layout
- A **JSON API** for current departures
- An **optional RGB LED matrix renderer** for Raspberry Pi
- A **Docker image** for one-command deployment

Data comes from the federated [efa.de](https://efa.de) EFA endpoint, which covers most German tram networks without per-city configuration.

---

## Quick start (Docker)

```bash
docker compose up --build -d
open http://localhost:8080
```

Set a station in the form and the preview updates within a second. Configuration is persisted to `./data/config.json` on the host.

## Quick start (local Python)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python app.py
# → http://localhost:8080
```

---

## Web admin

| Route | Description |
|---|---|
| `GET /` | Admin page with input box and live preview |
| `GET /api/state` | Current departures, station info, last update time |
| `POST /api/station` | Body `{"query": "Stop City"}` — resolves and switches |

The preview polls `/api/state` every 500 ms and renders the same 22-column frame the LED uses, with the same colours (blue badge, orange destination, white time, yellow flash on change).

## Deploy to a Raspberry Pi (or any Docker host)

The clean path is: **build a multi-arch image on your Mac → push to Docker Hub → `docker compose pull` on the Pi**.

### One-time on your Mac

```bash
docker login
docker buildx create --use --name tram-builder    # one-time
```

### Build & push (run from the project root)

```bash
docker buildx build \
  --platform linux/amd64,linux/arm64,linux/arm/v7 \
  -t <your-dockerhub-user>/tram-board:latest \
  --push .
```

Pi 4 / 5 on 64-bit Raspberry Pi OS uses `arm64`. Pi 3 or any 32-bit OS uses `arm/v7`. Including `amd64` lets you also run the same image on your Mac/Linux box without rebuilding.

### One-time on the Pi

```bash
ssh pi@raspberrypi.local
sudo apt-get install -y docker.io docker-compose-plugin   # if not already
sudo usermod -aG docker $USER && newgrp docker            # so you don't need sudo

mkdir -p ~/tram-board/data && cd ~/tram-board
curl -O https://raw.githubusercontent.com/<you>/tram-board/main/docker-compose.deploy.yml

# first start
IMAGE=<your-dockerhub-user>/tram-board:latest \
  docker compose -f docker-compose.deploy.yml up -d
```

The container restarts on reboot (`restart: unless-stopped`) and persists configuration to `~/tram-board/data/config.json` on the host. Open `http://raspberrypi.local:8080` from any browser on the LAN to set the station.

### Updating later

```bash
docker compose -f docker-compose.deploy.yml pull
docker compose -f docker-compose.deploy.yml up -d
```

That's it — no rebuild on the Pi, no source code on the Pi.

---

## LED hardware mode

On a Raspberry Pi with [rpi-rgb-led-matrix](https://github.com/hzeller/rpi-rgb-led-matrix) installed:

```bash
sudo python app.py --led
```

The LED renderer runs in a background thread and reads the same `BoardState` the web UI does, so changing the station from a phone instantly updates the panel.

> Hardware mode in Docker requires `--privileged` and host GPIO access. The image works as-is for the web service; running the LED inside a container is left as an exercise.

---

## Layout (debug frame)

```
┌──────────────────────┐
│10 Ahlem             3│
│17 Rethen            7│
│10 Ahlem            33│
│17 Rethen           37│
│       10:42:07       │
└──────────────────────┘
```

22 chars wide: 2 (line badge) + 10 (` ` + 9-char destination, scrolling) + 10 (right-aligned minutes).

## Project layout

```
tram-board/
├── app.py            # Flask app + entry point
├── tram.py           # EFA client, BoardState, fetch loop
├── config.py         # JSON config persistence
├── led.py            # Optional LED renderer (lazy-imports rgbmatrix)
├── templates/
│   └── index.html    # Admin page with live preview
├── fonts/4x6.bdf     # Font for LED rendering
├── data/             # config.json lives here (Docker volume)
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## Configuration

The fetch loop calls back into `config.load()` every iteration, so changing the station via the web UI takes effect on the next 30-second tick without a restart.

| Env var | Default | Description |
|---|---|---|
| `PORT` | `8080` | HTTP port |
| `HOST` | `0.0.0.0` | Bind address |
| `TRAM_BOARD_CONFIG` | `data/config.json` | Path to the persisted config |
| `LED_BRIGHTNESS` | `70` | LED panel brightness (1–100) |
| `LED_SLOWDOWN_GPIO` | `2` | GPIO write slowdown (0–4) |
| `LED_PWM_BITS` | `11` | PWM bits |

## Notes

- The admin page has **no authentication**. Run it on a trusted network or front it with a reverse proxy that provides auth.
- The EFA stop-finder returns the highest-quality stop match, which isn't always the literal name (e.g. `Hauptbahnhof Karlsruhe` resolves to `Hauptbahnhof Süd`). The resolved name is shown next to the station so you can refine the query.
- Only Stadtbahn / tram products (`product.class == 3`) are shown. Buses, S-Bahn, regional trains are filtered out. Change `STADTBAHN_CLASS` in `tram.py` to widen this.
