# ŠIT — ŠIKOVNÝ INTERAKTIVNÍ TRANSKRIPTOR

Nahrávky, přepisy s rozlišením mluvčích a souhrny na jednom místě. ŠIT přijímá
audio/video soubory a na podporovaném linuxovém zvukovém serveru nahrává mikrofon
společně se systémovým zvukem.

FastAPI backend lze otevřít v prohlížeči nebo v nativním okně Tauri. Přepis používá
nakonfigurovaný privátní ASR server, při jeho nedostupnosti lokální faster-whisper.
Rozpoznání mluvčích obdobně používá vzdálený nebo lokální pyannote. Souhrny vyžadují
samostatný OpenAI-kompatibilní server; lokální fallback pro souhrny není zabudovaný.
Audio se při použití vzdáleného ASR/diarizace odesílá příslušnému serveru, přepis
při generování souhrnu souhrnnému serveru. Pro čistě lokální ASR nastav
`SPARK_WHISPER_URL=` a `SPARK_DIARIZER_URL=`.

## Platformy

| Prostředí | Stav |
| --- | --- |
| Arch Linux + PipeWire-Pulse / PulseAudio | Vývojové prostředí; lokálně ověřené spuštění a backend |
| Ubuntu 24.04 / Debian 12 a novější | Připravený instalační postup; běh na těchto distribucích zatím neověřen |
| WSL2 + WSLg | Launcher nevyžaduje systemd; cílové prostředí zatím neověřeno, omezení zvuku níže |
| Nativní Windows / macOS | Zatím nepodporované desktopovým instalátorem ani recorderem |

Jde o **instalaci ze zdrojů**, ne o samostatný distribuční balíček. Desktopový
launcher potřebuje zachovaný checkout, Python prostředí a sestavenou binárku.
Žádné systémové služby ani cesty specifické pro Arch nejsou pro běh nutné.

## Instalace

Doporučený Python je **3.12** (alternativně 3.11); nejnovější systémový Python
na rolling-release distribuci nemusí mít dostupné ML wheels. Python lze spravovat
pomocí `uv`. Pro instalaci z tohoto soukromého GitHub repozitáře potřebuješ přístup.

### Ubuntu / Debian — systémové závislosti

```bash
sudo apt update
sudo apt install -y git curl python3 python3-venv ffmpeg pulseaudio-utils

# Jen pro nativní okno; pro prohlížeč nejsou build závislosti potřeba.
sudo apt install -y build-essential pkg-config libwebkit2gtk-4.1-dev \
  libssl-dev libxdo-dev libayatana-appindicator3-dev librsvg2-dev
```

Na běžném desktopu musí běžet PulseAudio nebo PipeWire s kompatibilní službou
PipeWire-Pulse. `pulseaudio-utils` dodává klienta `pactl`, ne zvukový server.

### Arch Linux — systémové závislosti

```bash
sudo pacman -S --needed git curl python uv ffmpeg libpulse

# Jen pro nativní okno:
sudo pacman -S --needed base-devel pkgconf webkit2gtk-4.1 openssl \
  libappindicator-gtk3 librsvg xdotool rustup
rustup default stable
```

Použij existující PipeWire-Pulse nebo PulseAudio; **nenahrazuj kvůli aplikaci
svůj zvukový server**. `libpulse` poskytuje `pactl` i při použití PipeWire.

### Společné kroky — Python a checkout

Pokud `uv` není nainstalované, nainstaluj ho podle
[oficiálního postupu](https://docs.astral.sh/uv/getting-started/installation/),
například `curl -LsSf https://astral.sh/uv/install.sh | sh`, a otevři nový terminál.

```bash
git clone https://github.com/janhorak58/sit.git
cd sit
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
```

Alternativa se systémovým Pythonem 3.11/3.12:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Instalace ML knihoven a první stažení modelů potřebují připojení a volné místo.
GPU není nutné; výchozí lokální ASR používá CPU. `HF_TOKEN` je potřeba pro gated
modely rozpoznání mluvčích, ne pro samotné otevření aplikace nebo přepis Whisperem.

### Varianta A — aplikace v prohlížeči

```bash
.venv/bin/python -m transcriber
```

Otevři <http://127.0.0.1:47831>. Backend běží do ukončení příkazu pomocí Ctrl+C.
Data se standardně ukládají do `~/.local/share/sit` (respektuje `XDG_DATA_HOME`).

### Varianta B — nativní desktopové okno

Nainstaluj stabilní Rust přes [rustup](https://rustup.rs/) (na Archu viz výše).
Při instalaci rustup skriptem poté načti `source "$HOME/.cargo/env"`.
Systémové build závislosti jsou uvedené výše;
[oficiální předpoklady Tauri](https://v2.tauri.app/start/prerequisites/).

```bash
cargo build --release --manifest-path src-tauri/Cargo.toml
python3 scripts/install-desktop.py
```

V nabídce aplikací se objeví **ŠIT**. Z terminálu ho spustíš i absolutní cestou:

```bash
~/.local/bin/sit
```

Instalátor nevyžaduje sudo. Vytvoří:

- `~/.local/bin/sit` — launcher s absolutní cestou k checkoutu a `.venv/bin/python`;
- `${XDG_DATA_HOME:-~/.local/share}/applications/sit.desktop` — položku nabídky;
- `${XDG_DATA_HOME:-~/.local/share}/icons/sit.png` — ikonu.

Opakovaná instalace aktualizuje vlastní soubory; cizí soubor nebo symbolický odkaz
na některém z cílových míst odmítne změnit. Po přesunutí checkoutu instalátor spusť
znovu. Aktualizace Rust kódu vyžaduje nový `cargo build --release`; launcher používá
binárku přímo z checkoutu. Webové/Python změny se projeví po restartu backendu.

Launcher znovu použije backend na `127.0.0.1:47831`. Na Linuxu se také může pokusit
spustit již nainstalovanou službu `transcriber.service`. Pokud služba není dostupná,
spustí Python přímo — **systemd není podmínka**, ani ve WSL.
Před otevřením okna čeká na odpověď API; chybnou instalaci vypíše do stderr.
Při problému proto spusť `~/.local/bin/sit` v terminálu.

**Zavření okna ukončí backend, který si okno samo spustilo.** Nejprve dokonči
uložení nahrávky a zpracování; rozpracovaný přepis se při ukončení přeruší.
Aktivní recorder se při řádném vypnutí backendu zastaví, dočasný WAV zůstane
v `_scratch` (nenahrazuje to tlačítko pro uložení nahrávky).
Ručně spuštěný nebo systemd spravovaný backend okno neukončuje. Pokud mají úlohy
běžet i po zavření okna, spusť backend předem podle varianty A.

Odstranění desktopové integrace (nesmaže data, checkout ani `.venv`):

```bash
python3 scripts/install-desktop.py --uninstall
```

### WSL2 s WSLg

V PowerShellu na Windows 11 ověř/aktualizuj WSL:

```powershell
wsl --version
wsl --update
```

Uvnitř Ubuntu ve WSL postupuj podle linuxové instalace výše. Checkout doporučujeme
v linuxovém souborovém systému, např. `~/sit`, ne `/mnt/c/...` kvůli výkonu a právům.
WSLg poskytuje grafické prostředí a zvukové propojení; nenastavuj ručně `DISPLAY`
ani `PULSE_SERVER`, pokud je již WSLg nastavilo. Nespouštěj pro tuto aplikaci další
PulseAudio server nad WSLg. Viz [Linux GUI aplikace ve WSL](https://learn.microsoft.com/windows/wsl/tutorials/gui-apps).

**Mikrofon a zvuk Windows nejsou totéž.** Recorder potřebuje PulseAudio moduly
`module-null-sink`, `module-loopback` a monitor výstupu. WSLg je nemusí poskytovat;
systémový zvuk Windows nelze touto implementací zaručit. Při chybě použij
**Nahrát soubor**: pořiď záznam ve Windows a nahraj ho přes UI. Upload používá
`ffmpeg`, ne PulseAudio. Podpora WSLg není podpora nativního Windows buildu.

## Data a konfigurace

Volitelné `.env` v kořeni checkoutu je ignorované Gitem; nikdy do repozitáře
neukládej tokeny. Pro existující knihovnu nastav například
`TRANSCRIBER_DATA_DIR=/absolutni/cesta/ke/knihovne`.
**Původní `/data` se automaticky nepřesouvá:** pokud jej dosud používáš mimo Docker,
zachovej `TRANSCRIBER_DATA_DIR=/data` v prostředí nebo `.env`.

| Proměnná | Výchozí hodnota | Účel |
| --- | --- | --- |
| `TRANSCRIBER_DATA_DIR` | `${XDG_DATA_HOME:-~/.local/share}/sit`; Docker `/data` | Kořen knihovny; explicitní hodnota má přednost, `~` se rozbalí |
| `TRANSCRIBER_HOST` / `TRANSCRIBER_PORT` | `127.0.0.1` / `47831` | Adresa backendu při ručním spuštění |
| `HF_TOKEN` | prázdné | Přístup ke gated pyannote modelům |
| `WHISPER_MODEL` | `small` | Lokální faster-whisper model |
| `WHISPER_DEVICE` / `WHISPER_COMPUTE_TYPE` | `cpu` / `int8` | Lokální inference |
| `DIARIZE_MODEL` | `pyannote/speaker-diarization-3.1` | Lokální diarizace |
| `SPARK_WHISPER_URL` | `http://127.0.0.1:8204/v1/audio/transcriptions` | Vzdálený ASR; prázdné = lokální |
| `SPARK_WHISPER_MODEL` | `large-v3` | Vzdálený model |
| `SPARK_DIARIZER_URL` | `http://127.0.0.1:8000/v1/audio/diarizations` | Vzdálená diarizace; prázdné = lokální |
| `OMNIROUTE_URL` / `OMNIROUTE_MODEL` | `http://127.0.0.1:20128` / `cc/claude-sonnet-5` | Server a model pro souhrny; nutné nastavit podle svého serveru |
| `GPT_OSS_API_KEY` | prázdné | Autorizace serveru pro souhrny |
| `MAX_RECORDING_SECONDS` | `10800` | Maximální délka záznamu v sekundách |

Lokální pyannote potřebuje souhlas s podmínkami modelů
[speaker-diarization-3.1](https://huggingface.co/pyannote/speaker-diarization-3.1) a
[segmentation-3.0](https://huggingface.co/pyannote/segmentation-3.0).
Vzdálené modelové servery nejsou součástí běžící desktopové aplikace; vlastní
Spark diarizační službu popisuje [diarizer/README.md](diarizer/README.md).

Desktopový launcher nastavuje `TRANSCRIBER_PROJECT_DIR` a `TRANSCRIBER_PYTHON`
na absolutní cesty. Při vlastním spuštění binárky je lze nastavit v prostředí.
Bez nich hledá checkout v místě sestavení a Python v jeho `.venv/bin/python`.
Desktop vždy používá `127.0.0.1:47831`; jiný port backendu otevři v prohlížeči.
Relativní `XDG_DATA_HOME` se ignoruje podle XDG specifikace.

Přepisy: `<folder>/<name>.txt`; strukturovaná data: `<name>.meeting.json`;
nahrávky: `<folder>/audio/<name>.wav`; souhrny: `<name>.summary.md` / `.summary.json`.
Starší audio přímo ve složce zůstává čitelné. Pracovní soubory jsou v `_scratch/`.

### Nahrávání a volitelná integrace správce souborů

`pactl info` musí vidět zvukový server uživatele, pod kterým běží backend.
Chybějící `pactl`, `ffmpeg` nebo odmítnuté PulseAudio moduly vracejí chybu v UI;
chybějící zvukový server nebrání práci s knihovnou a uploadu souboru.

Tlačítko **Otevřít na disku** zatím používá samostatný pomocník
`transcriber-opener` na `127.0.0.1:47833`. Tento pomocník není součástí repozitáře
ani desktopového instalátoru; bez něj tlačítko oznámí chybu. Ostatní funkce ho
nepotřebují — knihovnu lze otevřít ručně v jejím datovém adresáři.

## Docker

```bash
docker build -t transcriber .
docker run --rm -p 127.0.0.1:47831:47831 -v "$PWD/data:/data" transcriber
```

Obraz explicitně používá `/data` a bind `0.0.0.0` uvnitř kontejneru. Ukázka
publikuje port pouze lokálně. Upload nepotřebuje zvukový socket; přímý záznam ano
(PulseAudio socket hostitele, přístupová práva a `PULSE_SERVER`). Desktopový
instalátor neinstaluje kontejner. Konfiguraci můžeš předat přes `--env-file .env`.

**API nemá přihlašování.** Nevystavuj ho veřejně. Pro LAN přístup je nutné vědomě
změnit bind/port mapping a zabezpečit přístup; výchozí lokální bind je záměrný.

## Vývoj a API

```bash
uv pip install --python .venv/bin/python -r requirements-dev.txt
.venv/bin/python -m pytest tests/test_services.py
node tests/group_segments.mjs
```

| Metoda | Cesta | Účel |
| --- | --- | --- |
| GET | `/` | UI |
| POST | `/start`, `/stop`, `/recording/cancel` | Životní cyklus nahrávání |
| GET | `/recording/status`, `/progress` | Stav nahrávání / zpracování |
| POST | `/upload` | Raw audio/video upload a převod přes ffmpeg |
| POST | `/transcribe`, `/diarize` | Přepis nebo nové rozpoznání mluvčích |
| POST | `/summaries` | Souhrn |
| GET | `/asr/status`, `/asr/diagnostics` | Dostupnost a diagnostika modelů |
| GET | `/projects`, `/library/browse` | Procházení knihovny |
| POST | `/projects/suggest-folder` | Návrh složky |
| GET | `/library/file`, `/library/meeting`, `/library/summary-json`, `/library/audio` | Výstupy a audio |
| POST | `/library/mkdir`, `/library/move`, `/library/delete` | Úpravy knihovny |
| POST | `/library/folder/rename`, `/library/folder/delete`, `/library/rename-speaker` | Přejmenování a mazání |
| GET | `/download` | Stažení textového výstupu |

Očekávané aplikační chyby vracejí HTTP 200 s `{"error": "..."}`; UI kontroluje toto
pole. Validační chyby API mohou vracet jiné HTTP statusy.
