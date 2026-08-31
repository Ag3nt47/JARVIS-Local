"""Native Windows desktop shell for the unchanged JARVIS web interface."""

from __future__ import annotations

import ctypes
import json
import multiprocessing
import os
import socket
import sys
import tempfile
import threading
import time
from ctypes import wintypes
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import psutil
import uvicorn

from jarvis import __version__
from jarvis.api import create_app
from jarvis.config import Settings, load_settings
from jarvis.preferences import DesktopPreference, PreferencesService, PreferencesUpdate

APP_TITLE = "JARVIS // Command Interface"
LOOPBACK_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
COMPANION_TITLE = "JARVIS // Globe Companion"
COMPANION_HOTKEY = "Ctrl+Alt+J"


COMPANION_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta http-equiv="Content-Security-Policy"
        content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'">
  <title>JARVIS Globe Companion</title>
  <style>
    :root { color-scheme: dark; font-family: "Segoe UI", sans-serif; }
    * { box-sizing: border-box; }
    html, body { width: 100%; height: 100%; margin: 0; overflow: hidden; background: transparent; }
    body { display: grid; grid-template-rows: minmax(0,1fr) 50px 50px; user-select: none; }
    #stage { position: relative; cursor: default; }
    canvas { width: 100%; height: 100%; display: block; }
    #name { position: absolute; inset: 50% auto auto 50%; transform: translate(-50%,-50%);
      color: #d9fbff; letter-spacing: .36em; font-size: clamp(13px,4vw,21px);
      text-shadow: 0 0 13px #61e6ff, 0 0 35px #088fb4; pointer-events: none; }
    #close { position: absolute; top: 12%; right: 12%; width: 30px; height: 30px;
      border: 1px solid rgba(110,232,255,.35); border-radius: 50%; color: #8deeff;
      background: rgba(2,17,25,.74); cursor: pointer; font-size: 16px; }
    .node { position: absolute; z-index: 2; width: 64px; height: 64px; padding: 4px;
      border: 1px solid rgba(97,230,255,.42); border-radius: 50%; color: #b9f5ff;
      background: radial-gradient(circle,rgba(13,61,77,.88),rgba(2,18,25,.78));
      font: 600 8px Consolas,monospace; cursor: pointer; }
    .node::before { content:""; position:absolute; inset:-5px; border:1px dashed rgba(97,230,255,.18); border-radius:50%; }
    .node:hover,.node:focus-visible { outline: 2px solid rgba(168,247,255,.72); outline-offset: 2px; color:#fff; }
    .node .detail { position:absolute; z-index:5; width:145px; padding:7px; opacity:0; pointer-events:none;
      color:#ccecf2; border:1px solid rgba(97,230,255,.3); border-radius:7px;
      background:rgba(2,18,25,.96); font:9px/1.35 "Segoe UI",sans-serif; transition:opacity .12s; }
    .node:hover .detail,.node:focus-visible .detail { opacity:1; }
    #projects { left:7%; top:17%; } #projects .detail { left:54px; top:-10px; }
    #angerona { right:7%; top:17%; } #angerona .detail { right:54px; top:-10px; }
    #models { left:8%; bottom:16%; } #models .detail { left:54px; bottom:-10px; }
    #federation { right:7%; bottom:16%; } #federation .detail { right:54px; bottom:-10px; }
    footer { position:relative; display:flex; align-items:center; justify-content:center; gap:8px; }
    #open,#menuToggle,.menu button { border: 1px solid rgba(97,230,255,.58); border-radius: 999px; padding: 9px 14px;
      color: #d8fbff; background: rgba(3,26,36,.88); cursor: pointer; letter-spacing: .2em;
      font: 600 10px Consolas, monospace; box-shadow: 0 0 18px rgba(27,190,225,.18); }
    #menuToggle { padding-inline:11px; }
    .menu { position:absolute; z-index:9; right:16%; bottom:48px; display:grid; gap:4px; width:150px;
      padding:7px; border:1px solid rgba(97,230,255,.4); border-radius:10px; background:rgba(2,16,23,.97); }
    .menu[hidden] { display:none; }
    .menu button { width:100%; border-radius:6px; padding:8px; text-align:left; letter-spacing:.08em; }
    #state { color: rgba(151,235,249,.68); font: 9px Consolas, monospace; letter-spacing: .08em; }
    #voiceBar { display:grid; grid-template-columns:38px minmax(0,1fr) 52px; align-items:center; gap:6px;
      margin:0 8%; padding:5px 7px; border:1px solid rgba(98,242,179,.3); border-radius:11px 11px 0 0;
      background:rgba(2,18,24,.9); box-shadow:0 0 18px rgba(54,225,160,.1); }
    #listen,#stageCommand { height:34px; padding:0 7px; border:1px solid rgba(105,245,189,.44); border-radius:8px;
      color:#9ff5cb; background:rgba(5,42,37,.82); font:700 8px Consolas,monospace; cursor:pointer; }
    #listen[aria-pressed="true"] { color:#eafff4; box-shadow:0 0 14px rgba(105,245,189,.5); }
    #voiceDraft { min-width:0; height:34px; padding:0 8px; border:1px solid rgba(97,230,255,.24); border-radius:8px;
      color:#d8fbff; background:rgba(2,14,20,.84); font:9px "Segoe UI",sans-serif; user-select:text; }
    #voiceDraft::placeholder { color:rgba(151,207,216,.62); }
    #stageCommand:disabled { opacity:.42; cursor:not-allowed; }
    button:hover { filter: brightness(1.25); }
    @media (prefers-reduced-motion: reduce) { .node::before { border-style:solid; } }
  </style>
</head>
<body>
  <div id="stage" title="Double-click to open the full JARVIS interface">
    <canvas id="globe" aria-hidden="true"></canvas><div id="name">JARVIS</div>
    <button id="projects" class="node" type="button" data-target="projects">PROJECTS
      <span class="detail" role="tooltip">Open tracked projects and local workspaces.</span></button>
    <button id="angerona" class="node" type="button" data-target="angerona">ANGERONA
      <span class="detail" role="tooltip">View primary endpoint-defense posture and read-only observations.</span></button>
    <button id="models" class="node" type="button" data-target="models">MCP / OLLAMA
      <span class="detail" role="tooltip">Inspect local model and MCP service settings.</span></button>
    <button id="federation" class="node" type="button" data-target="federation">COMMS
      <span class="detail" role="tooltip">Open trusted device and federation settings.</span></button>
    <button id="close" type="button" aria-label="Exit JARVIS">×</button>
  </div>
  <footer title="Mouse interaction is available without activation. Ctrl+Alt+J always returns to the full HUD.">
    <button id="open" type="button">OPEN HUD</button><button id="menuToggle" type="button" aria-expanded="false" aria-controls="menu">MENU</button><span id="state">LOCAL</span>
    <div id="menu" class="menu" role="menu" hidden>
      <button type="button" role="menuitem" data-menu="hud">Open HUD</button>
      <button type="button" role="menuitem" data-menu="projects">Projects</button>
      <button type="button" role="menuitem" data-menu="settings">Settings</button>
      <button id="motion" type="button" role="menuitem" data-menu="motion">Pause motion</button>
      <button type="button" role="menuitem" data-menu="exit">Exit</button>
    </div>
  </footer>
  <div id="voiceBar" aria-label="Voice to text command draft">
    <button id="listen" type="button" aria-label="Start voice to text" aria-pressed="false">MIC</button>
    <input id="voiceDraft" type="text" maxlength="2000" autocomplete="off" placeholder="Voice to text…">
    <button id="stageCommand" type="button" disabled title="Open this draft in the guarded command console">OPEN</button>
  </div>
  <script>
    const canvas = document.getElementById('globe');
    const ctx = canvas.getContext('2d', {alpha: true});
    const state = document.getElementById('state');
    let started = performance.now(), motionPaused = false, lastFrame = 0;
    let recognition = null, micAnalyser = null, micData = null, micStream = null;
    function size() {
      const dpr = Math.min(devicePixelRatio || 1, 2);
      const box = canvas.getBoundingClientRect();
      canvas.width = Math.round(box.width * dpr); canvas.height = Math.round(box.height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    function draw(now) {
      if (motionPaused) { requestAnimationFrame(draw); return; }
      lastFrame = now;
      const w = canvas.clientWidth, h = canvas.clientHeight, cx = w/2, cy = h/2;
      const r = Math.min(w,h) * .315, t = (now-started)/1000;
      ctx.clearRect(0,0,w,h);
      const halo = ctx.createRadialGradient(cx,cy,r*.18,cx,cy,r*1.55);
      halo.addColorStop(0,'rgba(95,231,255,.23)'); halo.addColorStop(.52,'rgba(19,144,180,.11)');
      halo.addColorStop(1,'rgba(0,0,0,0)'); ctx.fillStyle=halo; ctx.beginPath();
      ctx.arc(cx,cy,r*1.55,0,Math.PI*2); ctx.fill();
      ctx.save(); ctx.translate(cx,cy); ctx.rotate(t*.07);
      for (let ring=0; ring<6; ring++) {
        const rr=r*(.72+ring*.075), phase=t*(ring%2?-.24:.18)+ring*.72;
        ctx.strokeStyle=`rgba(${80+ring*12},${190+ring*8},255,${.17+ring*.035})`;
        ctx.lineWidth=ring===5?1.4:.75; ctx.setLineDash([rr*.17,rr*.085]); ctx.lineDashOffset=phase*22;
        ctx.beginPath(); ctx.ellipse(0,0,rr,rr*(.38+ring*.075),phase*.07,0,Math.PI*2); ctx.stroke();
      }
      ctx.setLineDash([]);
      for (let i=0;i<54;i++) {
        const a=i*2.399963+t*.11, y=1-(i/53)*2, rr=Math.sqrt(1-y*y);
        const x=Math.cos(a)*rr, z=Math.sin(a)*rr, px=x*r*.9, py=y*r*.9;
        if(z<-.15) continue; const alpha=.18+(z+1)*.25;
        ctx.fillStyle=`rgba(118,235,255,${alpha})`; ctx.beginPath();
        ctx.arc(px,py,1.1+z*.8,0,Math.PI*2); ctx.fill();
      }
      ctx.strokeStyle='rgba(116,237,255,.64)'; ctx.lineWidth=1.2; ctx.beginPath();
      ctx.arc(0,0,r*.92,0,Math.PI*2); ctx.stroke(); ctx.restore();
      const core=ctx.createRadialGradient(cx-r*.2,cy-r*.25,2,cx,cy,r*.72);
      core.addColorStop(0,'rgba(226,253,255,.48)'); core.addColorStop(.3,'rgba(70,205,235,.19)');
      core.addColorStop(1,'rgba(0,75,103,.04)'); ctx.fillStyle=core; ctx.beginPath();
      ctx.arc(cx,cy,r*.72,0,Math.PI*2); ctx.fill();
      let micLevel=0;
      if(micAnalyser&&micData){ micAnalyser.getByteTimeDomainData(micData); let energy=0;
        for(const value of micData){ const sample=(value-128)/128; energy+=sample*sample; }
        micLevel=Math.min(1,Math.sqrt(energy/micData.length)*5); }
      const speaking=Boolean(window.speechSynthesis&&window.speechSynthesis.speaking);
      drawSignal(cx,cy+26,r*1.1,speaking?7+Math.sin(t*18)*3:1.4,'rgba(97,230,255,.86)',t*4.5);
      drawSignal(cx,cy+43,r*.88,1.2+micLevel*12,'rgba(105,245,189,.88)',t*5.7);
      requestAnimationFrame(draw);
    }
    function drawSignal(cx,y,width,amplitude,color,phase) {
      ctx.save(); ctx.strokeStyle=color; ctx.lineWidth=1.35; ctx.shadowColor=color; ctx.shadowBlur=7;
      ctx.beginPath(); const left=cx-width/2;
      for(let i=0;i<=48;i++){ const ratio=i/48, envelope=Math.sin(Math.PI*ratio);
        const wave=Math.sin(ratio*Math.PI*10+phase)+.42*Math.sin(ratio*Math.PI*21-phase*.7);
        const py=y+wave*amplitude*envelope; if(i===0)ctx.moveTo(left,py);else ctx.lineTo(left+ratio*width,py); }
      ctx.stroke(); ctx.restore();
    }
    async function openHud() { try { await window.pywebview.api.restore_full_app(); } catch (_) {} }
    async function navigate(target) { try { await window.pywebview.api.open_destination(target); } catch (_) {} }
    async function exitJarvis() { try { await window.pywebview.api.exit_app(); } catch (_) {} }
    async function stageVoiceCommand() {
      const draft=document.getElementById('voiceDraft').value.trim(); if(!draft) return;
      try { await window.pywebview.api.stage_command(draft); } catch (_) { state.textContent='OPEN HUD'; }
    }
    async function startMicMeter() {
      try { micStream=await navigator.mediaDevices.getUserMedia({audio:true});
        const audio=new (window.AudioContext||window.webkitAudioContext)();
        micAnalyser=audio.createAnalyser(); micAnalyser.fftSize=256; micData=new Uint8Array(micAnalyser.fftSize);
        audio.createMediaStreamSource(micStream).connect(micAnalyser);
      } catch (_) { micAnalyser=null; micData=null; }
    }
    function stopMicMeter() { if(micStream) micStream.getTracks().forEach(track=>track.stop()); micStream=null; micAnalyser=null; micData=null; }
    function setupVoice() {
      const listen=document.getElementById('listen'), draft=document.getElementById('voiceDraft');
      const open=document.getElementById('stageCommand');
      const Recognition=window.SpeechRecognition||window.webkitSpeechRecognition;
      draft.addEventListener('input',()=>{ open.disabled=!draft.value.trim(); });
      open.addEventListener('click',stageVoiceCommand);
      if(!Recognition){ listen.title='Voice transcription is unavailable; type a draft or open the HUD'; state.textContent='VOICE READY'; return; }
      recognition=new Recognition(); recognition.continuous=false; recognition.interimResults=true; recognition.lang=navigator.language||'en-US';
      recognition.onstart=()=>{ listen.setAttribute('aria-pressed','true'); state.textContent='LISTENING'; startMicMeter(); };
      recognition.onresult=event=>{ let text=''; for(let i=event.resultIndex;i<event.results.length;i++) text+=event.results[i][0].transcript;
        draft.value=text.trim(); open.disabled=!draft.value; };
      recognition.onerror=()=>{ state.textContent='VOICE RETRY'; };
      recognition.onend=()=>{ listen.setAttribute('aria-pressed','false'); stopMicMeter(); if(state.textContent==='LISTENING')state.textContent='LOCAL'; };
      listen.addEventListener('click',()=>{ if(listen.getAttribute('aria-pressed')==='true')recognition.stop();else try{recognition.start();}catch(_){} });
    }
    document.getElementById('open').addEventListener('click', openHud);
    document.getElementById('stage').addEventListener('dblclick', openHud);
    document.getElementById('close').addEventListener('click', exitJarvis);
    document.querySelectorAll('.node').forEach(node => node.addEventListener('click', () => navigate(node.dataset.target)));
    const menu=document.getElementById('menu'), toggle=document.getElementById('menuToggle');
    function setMenu(open) { menu.hidden=!open; toggle.setAttribute('aria-expanded', String(open)); if(open) menu.querySelector('button').focus(); }
    toggle.addEventListener('click', () => setMenu(menu.hidden));
    menu.addEventListener('click', event => {
      const action=event.target.dataset.menu; if(!action) return;
      if(action==='hud') openHud(); else if(action==='projects'||action==='settings') navigate(action);
      else if(action==='exit') exitJarvis(); else if(action==='motion') {
        motionPaused=!motionPaused; event.target.textContent=motionPaused?'Resume motion':'Pause motion';
      }
      if(action!=='motion') setMenu(false);
    });
    document.addEventListener('keydown', event => { if(event.key==='Escape') setMenu(false); });
    window.addEventListener('resize', size); size(); setupVoice(); requestAnimationFrame(draw);
    window.addEventListener('pywebviewready', async () => {
      try { const info=await window.pywebview.api.status(); state.textContent=info.mode.toUpperCase(); } catch (_) {}
    });
  </script>
</body>
</html>"""


class DesktopError(RuntimeError):
    """An actionable desktop startup or shutdown failure."""


@dataclass(frozen=True, slots=True)
class CompanionStatus:
    """Public, non-sensitive desktop shell state."""

    active: bool = False
    mode: str = "inactive"
    companion_available: bool = False
    companion_visible: bool = False
    desktop_attached: bool = False
    true_alpha: bool = False
    alpha_pixel_verified: bool = False
    non_activating: bool = False
    taskbar_hidden: bool = False
    hotkey_registered: bool = False
    fallback_reason: str | None = None
    updated_at: str | None = None


def _fit_companion_box(
    requested_size: int,
    position: str,
    width: int,
    height: int,
    *,
    scale: float = 1.0,
) -> tuple[int, int, int, int]:
    """Fit the circular companion and footer entirely inside a work area."""

    width = max(1, int(width))
    height = max(1, int(height))
    requested = max(1, round(requested_size * scale))
    footer = min(round(100 * scale), max(0, height - 1))
    requested_margin = max(
        0,
        min(width - requested, height - footer - requested) // 2,
    )
    margin = min(
        round(28 * scale),
        width // 20,
        height // 20,
        requested_margin,
    )
    maximum = max(1, min(width - (2 * margin), height - (2 * margin) - footer))
    size = min(requested, maximum)
    total_height = min(height, size + footer)
    x = margin if position == "bottom_left" else max(margin, width - size - margin)
    y = max(margin, height - total_height - margin)
    return size, total_height, x, y


def _companion_requested(preference: DesktopPreference) -> bool:
    """Return the retired companion state for legacy callers."""

    del preference
    return False


def _write_desktop_status(data_dir: Path, status: CompanionStatus) -> None:
    """Atomically publish runtime state for the existing settings endpoint."""

    payload = {
        **asdict(status),
        "pid": os.getpid(),
        "process_created_at": psutil.Process().create_time(),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    target = data_dir / "desktop-status.json"
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=data_dir,
            prefix=".jarvis-desktop-status-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, target)
    finally:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)


class _Rect(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class _Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _Message(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", _Point),
        ("lPrivate", wintypes.DWORD),
    ]


class WindowsDesktopLayer:
    """Apply documented Win32 styles and attach to the existing desktop host.

    The class deliberately does not send undocumented messages to Explorer to
    create WorkerW windows. If the shell's existing desktop host is unavailable,
    it keeps a normal low-priority transparent companion as a safe fallback.
    """

    GWL_STYLE = -16
    GWL_EXSTYLE = -20
    WS_CHILD = 0x40000000
    WS_POPUP = 0x80000000
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_APPWINDOW = 0x00040000
    WS_EX_NOACTIVATE = 0x08000000
    HWND_BOTTOM = 1
    HWND_TOP = 0
    HWND_NOTOPMOST = -2
    SW_SHOWNOACTIVATE = 4
    SW_RESTORE = 9
    SWP_NOSIZE = 0x0001
    SWP_NOMOVE = 0x0002
    SWP_NOACTIVATE = 0x0010
    SWP_FRAMECHANGED = 0x0020
    SWP_SHOWWINDOW = 0x0040
    SWP_ASYNCWINDOWPOS = 0x4000
    RGN_OR = 2

    def __init__(
        self,
        *,
        user32: object | None = None,
        kernel32: object | None = None,
        gdi32: object | None = None,
        dwmapi: object | None = None,
        platform_name: str | None = None,
    ) -> None:
        self.supported = (platform_name or os.name) == "nt"
        self.user32 = user32
        self.kernel32 = kernel32
        self.gdi32 = gdi32
        self.dwmapi = dwmapi
        if self.supported and self.user32 is None:
            try:
                self.user32 = ctypes.windll.user32
                self.kernel32 = ctypes.windll.kernel32
                self.gdi32 = ctypes.windll.gdi32
                self.dwmapi = ctypes.windll.dwmapi
                self._configure_signatures()
            except (AttributeError, OSError):
                self.supported = False
                self.user32 = None
                self.kernel32 = None
                self.gdi32 = None
                self.dwmapi = None

    def _configure_signatures(self) -> None:
        assert self.user32 is not None
        pointer = ctypes.c_ssize_t
        self.user32.FindWindowW.restype = wintypes.HWND
        self.user32.FindWindowExW.restype = wintypes.HWND
        self.user32.SetParent.restype = wintypes.HWND
        self.user32.GetWindowLongPtrW.restype = pointer
        self.user32.SetWindowLongPtrW.restype = pointer
        self.user32.SetWindowPos.restype = wintypes.BOOL
        self.user32.GetClientRect.restype = wintypes.BOOL

    @staticmethod
    def _window_handle(window: object) -> int:
        native = getattr(window, "native", None)
        handle = getattr(native, "Handle", None)
        if handle is None:
            raise DesktopError("The transparent globe window has no native handle")
        converter = getattr(handle, "ToInt64", None)
        return int(converter() if callable(converter) else handle)

    def _composition_enabled(self) -> bool:
        if not self.supported or self.dwmapi is None:
            return False
        enabled = wintypes.BOOL()
        try:
            result = self.dwmapi.DwmIsCompositionEnabled(ctypes.byref(enabled))
        except (AttributeError, OSError):
            return False
        return result == 0 and bool(enabled.value)

    def _desktop_host(self) -> int | None:
        if self.user32 is None:
            return None
        progman = int(self.user32.FindWindowW("Progman", None) or 0)
        if progman and self.user32.FindWindowExW(progman, 0, "SHELLDLL_DefView", None):
            return progman

        found: list[int] = []
        callback_type = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)

        @callback_type(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def locate(hwnd: int, _parameter: int) -> bool:
            if self.user32.FindWindowExW(hwnd, 0, "SHELLDLL_DefView", None):
                found.append(int(hwnd))
                return False
            return True

        try:
            self.user32.EnumWindows(locate, 0)
        except (AttributeError, OSError):
            return None
        return found[0] if found else None

    def _set_window_value(self, hwnd: int, index: int, value: int) -> bool:
        if self.user32 is None or self.kernel32 is None:
            return False
        self.kernel32.SetLastError(0)
        previous = self.user32.SetWindowLongPtrW(hwnd, index, value)
        return bool(previous) or self.kernel32.GetLastError() == 0

    def _extended_styles(self, hwnd: int) -> tuple[bool, bool]:
        assert self.user32 is not None
        current = int(self.user32.GetWindowLongPtrW(hwnd, self.GWL_EXSTYLE))
        desired = (current | self.WS_EX_TOOLWINDOW | self.WS_EX_NOACTIVATE) & ~self.WS_EX_APPWINDOW
        applied = self._set_window_value(hwnd, self.GWL_EXSTYLE, desired)
        return applied and bool(desired & self.WS_EX_NOACTIVATE), applied and bool(
            desired & self.WS_EX_TOOLWINDOW
        )

    def _set_companion_region(self, hwnd: int, size: int, height: int) -> None:
        if self.user32 is None or self.gdi32 is None:
            return
        ellipse = self.gdi32.CreateEllipticRgn(0, 0, size, size)
        footer = self.gdi32.CreateRectRgn(size // 12, size - 8, size * 11 // 12, height)
        combined = self.gdi32.CreateRectRgn(0, 0, 0, 0)
        try:
            if ellipse and footer and combined:
                self.gdi32.CombineRgn(combined, ellipse, footer, self.RGN_OR)
                if self.user32.SetWindowRgn(hwnd, combined, True):
                    combined = 0  # The system owns the successful region.
        finally:
            for region in (ellipse, footer, combined):
                if region:
                    self.gdi32.DeleteObject(region)

    def _position_in_host(
        self,
        hwnd: int,
        host: int,
        preference: DesktopPreference,
        *,
        visible: bool,
    ) -> bool:
        assert self.user32 is not None
        rect = _Rect()
        if not self.user32.GetClientRect(host, ctypes.byref(rect)):
            return False
        dpi_getter = getattr(self.user32, "GetDpiForWindow", None)
        dpi = int(dpi_getter(hwnd)) if callable(dpi_getter) else 96
        scale = max(1.0, dpi / 96.0)
        size, height, x, y = _fit_companion_box(
            preference.transparent_globe_size,
            preference.transparent_globe_position,
            rect.right - rect.left,
            rect.bottom - rect.top,
            scale=scale,
        )
        self._set_companion_region(hwnd, size, height)
        # SHELLDLL_DefView owns the desktop icons.  Insert immediately behind
        # it so the globe remains above wallpaper providers/WorkerW children,
        # while the Progman parent keeps it below every ordinary application.
        icon_view = int(
            self.user32.FindWindowExW(host, 0, "SHELLDLL_DefView", None) or 0
        )
        insert_after = icon_view or self.HWND_TOP
        flags = (
            self.SWP_NOACTIVATE | self.SWP_FRAMECHANGED | self.SWP_ASYNCWINDOWPOS
        )
        if visible:
            flags |= self.SWP_SHOWWINDOW
        return bool(
            self.user32.SetWindowPos(
                hwnd,
                insert_after,
                x,
                y,
                size,
                height,
                flags,
            )
        )

    def apply(
        self,
        window: object,
        preference: DesktopPreference,
        *,
        visible: bool = True,
    ) -> CompanionStatus:
        """Apply alpha, no-activation, taskbar, and desktop-host behavior."""

        if not self.supported or self.user32 is None:
            return CompanionStatus(
                mode="full_app_fallback",
                fallback_reason="Transparent globe mode is supported only on Windows",
            )
        if not self._composition_enabled():
            return CompanionStatus(
                mode="full_app_fallback",
                fallback_reason="Windows composition did not report true-alpha support",
            )
        try:
            hwnd = self._window_handle(window)
        except DesktopError as exc:
            return CompanionStatus(mode="full_app_fallback", fallback_reason=str(exc))

        non_activating, taskbar_hidden = self._extended_styles(hwnd)
        host = self._desktop_host()
        fallback_reason: str | None = None
        if host is not None and self.kernel32 is not None:
            original_style = int(self.user32.GetWindowLongPtrW(hwnd, self.GWL_STYLE))
            child_style = (original_style | self.WS_CHILD) & ~self.WS_POPUP
            if self._set_window_value(hwnd, self.GWL_STYLE, child_style):
                self.kernel32.SetLastError(0)
                previous_parent = self.user32.SetParent(hwnd, host)
                parent_error = self.kernel32.GetLastError()
                parented = bool(previous_parent) or parent_error == 0
                if parented and self._position_in_host(
                    hwnd, host, preference, visible=visible
                ):
                    if visible:
                        self.user32.ShowWindow(hwnd, self.SW_SHOWNOACTIVATE)
                    return CompanionStatus(
                        active=visible,
                        mode="desktop_child" if visible else "full_app",
                        companion_available=True,
                        companion_visible=visible,
                        desktop_attached=True,
                        true_alpha=True,
                        non_activating=non_activating,
                        taskbar_hidden=taskbar_hidden,
                    )
                self.user32.SetParent(hwnd, 0)
                self._set_window_value(hwnd, self.GWL_STYLE, original_style)
                fallback_reason = "Explorer rejected desktop-host attachment"
            else:
                fallback_reason = "Windows rejected companion child-window styling"
        else:
            fallback_reason = "Explorer desktop host was not available"

        flags = (
            self.SWP_NOMOVE
            | self.SWP_NOSIZE
            | self.SWP_NOACTIVATE
            | self.SWP_FRAMECHANGED
        )
        if visible:
            flags |= self.SWP_SHOWWINDOW
        self.user32.SetWindowPos(
            hwnd,
            self.HWND_NOTOPMOST,
            0,
            0,
            0,
            0,
            flags,
        )
        if visible:
            self.user32.ShowWindow(hwnd, self.SW_SHOWNOACTIVATE)
        return CompanionStatus(
            active=visible,
            mode="floating_fallback" if visible else "full_app",
            companion_available=True,
            companion_visible=visible,
            true_alpha=True,
            non_activating=non_activating,
            taskbar_hidden=taskbar_hidden,
            fallback_reason=fallback_reason,
        )

    def show_without_activation(self, window: object) -> None:
        if not self.supported or self.user32 is None:
            return
        try:
            self.user32.ShowWindow(self._window_handle(window), self.SW_SHOWNOACTIVATE)
        except DesktopError:
            pass

    def activate(self, window: object) -> None:
        if not self.supported or self.user32 is None:
            return
        try:
            hwnd = self._window_handle(window)
        except DesktopError:
            return
        self.user32.ShowWindow(hwnd, self.SW_RESTORE)
        self.user32.SetForegroundWindow(hwnd)


class CompanionHotkey:
    """Own the documented RegisterHotKey message loop for Ctrl+Alt+J."""

    HOTKEY_ID = 0x4A17
    WM_HOTKEY = 0x0312
    WM_QUIT = 0x0012
    MOD_ALT = 0x0001
    MOD_CONTROL = 0x0002
    MOD_NOREPEAT = 0x4000

    def __init__(self, callback: object) -> None:
        self.callback = callback
        self.thread: threading.Thread | None = None
        self.thread_id: int | None = None
        self.registered = False
        self._ready = threading.Event()

    def start(self, timeout: float = 2.0) -> bool:
        if os.name != "nt":
            return False
        self.thread = threading.Thread(target=self._run, name="jarvis-globe-hotkey", daemon=True)
        self.thread.start()
        self._ready.wait(timeout)
        return self.registered

    def _run(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self.thread_id = int(kernel32.GetCurrentThreadId())
        self.registered = bool(
            user32.RegisterHotKey(
                None,
                self.HOTKEY_ID,
                self.MOD_CONTROL | self.MOD_ALT | self.MOD_NOREPEAT,
                ord("J"),
            )
        )
        self._ready.set()
        if not self.registered:
            return
        message = _Message()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            if message.message == self.WM_HOTKEY and message.wParam == self.HOTKEY_ID:
                try:
                    self.callback()
                except (DesktopError, OSError, RuntimeError, ValueError):
                    continue
        user32.UnregisterHotKey(None, self.HOTKEY_ID)
        self.registered = False

    def stop(self) -> None:
        if os.name == "nt" and self.thread_id is not None and self.thread is not None:
            ctypes.windll.user32.PostThreadMessageW(self.thread_id, self.WM_QUIT, 0, 0)
            self.thread.join(timeout=2)
        self.registered = False


class CompanionController:
    """Coordinate the globe, full HUD, return hotkey, and clean shutdown."""

    def __init__(
        self,
        full_window: object,
        companion_window: object,
        preference: DesktopPreference,
        data_dir: Path,
        layer: WindowsDesktopLayer | None = None,
        preferences: PreferencesService | None = None,
        initial_visible: bool | None = None,
    ) -> None:
        self.full_window = full_window
        self.companion_window = companion_window
        self.preference = preference
        self.data_dir = data_dir
        self.layer = layer or WindowsDesktopLayer()
        self.preferences = preferences or PreferencesService(data_dir / "settings.json")
        self.initial_visible = (
            preference.transparent_globe_enabled
            if initial_visible is None
            else initial_visible
        )
        self.hotkey = CompanionHotkey(self.restore_full_app)
        self.current_status = CompanionStatus(mode="starting")
        self.shutting_down = False
        self._lock = threading.RLock()

    def _wait_for_companion_window(self, timeout: float = 20.0) -> None:
        """Wait for pywebview to create the second window's native HWND.

        ``webview.start(func=...)`` can run its callback while additional native
        windows are still being created. Polling the native handle also works
        when the companion intentionally starts hidden and never fires ``shown``.
        """

        deadline = time.monotonic() + timeout
        while (
            getattr(self.companion_window, "native", None) is None
            and time.monotonic() < deadline
        ):
            time.sleep(0.02)

    def start(self) -> None:
        self._wait_for_companion_window()
        try:
            status = self.layer.apply(
                self.companion_window,
                self.preference,
                visible=self.initial_visible,
            )
        except (
            AttributeError,
            ctypes.ArgumentError,
            DesktopError,
            OSError,
            RuntimeError,
            ValueError,
        ) as exc:
            status = CompanionStatus(
                mode="full_app_fallback",
                fallback_reason=(f"Windows companion setup failed safely: {type(exc).__name__}"),
            )
        if status.companion_available:
            hotkey_registered = self.hotkey.start()
            status = replace(status, hotkey_registered=hotkey_registered)
            if not self.initial_visible:
                self.companion_window.hide()
        else:
            self.companion_window.hide()
        self.full_window.show()
        self._publish(status)

    def _publish(self, status: CompanionStatus) -> None:
        with self._lock:
            self.current_status = replace(status, updated_at=datetime.now(UTC).isoformat())
            try:
                _write_desktop_status(self.data_dir, self.current_status)
            except (OSError, psutil.Error):
                return

    def status(self) -> dict[str, object]:
        return asdict(self.current_status)

    def restore_full_app(self) -> dict[str, object]:
        with self._lock:
            if self.shutting_down:
                return self.status()
            self.full_window.show()
            self.full_window.restore()
            self.layer.activate(self.full_window)
            return self.status()

    def _visibility_result(
        self, requested_visible: bool, applied: bool
    ) -> dict[str, object]:
        return {
            "applied": applied,
            "requested_visible": requested_visible,
            "preference_enabled": self.preference.transparent_globe_enabled,
            "runtime": self.status(),
        }

    def _persist_visibility(self, enabled: bool) -> None:
        self.preferences.update(
            PreferencesUpdate.model_validate(
                {"desktop": {"transparent_globe_enabled": enabled}}
            )
        )
        self.preference = self.preferences.desktop()

    def set_companion_visibility(self, visible: bool) -> dict[str, object]:
        """Apply and persist one strict visibility choice without hiding the HUD."""

        if not isinstance(visible, bool):
            raise TypeError("Companion visibility must be boolean")
        with self._lock:
            if self.shutting_down:
                return self._visibility_result(visible, False)
            if not visible:
                try:
                    self.companion_window.hide()
                    self._persist_visibility(False)
                except (AttributeError, OSError, RuntimeError, ValueError):
                    self.full_window.show()
                    return self._visibility_result(False, False)
                self._publish(
                    replace(
                        self.current_status,
                        active=False,
                        mode="full_app",
                        companion_visible=False,
                    )
                )
                return self._visibility_result(False, True)

            self.full_window.show()
            try:
                current_preference = self.preferences.desktop()
                capability = self.layer.apply(
                    self.companion_window,
                    current_preference,
                    visible=False,
                )
                if not capability.companion_available:
                    self.companion_window.hide()
                    self.full_window.restore()
                    self.layer.activate(self.full_window)
                    self._publish(capability)
                    return self._visibility_result(True, False)
                self.companion_window.show()
                self.layer.show_without_activation(self.companion_window)
                self._persist_visibility(True)
            except (
                AttributeError,
                ctypes.ArgumentError,
                DesktopError,
                OSError,
                RuntimeError,
                ValueError,
            ) as exc:
                try:
                    self.companion_window.hide()
                except (AttributeError, OSError, RuntimeError):
                    pass
                self.full_window.show()
                self.full_window.restore()
                self.layer.activate(self.full_window)
                self._publish(
                    replace(
                        self.current_status,
                        active=False,
                        mode="full_app_fallback",
                        companion_visible=False,
                        fallback_reason=(
                            f"Live companion show failed safely: {type(exc).__name__}"
                        ),
                    )
                )
                return self._visibility_result(True, False)

            mode = "desktop_child" if capability.desktop_attached else "floating_fallback"
            hotkey_registered = self.current_status.hotkey_registered
            if not hotkey_registered:
                hotkey_registered = self.hotkey.start()
            self._publish(
                replace(
                    capability,
                    active=True,
                    mode=mode,
                    companion_visible=True,
                    hotkey_registered=hotkey_registered,
                    fallback_reason=capability.fallback_reason,
                )
            )
            return self._visibility_result(True, True)

    def open_destination(self, destination: str) -> dict[str, object]:
        """Restore the HUD and navigate using a fixed, script-injection-free map."""

        scripts = {
            "projects": "document.getElementById('projectsPanel')?.scrollIntoView({block:'start'});",
            "settings": "document.getElementById('openSettings')?.click();",
            "angerona": (
                "document.getElementById('openSettings')?.click();"
                "document.querySelector('[data-settings-target=\"defenseSettings\"]')?.click();"
            ),
            "models": (
                "document.getElementById('openSettings')?.click();"
                "document.querySelector('[data-settings-target=\"connectionSettings\"]')?.click();"
            ),
            "federation": (
                "document.getElementById('openSettings')?.click();"
                "document.querySelector('[data-settings-target=\"federationSettings\"]')?.click();"
            ),
        }
        script = scripts.get(destination)
        if script is None:
            raise ValueError("Unknown companion destination")
        status = self.restore_full_app()
        self.full_window.evaluate_js(script)
        return status

    def stage_command(self, command: str) -> dict[str, object]:
        """Move a bounded voice/text draft into the HUD without submitting it."""

        if not isinstance(command, str):
            raise TypeError("The companion command must be text")
        draft = command.strip()
        if not draft or len(draft) > 2000 or "\x00" in draft:
            raise ValueError("The companion command must contain 1 to 2000 safe characters")
        status = self.restore_full_app()
        encoded = json.dumps(draft, ensure_ascii=True)
        self.full_window.evaluate_js(
            "(()=>{const input=document.getElementById('chatInput');"
            "if(!input)return false;input.value="
            f"{encoded};"
            "input.dispatchEvent(new Event('input',{bubbles:true}));"
            "input.focus();return true;})()"
        )
        return status

    def show_companion(self, *, minimize_full_app: bool = False) -> dict[str, object]:
        result = self.set_companion_visibility(True)
        if result["applied"] and minimize_full_app:
            self.full_window.hide()
        return result

    def on_full_closing(self) -> bool | None:
        if (
            self.shutting_down
            or not self.preference.transparent_globe_enabled
            or not self.current_status.companion_available
        ):
            return None
        result = self.show_companion(minimize_full_app=True)
        if not result["applied"]:
            return None
        return False  # pywebview interprets False as cancel-close.

    def exit_app(self) -> dict[str, bool]:
        with self._lock:
            if self.shutting_down:
                return {"closing": True}
            self.shutting_down = True
        self.hotkey.stop()
        self._publish(CompanionStatus(mode="inactive"))
        try:
            self.companion_window.destroy()
        finally:
            self.full_window.destroy()
        return {"closing": True}

    def stop(self) -> None:
        self.shutting_down = True
        self.hotkey.stop()
        self._publish(CompanionStatus(mode="inactive"))


class CompanionBridge:
    """Expose only lifecycle controls to the isolated companion document."""

    def __init__(self) -> None:
        # pywebview recursively inspects every public object attribute when it
        # builds the JavaScript API. Keep the controller private so native
        # windows and their COM graph can never become bridge-visible.
        self._controller: CompanionController | None = None

    def _bind(self, controller: CompanionController) -> None:
        self._controller = controller

    def restore_full_app(self) -> dict[str, object]:
        if self._controller is None:
            return {"active": False, "mode": "starting"}
        return self._controller.restore_full_app()

    def exit_app(self) -> dict[str, bool]:
        if self._controller is None:
            return {"closing": False}
        return self._controller.exit_app()

    def open_destination(self, destination: str) -> dict[str, object]:
        if self._controller is None:
            return {"active": False, "mode": "starting"}
        return self._controller.open_destination(destination)

    def stage_command(self, command: str) -> dict[str, object]:
        if self._controller is None:
            return {"active": False, "mode": "starting"}
        return self._controller.stage_command(command)

    def status(self) -> dict[str, object]:
        if self._controller is None:
            return {"active": False, "mode": "starting"}
        return self._controller.status()


class HudCompanionBridge:
    """Expose one visibility toggle to the trusted full-HUD WebView only."""

    def __init__(self) -> None:
        self._controller: CompanionController | None = None

    def _bind(self, controller: CompanionController) -> None:
        self._controller = controller

    def set_companion_visibility(self, visible: bool) -> dict[str, object]:
        if not isinstance(visible, bool):
            raise TypeError("Companion visibility must be boolean")
        if self._controller is None:
            return {
                "applied": False,
                "requested_visible": visible,
                "preference_enabled": False,
                "runtime": {
                    "active": False,
                    "mode": "starting",
                    "companion_available": False,
                    "companion_visible": False,
                },
            }
        return self._controller.set_companion_visibility(visible)


def _initial_companion_geometry(
    preference: DesktopPreference,
    work_area: tuple[int, int, int, int] | None = None,
) -> tuple[int, int, int, int]:
    """Fit initial logical size and position inside the primary work area."""

    if work_area is None and os.name == "nt":
        try:
            rect = _Rect()
            if ctypes.windll.user32.SystemParametersInfoW(
                0x0030, 0, ctypes.byref(rect), 0  # SPI_GETWORKAREA
            ):
                dpi_getter = getattr(ctypes.windll.user32, "GetDpiForSystem", None)
                dpi = int(dpi_getter()) if callable(dpi_getter) else 96
                scale = max(1.0, dpi / 96.0)
                work_area = tuple(
                    round(value / scale)
                    for value in (rect.left, rect.top, rect.right, rect.bottom)
                )
        except (AttributeError, OSError, ValueError):
            work_area = None
    left, top, right, bottom = work_area or (0, 0, 1376, 824)
    size, height, x, y = _fit_companion_box(
        preference.transparent_globe_size,
        preference.transparent_globe_position,
        right - left,
        bottom - top,
    )
    return size, height, left + x, top + y


def _initial_companion_position(preference: DesktopPreference) -> tuple[int | None, int | None]:
    if os.name != "nt":
        return None, None
    _, _, x, y = _initial_companion_geometry(preference)
    return x, y


def _initial_hud_geometry(
    work_area: tuple[int, int] | None = None,
) -> tuple[int, int, tuple[int, int]]:
    """Fit the full HUD inside the primary work area at the current DPI."""

    if work_area is None and os.name == "nt":
        try:
            rect = _Rect()
            if ctypes.windll.user32.SystemParametersInfoW(
                0x0030, 0, ctypes.byref(rect), 0  # SPI_GETWORKAREA
            ):
                physical_width = max(0, rect.right - rect.left)
                physical_height = max(0, rect.bottom - rect.top)
                dpi_getter = getattr(ctypes.windll.user32, "GetDpiForSystem", None)
                dpi = int(dpi_getter()) if callable(dpi_getter) else 96
                scale = max(1.0, dpi / 96.0)
                work_area = (
                    round(physical_width / scale),
                    round(physical_height / scale),
                )
        except (AttributeError, OSError, ValueError):
            work_area = None
    usable_width, usable_height = work_area or (1376, 824)
    usable_width = max(1, int(usable_width))
    usable_height = max(1, int(usable_height))
    width = min(1440, usable_width - 96 if usable_width > 416 else usable_width)
    height = min(900, usable_height - 64 if usable_height > 384 else usable_height)
    # Let the native shell reach the web HUD's compact responsive layouts while
    # never advertising a minimum larger than the fitted initial/work-area size.
    return width, height, (min(640, width), min(480, height))


def _initial_hud_position(
    width: int,
    height: int,
    work_area: tuple[int, int, int, int] | None = None,
) -> tuple[int, int]:
    """Center even a hidden HUD inside the primary logical work area."""

    if work_area is None and os.name == "nt":
        try:
            rect = _Rect()
            if ctypes.windll.user32.SystemParametersInfoW(
                0x0030, 0, ctypes.byref(rect), 0  # SPI_GETWORKAREA
            ):
                dpi_getter = getattr(ctypes.windll.user32, "GetDpiForSystem", None)
                dpi = int(dpi_getter()) if callable(dpi_getter) else 96
                scale = max(1.0, dpi / 96.0)
                work_area = tuple(
                    round(value / scale)
                    for value in (rect.left, rect.top, rect.right, rect.bottom)
                )
        except (AttributeError, OSError, ValueError):
            work_area = None
    left, top, right, bottom = work_area or (0, 0, 1376, 824)
    return (
        left + max(0, (right - left - width) // 2),
        top + max(0, (bottom - top - height) // 2),
    )


def resource_root() -> Path:
    """Return the repository or PyInstaller extraction root containing web assets."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS).resolve()  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[2]


def _persistent_data_root() -> Path:
    override = os.getenv("JARVIS_DESKTOP_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    local_app_data = os.getenv("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return (base / "JARVIS").resolve()


def _workspace_root() -> Path:
    override = os.getenv("JARVIS_WORKSPACE_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / "Documents" / "JARVIS Workspace").resolve()


def _brain_root() -> Path:
    return Path(os.getenv("JARVIS_BRAIN_ROOT", r"F:\BRAIN DRIVE\_root")).expanduser().resolve()


def _source_root(asset_root: Path) -> Path | None:
    """Locate editable source without ever treating the packaged bundle as source."""

    override = os.getenv("JARVIS_SOURCE_ROOT")
    if override:
        candidate = Path(override).expanduser()
    elif getattr(sys, "frozen", False):
        candidate = Path(sys.executable).resolve().parent.parent / "Source"
    else:
        candidate = asset_root
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        return None
    return resolved if resolved.is_dir() else None


def desktop_settings(root: Path | None = None) -> Settings:
    """Load model/config choices while keeping writable state outside the app bundle."""
    asset_root = (root or resource_root()).resolve()
    configured = os.getenv("JARVIS_CONFIG") or str(asset_root / "config" / "default.toml")
    settings = load_settings(configured)
    data_root = _persistent_data_root()
    workspace = _workspace_root()
    data_root.mkdir(parents=True, exist_ok=True)
    try:
        workspace.mkdir(parents=True, exist_ok=True)
    except OSError:
        # Controlled Folder Access and redirected Documents folders can reject
        # packaged applications. LocalAppData is the safe per-user fallback.
        workspace = (data_root / "Workspace").resolve()
        workspace.mkdir(parents=True, exist_ok=True)
    settings.project_root = asset_root
    settings.data_dir = data_root
    allowed_roots = [workspace, _brain_root()]
    source_root = _source_root(asset_root)
    if source_root is not None and source_root not in allowed_roots:
        allowed_roots.append(source_root)
    settings.allowed_roots = tuple(allowed_roots)
    settings.host = LOOPBACK_HOST
    settings.port = int(os.getenv("JARVIS_DESKTOP_PORT", str(settings.port or DEFAULT_PORT)))
    settings.validate()
    return settings


def _is_jarvis_server(host: str, port: int, timeout: float = 0.75) -> bool:
    try:
        with urlopen(f"http://{host}:{port}/api", timeout=timeout) as response:
            payload = json.load(response)
    except (OSError, URLError, ValueError, TimeoutError):
        return False
    return response.status == 200 and payload.get("name") == "JARVIS Local API"


class LocalServer:
    """Own one in-process Uvicorn server, or reuse an already-running JARVIS origin."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.server: uvicorn.Server | None = None
        self.thread: threading.Thread | None = None
        self.socket: socket.socket | None = None
        self.owns_server = False

    @property
    def url(self) -> str:
        return f"http://{self.settings.host}:{self.settings.port}/"

    def start(self, timeout: float = 30.0) -> None:
        if _is_jarvis_server(self.settings.host, self.settings.port):
            return
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind((self.settings.host, self.settings.port))
            listener.listen(2048)
        except OSError as exc:
            listener.close()
            raise DesktopError(
                f"Port {self.settings.port} is already in use by another application."
            ) from exc
        self.socket = listener
        config = uvicorn.Config(
            create_app(self.settings),
            host=self.settings.host,
            port=self.settings.port,
            log_config=None,
            access_log=False,
            lifespan="on",
            timeout_graceful_shutdown=10,
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(
            target=self.server.run,
            kwargs={"sockets": [listener]},
            name="jarvis-desktop-server",
            daemon=True,
        )
        self.owns_server = True
        self.thread.start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if _is_jarvis_server(self.settings.host, self.settings.port):
                return
            if self.thread is not None and not self.thread.is_alive():
                break
            time.sleep(0.1)
        self.stop()
        raise DesktopError("The local JARVIS service did not start before the desktop timeout.")

    def stop(self) -> None:
        if not self.owns_server:
            return
        if self.server is not None:
            self.server.should_exit = True
        if self.thread is not None:
            self.thread.join(timeout=15)
            if self.thread.is_alive() and self.server is not None:
                self.server.force_exit = True
                self.thread.join(timeout=3)
        if self.socket is not None:
            try:
                self.socket.close()
            except OSError:
                pass
        self.owns_server = False


def _show_error(message: str) -> None:
    if os.name == "nt":
        ctypes.windll.user32.MessageBoxW(None, message, "JARVIS could not start", 0x10)
    elif sys.stderr is not None:
        print(message, file=sys.stderr)


def main() -> int:
    """Run the local API and render the exact browser HUD in Edge WebView2."""
    multiprocessing.freeze_support()
    server: LocalServer | None = None
    try:
        settings = desktop_settings()
        preferences = PreferencesService(settings.data_dir / "settings.json")
        desktop_preference = preferences.desktop()
        if desktop_preference.transparent_globe_enabled:
            preferences.update(
                PreferencesUpdate.model_validate(
                    {"desktop": {"transparent_globe_enabled": False}}
                )
            )
        server = LocalServer(settings)
        server.start()

        import webview

        webview.settings["ALLOW_DOWNLOADS"] = False
        webview.settings["ALLOW_FILE_URLS"] = False
        webview.settings["OPEN_DEVTOOLS_IN_DEBUG"] = False
        webview.settings["SHOW_DEFAULT_MENUS"] = False
        hud_width, hud_height, hud_min_size = _initial_hud_geometry()
        hud_x, hud_y = _initial_hud_position(hud_width, hud_height)
        _write_desktop_status(
            settings.data_dir,
            CompanionStatus(mode="full_app"),
        )
        full_window = webview.create_window(
            APP_TITLE,
            f"{server.url}/?app_version={__version__}",
            width=hud_width,
            height=hud_height,
            x=hud_x,
            y=hud_y,
            min_size=hud_min_size,
            resizable=True,
            background_color="#071015",
            text_select=True,
        )
        if full_window is None:
            raise DesktopError("The full JARVIS window could not be created")
        webview.start(
            gui="edgechromium",
            debug=False,
            private_mode=False,
            storage_path=str(settings.data_dir / "webview"),
        )
        return 0
    except (DesktopError, ImportError, OSError, RuntimeError, ValueError) as exc:
        _show_error(str(exc))
        return 1
    finally:
        if server is not None:
            server.stop()


if __name__ == "__main__":
    raise SystemExit(main())
