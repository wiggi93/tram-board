# tram-board

A self-contained departure board for German Stadtbahn / tram stops.

- **Web admin** at `http://<host>:8080` to set the station by name (`Kerstingstraße Hannover`, `Schlossplatz Stuttgart`, `Neumarkt Köln`, …)
- **Live HTML preview** that mirrors the on-device LED layout
- **Free-text mode** that replaces the tram board with a scrolling message
- **JSON API** so anything else can read the current state
- **Optional RGB LED matrix renderer** for Raspberry Pi
- **Pre-built multi-arch Docker images** — no source needed on the host

Data comes from the federated [efa.vrr.de](https://efa.vrr.de) EFA endpoint, which covers most German tram networks (Hannover, Stuttgart, Karlsruhe, München, Köln, Düsseldorf, Berlin, Hamburg, …) without per-city configuration.

## Requirements

- Docker 20+ with the Compose plugin
- For the LED variant: a Raspberry Pi (3, 4, 5) and an HUB75-compatible RGB LED matrix panel

---

## Quick start (web only, any host)

```bash
mkdir -p data && chmod 777 data
docker run -d --name tram-board \
  -p 8080:8080 -v "$(pwd)/data:/app/data" \
  --restart unless-stopped \
  profdrdisco/tram-board:latest
```

Open `http://localhost:8080`, type a station name, hit Apply. Configuration persists in `./data/config.json`.

---

## Deploy on a Raspberry Pi with LED

The Pi runs the **web service in Docker** and the **LED renderer as a host systemd service**. Docker on Raspberry Pi OS doesn't allow real-time scheduling for cgroup-bound processes, which the rgbmatrix C library needs for stable timing — so the renderer has to live outside the container. Both pieces share state via the JSON API.

### One-time host prep

The HUB75 panel's timing-critical PWM clashes with the Pi's onboard sound module. Disable it once:

```bash
sudo sed -i '/^dtparam=audio=on/c\dtparam=audio=off' /boot/firmware/config.txt
echo "blacklist snd_bcm2835" | sudo tee /etc/modprobe.d/blacklist-rgb-matrix.conf
sudo update-initramfs -u && sudo reboot
```

### Bring up the web service (Docker)

```bash
mkdir -p ~/tram-board/data && chmod 777 ~/tram-board/data && cd ~/tram-board
curl -O https://raw.githubusercontent.com/wiggi93/tram-board/master/docker-compose.pi.yml
docker compose -f docker-compose.pi.yml up -d
```

Open `http://<pi-ip>:8080` from any device on the LAN to set the station. The admin UI works at this point; the panel comes alive after the next step.

### Install the host LED driver (one command)

```bash
curl -fsSL https://raw.githubusercontent.com/wiggi93/tram-board/master/host/install.sh \
  | sudo bash
```

This compiles `rpi-rgb-led-matrix` (only if it isn't already installed), copies [`host/led-driver.py`](host/led-driver.py) and the fonts to `/opt/tram-board-led/`, and registers a systemd service. The driver polls `http://127.0.0.1:8080/api/state` four times a second and renders to the panel.

Both the container and the LED driver auto-restart on boot. Configuration persists in `~/tram-board/data/config.json`.

### Updating

```bash
# Container (web service)
docker compose -f ~/tram-board/docker-compose.pi.yml pull
docker compose -f ~/tram-board/docker-compose.pi.yml up -d

# Host LED driver (only if you bumped led-driver.py)
sudo curl -fsSL https://raw.githubusercontent.com/wiggi93/tram-board/master/host/led-driver.py \
  -o /opt/tram-board-led/led-driver.py
sudo systemctl restart tram-board-led
```

GitHub Actions republishes the container image on every push to `master`, so the first block always pulls the latest.

### Tuning the panel

Panel options are environment variables on the **systemd service**, not the container. Edit them with:

```bash
sudo systemctl edit tram-board-led
# add overrides under [Service], e.g.:
#   [Service]
#   Environment="LED_BRIGHTNESS=40"
#   Environment="LED_GPIO_MAPPING=adafruit-hat"
sudo systemctl restart tram-board-led
```

Available variables:

| Variable | Default | Notes |
|---|---|---|
| `LED_ROWS` | `32` | Panel row count |
| `LED_COLS` | `64` | Panel column count |
| `LED_CHAIN` | `1` | Daisy-chained panels |
| `LED_PARALLEL` | `1` | Parallel chains |
| `LED_BRIGHTNESS` | `70` | 1–100 |
| `LED_PWM_BITS` | `11` | 1–11 |
| `LED_SLOWDOWN_GPIO` | `2` | 0–4; raise if you see flicker |
| `LED_GPIO_MAPPING` | *(unset)* | `adafruit-hat` if using the HAT, otherwise omit |
| `LED_NO_HARDWARE_PULSE` | `0` | Set to `1` if you can't disable the audio module |

**Why `chmod 777` on the data dir:** the bind mount must be writable by the container's effective uid, which depends on your Docker install (rootful, rootless, or userns-remapped). World-writable is the simplest portable answer for a single-user appliance.

---

## Compose files

Three files for three use cases. Pick one per host.

| File | Image | Use |
|---|---|---|
| [`docker-compose.yml`](docker-compose.yml) | builds locally from `./Dockerfile` | Hacking on the source |
| [`docker-compose.deploy.yml`](docker-compose.deploy.yml) | `profdrdisco/tram-board:latest` | Web service on any non-Pi host |
| [`docker-compose.pi.yml`](docker-compose.pi.yml) | `profdrdisco/tram-board:latest` | Web service on a Pi (paired with the host LED driver) |

The Pi compose differs from the deploy compose only in `network_mode: host`, so the host-side LED driver can reach the API at `127.0.0.1:8080`.

---

## Web admin & API

| Route | Purpose |
|---|---|
| `GET /` | Admin page: Tram / Text tab toggle and live preview |
| `GET /api/state` | Current departures, station info, mode, free text, last update |
| `POST /api/station` | `{"query": "Stop City"}` — resolves and switches station |
| `POST /api/mode` | `{"mode": "tram"\|"text"}` — switches what the panel shows |
| `POST /api/text` | `{"text": "..."}` — sets the message for text mode |

The browser polls `/api/state` every 250 ms, so a change made on a phone reflects on the laptop within a frame and on the LED on the next render tick.

---

## Layout

### Tram mode

22 chars wide on the debug preview; the LED panel uses the same proportions in pixels.

```
┌──────────────────────┐
│10 Ahlem             3│
│17 Rethen            7│
│10 Ahlem            33│
│17 Rethen           37│
│       10:42:07       │
└──────────────────────┘
```

Columns: 2 (line number on a blue badge) + 10 (` ` + 9 destination chars, scrolling for long names) + 10 (right-aligned minutes). Bottom row centres a live clock.

Colours: blue badge, orange destination, white minutes (yellow flash for 1.5 s on change), grey clock.

### Text mode

Full-width scrolling text in a 7×14 BDF font, vertically centred, looping with a one-screen trailing gap. Browser preview matches the panel.

---

## Project layout

```
tram-board/
├── app.py                      # Flask app + entry point
├── tram.py                     # EFA client, BoardState, fetch loop
├── config.py                   # JSON config persistence
├── led.py                      # Optional LED renderer (lazy-imports rgbmatrix)
├── templates/index.html        # Admin page (vanilla HTML/CSS/JS)
├── fonts/{4x6,7x14}.bdf        # Tram-mode and text-mode fonts
├── Dockerfile                  # Single multi-stage build, WITH_LED toggles rgbmatrix
├── docker-compose*.yml         # Three compose files (see above)
├── host/                       # Pi LED driver (runs outside Docker)
│   ├── led-driver.py
│   ├── tram-board-led.service
│   └── install.sh
└── .github/workflows/docker.yml # CI: publishes both image tags on push to master
```

---

## Notes

- The admin page has **no authentication**. Run it on a trusted network or behind a reverse proxy.
- The EFA stop-finder returns the best-matching stop, which isn't always the literal name (e.g. `Hauptbahnhof Karlsruhe` resolves to `Hauptbahnhof Süd`). The resolved name is shown so you can refine the query.
- Only Stadtbahn / Straßenbahn products (`product.class` 3 or 4) are shown. Buses, S-Bahn, and regional trains are filtered out. Change `TRAM_CLASSES` in [`tram.py`](tram.py) to widen this.
- At busy hubs (4+ trams within 5 min) the board de-duplicates by `(line, destination)` and strips the redundant city prefix. Quieter stops render every entry as-is.

---

## Building and publishing your own image

Skip this section unless you've forked the repo. The pre-built images on Docker Hub are CI-published and stay current.

### Manual multi-arch build

```bash
docker login
docker buildx create --use --name tram-builder

# Web-only (runs anywhere)
docker buildx build --platform linux/amd64,linux/arm64,linux/arm/v7 \
  -t <your-dockerhub-user>/tram-board:latest --build-arg WITH_LED=0 --push .

# LED-enabled (Pi only; slow under QEMU)
docker buildx build --platform linux/arm64,linux/arm/v7 \
  -t <your-dockerhub-user>/tram-board:led --build-arg WITH_LED=1 --push .
```

### Or let GitHub Actions do it

[`.github/workflows/docker.yml`](.github/workflows/docker.yml) publishes both tags on every push to `master`. In your fork: add `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` (scope: *Read, Write, Delete*) at **Settings → Secrets and variables → Actions**, then update the `IMAGE` env in the workflow and the default `image:` in the compose files to point at your namespace.
