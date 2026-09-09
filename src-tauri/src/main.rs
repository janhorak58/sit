// The source-installed launcher sets the checkout and virtualenv paths.
// Reuse an existing backend; otherwise own it until the desktop app exits.
use std::env;
use std::io::{Read, Write};
use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

const BACKEND_ADDR: &str = "127.0.0.1:47831";
const STARTUP_TIMEOUT: Duration = Duration::from_secs(25);

struct OwnedBackend(Child);

impl Drop for OwnedBackend {
    fn drop(&mut self) {
        if matches!(self.0.try_wait(), Ok(Some(_))) {
            return;
        }
        // SIGTERM lets uvicorn run shutdown hooks and flush active recording.
        #[cfg(unix)]
        unsafe {
            libc::kill(self.0.id() as libc::pid_t, libc::SIGTERM);
        }
        #[cfg(not(unix))]
        let _ = self.0.kill();
        let deadline = Instant::now() + Duration::from_secs(10);
        while Instant::now() < deadline {
            if matches!(self.0.try_wait(), Ok(Some(_))) {
                return;
            }
            thread::sleep(Duration::from_millis(100));
        }
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

fn backend_ready(addr: &str) -> bool {
    let timeout = Duration::from_millis(500);
    let Ok(addr) = addr.parse() else { return false };
    let Ok(mut stream) = TcpStream::connect_timeout(&addr, timeout) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(timeout));
    let _ = stream.set_write_timeout(Some(timeout));
    if stream
        .write_all(b"GET /recording/status HTTP/1.0\r\n\r\n")
        .is_err()
    {
        return false;
    }
    let mut response = String::new();
    if stream.take(16384).read_to_string(&mut response).is_err() {
        return false;
    }
    let Some((headers, body)) = response.split_once("\r\n\r\n") else {
        return false;
    };
    if headers
        .lines()
        .next()
        .and_then(|line| line.split_whitespace().nth(1))
        != Some("200")
    {
        return false;
    }
    let Ok(status) = serde_json::from_str::<serde_json::Value>(body) else {
        return false;
    };
    status["recording"].is_boolean() && status["max_seconds"].is_number()
}

fn backend_paths() -> Result<(PathBuf, PathBuf), String> {
    let project = env::var_os("TRANSCRIBER_PROJECT_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            Path::new(env!("CARGO_MANIFEST_DIR"))
                .parent()
                .unwrap()
                .to_owned()
        });
    if !project.is_absolute() || !project.join("transcriber/__main__.py").is_file() {
        return Err(
            "Set TRANSCRIBER_PROJECT_DIR to the absolute path of a SIT source checkout.".into(),
        );
    }
    let python = env::var_os("TRANSCRIBER_PYTHON")
        .map(PathBuf::from)
        .unwrap_or_else(|| project.join(".venv/bin/python"));
    if !python.is_absolute() || !python.is_file() {
        return Err(format!(
            "Python not found at {}. In {}, create .venv with Python 3.12 and install -r requirements.txt, or set TRANSCRIBER_PYTHON to an absolute interpreter path.",
            python.display(), project.display()
        ));
    }
    Ok((project, python))
}

fn start_backend() -> Result<Option<OwnedBackend>, String> {
    if backend_ready(BACKEND_ADDR) {
        eprintln!("SIT: reusing backend at {BACKEND_ADDR}");
        return Ok(None);
    }
    // Keep compatibility with existing Linux installations, but never require
    // systemd. Bound even the systemctl client, not just the readiness wait.
    #[cfg(target_os = "linux")]
    if let Ok(mut service) = Command::new("systemctl")
        .args(["--user", "--no-block", "start", "transcriber.service"])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
    {
        let deadline = Instant::now() + Duration::from_secs(2);
        let started = loop {
            match service.try_wait() {
                Ok(Some(status)) => break status.success(),
                Ok(None) if Instant::now() < deadline => thread::sleep(Duration::from_millis(100)),
                _ => {
                    let _ = service.kill();
                    let _ = service.wait();
                    break false;
                }
            }
        };
        if started {
            let deadline = Instant::now() + STARTUP_TIMEOUT;
            while Instant::now() < deadline {
                if backend_ready(BACKEND_ADDR) {
                    return Ok(None);
                }
                thread::sleep(Duration::from_millis(200));
            }
            return Err("transcriber.service was started but did not become ready. Check journalctl --user -u transcriber.service.".into());
        }
    }
    let (project, python) = backend_paths()?;
    eprintln!(
        "SIT: starting {} -m transcriber in {}",
        python.display(),
        project.display()
    );
    let child = Command::new(&python)
        .args(["-m", "transcriber"])
        .current_dir(&project)
        .env("TRANSCRIBER_HOST", "127.0.0.1")
        .env("TRANSCRIBER_PORT", "47831")
        .stdin(Stdio::null())
        .spawn()
        .map_err(|err| format!("Cannot start {}: {err}", python.display()))?;
    let mut backend = OwnedBackend(child);
    let deadline = Instant::now() + STARTUP_TIMEOUT;
    loop {
        if let Some(status) = backend.0.try_wait().map_err(|err| err.to_string())? {
            return Err(format!(
                "Backend exited ({status}). Run {} -m transcriber in {} to inspect the error.",
                python.display(),
                project.display()
            ));
        }
        if backend_ready(BACKEND_ADDR) {
            return Ok(Some(backend));
        }
        if Instant::now() >= deadline {
            return Err(format!("Backend did not become ready at {BACKEND_ADDR}; check Python dependencies and port conflicts."));
        }
        thread::sleep(Duration::from_millis(200));
    }
}

fn run() -> Result<(), String> {
    let mut backend = start_backend()?;
    let app = tauri::Builder::default()
        .build(tauri::generate_context!())
        .map_err(|err| {
            format!("Cannot open desktop window (requires a graphical session / WSLg): {err}")
        })?;
    eprintln!("SIT: desktop window ready");
    app.run(move |_handle, event| {
        if matches!(event, tauri::RunEvent::Exit) {
            drop(backend.take());
        }
    });
    Ok(())
}

fn main() {
    if let Err(error) = run() {
        eprintln!("SIT: {error}");
        std::process::exit(1);
    }
}
