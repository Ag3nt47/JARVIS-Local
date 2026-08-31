import { FilesetResolver, HandLandmarker } from "./vendor/mediapipe/vision_bundle.mjs";

const PINCH_ON = 0.32;
const PINCH_OFF = 0.45;
const SMOOTHING = 0.4;
const MIN_CONFIDENCE = 0.68;
const STABLE_FRAMES = 3;

function distance(a, b) {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

function clamp(value, minimum, maximum) {
  return Math.min(maximum, Math.max(minimum, value));
}

function extended(landmarks, tip, pip) {
  return distance(landmarks[tip], landmarks[0]) > distance(landmarks[pip], landmarks[0]) * 1.14;
}

function handShape(landmarks) {
  const fingers = [
    extended(landmarks, 8, 6),
    extended(landmarks, 12, 10),
    extended(landmarks, 16, 14),
    extended(landmarks, 20, 18)
  ];
  if (fingers.every((value) => !value)) return "fist";
  if (fingers[0] && fingers[1] && fingers[2] && !fingers[3]) return "three";
  if (fingers[0] && fingers[1] && !fingers[2] && !fingers[3]) return "two";
  return "point";
}

class JarvisGestureController extends EventTarget {
  constructor({ target = document.documentElement, lowPower = false } = {}) {
    super();
    this.target = target;
    this.lowPower = lowPower;
    this.running = false;
    this.stopped = false;
    this.video = document.createElement("video");
    this.video.playsInline = true;
    this.video.muted = true;
    this.video.setAttribute("aria-hidden", "true");
    this.landmarker = null;
    this.stream = null;
    this.frameRequest = 0;
    this.lastFrameAt = 0;
    this.smoothed = null;
    this.pinch = false;
    this.pinchCandidateFrames = 0;
    this.zoomReference = null;
    this.scrollReference = null;
    this.swipeReference = null;
    this.fistFrames = 0;
    this.stopCooldownUntil = 0;
    this.cursor = document.createElement("div");
    this.cursor.className = "gesture-cursor";
    this.cursor.hidden = true;
    this.cursor.setAttribute("aria-hidden", "true");
    document.body.append(this.cursor);
  }

  emit(type, detail = {}) {
    const event = new CustomEvent(`jarvis:${type}`, { detail: { ...detail, source: "gesture" } });
    this.dispatchEvent(event);
    window.dispatchEvent(new CustomEvent(`jarvis:${type}`, { detail: event.detail }));
  }

  async initialize() {
    if (this.landmarker) return;
    const files = await FilesetResolver.forVisionTasks("./vendor/mediapipe");
    const options = {
      baseOptions: {
        modelAssetPath: "./vendor/mediapipe/hand_landmarker.task",
        delegate: "GPU"
      },
      runningMode: "VIDEO",
      numHands: 2,
      minHandDetectionConfidence: MIN_CONFIDENCE,
      minHandPresenceConfidence: MIN_CONFIDENCE,
      minTrackingConfidence: 0.62
    };
    try {
      this.landmarker = await HandLandmarker.createFromOptions(files, options);
    } catch {
      options.baseOptions.delegate = "CPU";
      this.landmarker = await HandLandmarker.createFromOptions(files, options);
    }
  }

  async start() {
    if (this.running) return;
    this.stopped = false;
    await this.initialize();
    if (!navigator.mediaDevices?.getUserMedia) throw new Error("Camera access is unavailable.");
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: { facingMode: "user", width: { ideal: 960 }, height: { ideal: 540 }, frameRate: { ideal: this.lowPower ? 12 : 30 } }
    });
    this.video.srcObject = this.stream;
    await this.video.play();
    this.running = true;
    this.cursor.hidden = false;
    this.emit("gesture-status", { state: "running", framesStored: false, uploaded: false });
    this.frameRequest = requestAnimationFrame((timestamp) => this.frame(timestamp));
  }

  stop(reason = "operator") {
    this.running = false;
    cancelAnimationFrame(this.frameRequest);
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
    this.video.srcObject = null;
    this.cursor.hidden = true;
    this.resetModes();
    this.emit("gesture-status", { state: "stopped", reason, framesStored: false, uploaded: false });
  }

  emergencyStop() {
    this.stopped = true;
    this.stop("fist");
    this.emit("emergency-stop", { reason: "fist" });
  }

  setLowPower(enabled) {
    this.lowPower = Boolean(enabled);
  }

  resetModes() {
    this.pinch = false;
    this.pinchCandidateFrames = 0;
    this.zoomReference = null;
    this.scrollReference = null;
    this.swipeReference = null;
    this.fistFrames = 0;
  }

  boundedPoint(landmark) {
    const rectangle = this.target.getBoundingClientRect();
    const x = clamp(rectangle.left + (1 - landmark.x) * rectangle.width, rectangle.left, rectangle.right);
    const y = clamp(rectangle.top + landmark.y * rectangle.height, rectangle.top, rectangle.bottom);
    if (!this.smoothed) this.smoothed = { x, y };
    this.smoothed.x += (x - this.smoothed.x) * SMOOTHING;
    this.smoothed.y += (y - this.smoothed.y) * SMOOTHING;
    return { x: this.smoothed.x, y: this.smoothed.y };
  }

  updateCursor(point, active = false) {
    this.cursor.style.transform = `translate3d(${point.x}px, ${point.y}px, 0)`;
    this.cursor.classList.toggle("active", active);
  }

  updatePinch(isPinched, point) {
    if (isPinched === this.pinch) {
      this.pinchCandidateFrames = 0;
      if (this.pinch) this.emit("gesture-drag", point);
      return;
    }
    this.pinchCandidateFrames += 1;
    if (this.pinchCandidateFrames < STABLE_FRAMES) return;
    this.pinchCandidateFrames = 0;
    this.pinch = isPinched;
    this.emit(isPinched ? "gesture-pinchstart" : "gesture-pinchend", point);
  }

  processHands(result, now) {
    const hands = result.landmarks || [];
    const scores = result.handednesses || [];
    const accepted = hands.filter((_, index) => (scores[index]?.[0]?.score || 0) >= MIN_CONFIDENCE);
    if (!accepted.length) {
      this.cursor.hidden = true;
      this.resetModes();
      return;
    }
    this.cursor.hidden = false;
    const primary = accepted[0];
    const handScale = Math.max(distance(primary[0], primary[9]), 0.04);
    const pinchRatio = distance(primary[4], primary[8]) / handScale;
    const isPinched = this.pinch ? pinchRatio < PINCH_OFF : pinchRatio < PINCH_ON;
    const point = this.boundedPoint({
      x: (primary[4].x + primary[8].x) / 2,
      y: (primary[4].y + primary[8].y) / 2
    });
    const shape = handShape(primary);
    this.updateCursor(point, isPinched);
    this.emit("gesture-pointer", { ...point, shape, confidence: scores[0]?.[0]?.score || 0 });

    if (shape === "fist") {
      this.fistFrames += 1;
      if (this.fistFrames >= 8 && now >= this.stopCooldownUntil) {
        this.stopCooldownUntil = now + 3000;
        this.emergencyStop();
      }
      return;
    }
    this.fistFrames = 0;

    if (accepted.length === 2) {
      const second = accepted[1];
      const secondScale = Math.max(distance(second[0], second[9]), 0.04);
      const secondRatio = distance(second[4], second[8]) / secondScale;
      if (isPinched && secondRatio < PINCH_ON) {
        const firstCenter = { x: (primary[4].x + primary[8].x) / 2, y: (primary[4].y + primary[8].y) / 2 };
        const secondCenter = { x: (second[4].x + second[8].x) / 2, y: (second[4].y + second[8].y) / 2 };
        const separation = distance(firstCenter, secondCenter);
        if (this.zoomReference) {
          this.emit("gesture-zoom", { factor: clamp(separation / this.zoomReference, 0.85, 1.18), ...point });
        }
        this.zoomReference = separation;
        return;
      }
    }
    this.zoomReference = null;

    if (shape === "two") {
      if (this.scrollReference !== null) {
        const delta = (primary[8].y - this.scrollReference) * window.innerHeight;
        if (Math.abs(delta) > 3) this.emit("gesture-scroll", { delta: clamp(delta * 1.8, -120, 120), ...point });
      }
      this.scrollReference = primary[8].y;
      return;
    }
    this.scrollReference = null;

    if (shape === "three") {
      if (this.swipeReference) {
        const deltaX = (primary[8].x - this.swipeReference.x) * window.innerWidth;
        const elapsed = now - this.swipeReference.at;
        if (elapsed < 450 && Math.abs(deltaX) > 90) {
          this.emit("gesture-swipe", { direction: deltaX > 0 ? "left" : "right" });
          this.swipeReference = null;
          return;
        }
      } else {
        this.swipeReference = { x: primary[8].x, at: now };
      }
      return;
    }
    this.swipeReference = null;
    this.updatePinch(isPinched, point);
  }

  frame(timestamp) {
    if (!this.running || this.stopped) return;
    const interval = this.lowPower ? 1000 / 12 : 1000 / 30;
    if (this.video.readyState >= 2 && timestamp - this.lastFrameAt >= interval) {
      this.lastFrameAt = timestamp;
      try {
        this.processHands(this.landmarker.detectForVideo(this.video, performance.now()), timestamp);
      } catch (error) {
        this.stop("tracking-error");
        this.emit("gesture-error", { message: error?.message || "Hand tracking failed." });
        return;
      }
    }
    this.frameRequest = requestAnimationFrame((next) => this.frame(next));
  }
}

window.JarvisGestureController = JarvisGestureController;
window.dispatchEvent(new CustomEvent("jarvis:gesture-ready"));

export { JarvisGestureController };
