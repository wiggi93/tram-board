# tram-board

A self-contained departure board for German Stadtbahn / tram stops.

- A **web admin UI** at `http://<host>:8080` to set the station by name (`Kerstingstraße Hannover`, `Schlossplatz Stuttgart`, `Neumarkt Köln`, …)
- A **live HTML preview** that mirrors the on-device LED layout
- A **free-text mode** that replaces the tram board with a scrolling message
- A **JSON API** so anything else can read the current state
- An **optional RGB LED matrix renderer** for Raspberry Pi
- Pre-built **multi-arch Docker images** on Docker Hub — no source needed on the host

Data comes from the federated [efa.vrr.de](https://efa.vrr.de) EFA endpoint, which covers most German tram networks (Hannover, Stuttgart, Karlsruhe, München, Köln, Düsseldorf, Berlin, Hamburg, …) without per-city configuration.

---

## Quick start

Pick whichever fits your machine. Both reach the same admin UI at `http://localhost:8080`.

### Just the web service (any machine with Docker)

```bash
mkdir -p tram-board-data && chmod 777 tram-board-data
docker run -d --name tram-board \
  -p 8080:8080 -v "$(pwd)/tram-board-data:/app/data" \
  --restart unless-stopped \
  profdrdisco/tram-board:latest
```

Open `http://localhost:8080`, pick a station, watch the live preview.

### Pi with an LED panel attached

See [Deploy on a Raspberry Pi with LED](#deploy-on-a-raspberry-pi-with-led) below — it's two compose commands.

### Local development (Python)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

---

## Deploy on a Raspberry Pi with LED

This is the main use case: HUB75 panel attached to a Pi, web admin reachable from any phone or laptop on the LAN, the panel and browser preview always in sync.

### One-time host setup

```bash
ssh pi@raspberrypi.local

# Install Docker if it isn't already
docker --version 2>/dev/null || curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER && newgrp docker
sudo systemctl enable --now docker

# Disable the Pi's onboard sound module — it shares a hardware timer with
# the LED library and prevents hardware-PWM from initialising.
sudo sed -i '/^dtparam=audio=on/c\dtparam=audio=off' /boot/firmware/config.txt
echo "blacklist snd_bcm2835" | sudo tee /etc/modprobe.d/blacklist-rgb-matrix.conf
sudo update-initramfs -u
sudo reboot

# If you have an existing systemd service (e.g. abfahrtstafel.service) running
# the LED panel directly, stop and disable it before bringing the container up:
sudo systemctl disable --now abfahrtstafel.service 2>/dev/null || true
```

### Bring up the container

```bash
mkdir -p ~/tram-board/data && chmod 777 ~/tram-board/data && cd ~/tram-board
curl -O https://raw.githubusercontent.com/wiggi93/tram-board/master/docker-compose.pi.yml
docker compose -f docker-compose.pi.yml up -d
```

That's it. The image (`profdrdisco/tram-board:led`) is multi-arch and ships with `rpi-rgb-led-matrix` compiled in. The container restarts on every boot (`restart: unless-stopped`) and persists configuration to `~/tram-board/data/config.json` on the host so it survives upgrades.

Open `http://<pi-ip>:8080` from your phone or laptop to set the station.

### Updating to a newer image

```bash
cd ~/tram-board
docker compose -f docker-compose.pi.yml pull
docker compose -f docker-compose.pi.yml up -d
```

GitHub Actions republishes the image on every push to `master`, so a fresh `pull && up -d` always lands you on the latest.

### Tuning the panel

[`docker-compose.pi.yml`](docker-compose.pi.yml) exposes the common rgbmatrix options as environment variables — edit them, then `up -d` again:

| Env var | Default | Notes |
|---|---|---|
| `LED_ROWS` | `32` | Panel row count |
| `LED_COLS` | `64` | Panel column count |
| `LED_CHAIN` | `1` | Daisy-chained panels |
| `LED_PARALLEL` | `1` | Parallel chains |
| `LED_BRIGHTNESS` | `70` | 1–100 |
| `LED_PWM_BITS` | `11` | 1–11 |
| `LED_SLOWDOWN_GPIO` | `2` | 0–4; raise if you see flicker on faster Pis |
| `LED_GPIO_MAPPING` | *(unset)* | Set to `adafruit-hat` if you use the HAT, otherwise leave empty |
| `LED_NO_HARDWARE_PULSE` | `0` | Set to `1` if you can't disable the audio module — slight flicker but works |

Why `chmod 777 ~/tram-board/data`: the bind-mounted dir must be writable by the container's effective uid. Different Docker installs (rootful, rootless, userns-remapped) end up with different uids in the container; world-writable is the simplest portable answer for a single-user appliance.

---

## Compose files at a glance

Three files for three use cases — each ~20 lines:

| File | Image | Purpose |
|---|---|---|
| [`docker-compose.yml`](docker-compose.yml) | builds locally from `./Dockerfile` | Development on your laptop |
| [`docker-compose.deploy.yml`](docker-compose.deploy.yml) | `profdrdisco/tram-board:latest` | Web service only on any host (Mac, Linux NAS, …) |
| [`docker-compose.pi.yml`](docker-compose.pi.yml) | `profdrdisco/tram-board:led` | Web service + LED renderer on a Raspberry Pi |

You only need one of them on whatever machine you're deploying to. Copy with `curl`, run `docker compose -f <file> up -d`, done.

---

## Web admin & API

| Route | Description |
|---|---|
| `GET /` | Admin page: Tram / Text tab toggle + live preview |
| `GET /api/state` | Current departures, station info, mode, free text, last update |
| `POST /api/station` | Body `{"query": "Stop City"}` — resolves and switches station |
| `POST /api/mode` | Body `{"mode": "tram"\|"text"}` — switches what the panel shows |
| `POST /api/text` | Body `{"text": "..."}` — sets the message for text mode |

The browser preview polls `/api/state` every 250 ms so changes from any device reflect on every other device within a frame, and on the LED on the next render tick.

---

## Layout

### Tram mode

22 characters wide on the debug preview (the LED panel uses the same proportions in pixels):

```
┌──────────────────────┐
│10 Ahlem             3│
│17 Rethen            7│
│10 Ahlem            33│
│17 Rethen           37│
│       10:42:07       │
└──────────────────────┘
```

Layout: 2 chars (line number on a blue badge) + 10 chars (` ` + 9 destination chars, horizontally scrolling for long names) + 10 chars (right-aligned minutes). Bottom row centres a live clock.

Colours: blue badge, orange destination, white minutes (yellow flash for 1.5 s when the value changes), grey clock.

### Text mode

Full-width scrolling free text using a 7×14 BDF font, vertically centred, looping with a one-screen trailing gap. Rendered identically in the browser preview.

---

## Project layout

```
tram-board/
├── app.py                  # Flask app + entry point
├── tram.py                 # EFA client, BoardState, fetch loop
├── config.py               # JSON config persistence
├── led.py                  # Optional LED renderer (lazy-imports rgbmatrix)
├── templates/
│   └── index.html          # Admin page (vanilla HTML/CSS/JS, no build step)
├── fonts/
│   ├── 4x6.bdf             # Tram-mode font
│   └── 7x14.bdf            # Text-mode font
├── docker-compose.yml      # Build locally — for development
├── docker-compose.deploy.yml  # Web only — pulls profdrdisco/tram-board:latest
├── docker-compose.pi.yml   # Web + LED — pulls profdrdisco/tram-board:led
├── Dockerfile              # Single multi-stage build, WITH_LED arg toggles rgbmatrix
└── .github/workflows/docker.yml  # Auto-publishes both images on push to master
```

---

## Notes

- The admin page has **no authentication**. Run it on a trusted network or front it with a reverse proxy that adds auth.
- The EFA stop-finder returns the best-matching stop, which isn't always the literal name (e.g. `Hauptbahnhof Karlsruhe` resolves to `Hauptbahnhof Süd`). The resolved name is shown next to the station so you can refine the query.
- Only Stadtbahn / Straßenbahn products (`product.class` 3 or 4) are shown. Buses, S-Bahn, regional trains are filtered out. Change `TRAM_CLASSES` in [`tram.py`](tram.py) to widen this.
- At very busy hubs (4+ trams within 5 min), the board automatically de-duplicates by `(line, destination)` and strips the redundant city prefix from destinations. Quiet stops are shown as-is.

---

## Building and publishing the image yourself

You only need this section if you've **forked the repo** and want to publish under your own Docker Hub namespace. For normal use, the pre-built `profdrdisco/tram-board` images on Docker Hub are auto-updated by CI and you can ignore this entire section.

### One-time setup on your dev machine

```bash
docker login
docker buildx create --use --name tram-builder
```

### Build & push manually (multi-arch)

```bash
# Web-only image (runs anywhere)
docker buildx build \
  --platform linux/amd64,linux/arm64,linux/arm/v7 \
  -t <your-dockerhub-user>/tram-board:latest \
  --build-arg WITH_LED=0 \
  --push .

# LED-enabled image (Pi only — slow under QEMU emulation)
docker buildx build \
  --platform linux/arm64,linux/arm/v7 \
  -t <your-dockerhub-user>/tram-board:led \
  --build-arg WITH_LED=1 \
  --push .
```

### Or let GitHub Actions do it on every push

[`.github/workflows/docker.yml`](.github/workflows/docker.yml) builds and publishes both tags on every push to `master`. To enable in your fork, add two repository secrets at **Settings → Secrets and variables → Actions**:

- `DOCKERHUB_USERNAME` — your Docker Hub username
- `DOCKERHUB_TOKEN` — an access token (scope: *Read, Write, Delete*) from [Docker Hub → Personal Access Tokens](https://app.docker.com/settings/personal-access-tokens)

Then update the `IMAGE` env in [`.github/workflows/docker.yml`](.github/workflows/docker.yml) and the default `image:` in the compose files to your namespace, and every push republishes both tags.
