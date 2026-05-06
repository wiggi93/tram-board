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

### Or: let GitHub Actions do it on every push to `main`

[`.github/workflows/docker.yml`](.github/workflows/docker.yml) builds the multi-arch image and pushes to Docker Hub automatically on every commit to `main`. Each build is tagged both `latest` and `sha-<short>` for rollbacks. To enable, add two repository secrets at **Settings → Secrets and variables → Actions**:

- `DOCKERHUB_USERNAME` — your Docker Hub username
- `DOCKERHUB_TOKEN` — an access token from [Docker Hub → Account Settings → Personal Access Tokens](https://app.docker.com/settings/personal-access-tokens) (scope: *Read, Write, Delete*)

After that, every push to `main` triggers a build; updating the Pi is just `docker compose -f docker-compose.deploy.yml pull && ... up -d`.

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

There are **two pre-built images**, both published by the GHA pipeline on every push to `main`:

| Tag | Architectures | Contents |
|---|---|---|
| `profdrdisco/tram-board:latest` | `amd64`, `arm64`, `arm/v7` | Web admin only — runs anywhere |
| `profdrdisco/tram-board:led` | `arm64`, `arm/v7` | Web admin **+** the `rpi-rgb-led-matrix` C library compiled in — Pi only |

### Running the LED variant on the Pi

```bash
mkdir -p ~/tram-board/data && chmod 777 ~/tram-board/data && cd ~/tram-board
curl -O https://raw.githubusercontent.com/wiggi93/tram-board/master/docker-compose.pi.yml
docker compose -f docker-compose.pi.yml up -d
```

The `chmod 777 ~/tram-board/data` is needed because the bind-mounted directory must be writable by the container's effective uid. Different Docker installs (rootful, rootless, userns-remapped) end up with different uids inside the container; world-writable is the simplest portable answer for a single-user appliance.

That's the entire setup — no source on the Pi, no Python install, nothing on the host except Docker. The same web admin works at `http://<pi>:8080` and the LED panel updates as you change stations.

`docker-compose.pi.yml` runs the container with `privileged: true` (for `/dev/mem` direct PWM access) and exposes env vars for the common panel options:

```
LED_ROWS=32  LED_COLS=64  LED_CHAIN=1  LED_PARALLEL=1
LED_BRIGHTNESS=70  LED_PWM_BITS=11  LED_SLOWDOWN_GPIO=2
LED_GPIO_MAPPING=adafruit-hat        # only if you use a HAT
```

### Running locally (no LED, no Pi)

```bash
sudo python app.py --led    # only works if you've installed rpi-rgb-led-matrix on the host
# or, just the web service:
python app.py
```

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
