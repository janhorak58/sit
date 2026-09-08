// Native window shell for the ŠIT web UI. No bundled frontend: the
// window loads the already-running local backend directly (see
// tauri.conf.json `app.windows[0].url`). The systemd user service
// (transcriber.service, enabled + linger) is the source of truth for the
// backend process; this just covers a cold-start race on first launch.

use std::net::TcpStream;
use std::process::Command;
use std::time::{Duration, Instant};

const BACKEND_ADDR: &str = "127.0.0.1:47831";

fn main() {
    let _ = Command::new("systemctl")
        .args(["--user", "start", "transcriber.service"])
        .status();

    let deadline = Instant::now() + Duration::from_secs(10);
    while Instant::now() < deadline {
        if TcpStream::connect_timeout(&BACKEND_ADDR.parse().unwrap(), Duration::from_millis(300))
            .is_ok()
        {
            break;
        }
        std::thread::sleep(Duration::from_millis(200));
    }

    tauri::Builder::default()
        .run(tauri::generate_context!())
        .expect("error while running Tauri application");
}
