# Aali Studio on the Mac — where to start

This archive is **source code only**. There is no ready-made `.app` inside:
a PyInstaller app must be built on the OS it will run on, and a macOS `.app`
cannot be produced from Windows. The build on your machine takes a couple of
minutes.

## 1. On the Mac

```bash
unzip HWK-Aali-MAC-TEST.zip
cd HWK-Aali
bash scripts/build_all_macos.sh studio
```

Output: `build-desktop/dist/Aali-Studio.app` — double-click it.
If Gatekeeper calls it "damaged" (only the first time):

```bash
xattr -dr com.apple.quarantine build-desktop/dist/Aali-Studio.app
```

## 2. Test it

```bash
bash scripts/build_all_macos.sh check
```

This probes **your** machine: config-dir convention, the sandbox, path
escapes, the Arabic renderer, and brain reachability. **Paste me the full
output** — it is the source of truth, because everything written here has
been verified on Windows only.

## 3. Run without building (fastest first check)

```bash
python3 -m venv .venv-studio
.venv-studio/bin/pip install flask pywebview
.venv-studio/bin/python build-desktop/aali-studio/studio_app.py
```

## 4. The brain still lives on the Windows PC

Studio on the Mac is a **client**. The model needs the GPU and the
checkpoint. Point it at the PC instead of waiting for one:

```bash
export AALI_BRAIN_URL=http://<ip-of-the-PC>:5055
```

Full details: `docs/PORTING.md` (Arabic, honest per-OS matrix).

## 5. What is NOT in this archive

- `scripts/cloudflared.exe` (Windows tunnel binary — removed on purpose)
- `.venv*`, `build-desktop/work*`, `build-desktop/dist` (machine-local)
- the model, `D:\hwk-data`, corpora, logs (never in git)

## 6. Honest limits

- The `.app` is **unsigned**: the `xattr` line above is the one-time fix.
- No `build-desktop/icon.icns` exists yet, so the app gets the default
  Python icon. Cosmetic only; the build does not fail on it.
- PyInstaller targets the machine that builds. Build on your Mac for your
  Mac.
- The first real check is still yours to run: I cannot execute macOS from
  here, so treat `build_all_macos.sh check` output as the verdict.