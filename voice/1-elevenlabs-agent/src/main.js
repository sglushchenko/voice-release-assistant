// Experiment 1: talk to a hosted ElevenLabs agent from the browser; its data tools run here as
// client tools and call our own API. The agent id is not a secret, so it lives in localStorage.
// A private agent needs a server-issued signed URL instead; that comes in a later step.
import "./style.css";
import { Conversation } from "@elevenlabs/client";
import { login } from "./api.js";
import { clientTools } from "./tools.js";
import { runDemo } from "./demo.js";

const $ = (id) => document.getElementById(id);
const orb = $("orb");
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;

$("agent").value = localStorage.getItem("agentId") ?? "";

let conversation = null;
let muted = false;
let mode = "listening";
// Rough on purpose: from the last final user transcript to the agent starting to speak. It
// includes the provider's speech-to-text finalisation, so compare it only with numbers measured
// the same way in the other experiments.
let lastUserTranscriptAt = null;

// ---- view ------------------------------------------------------------------------------

function addBubble(role, text) {
  $("empty")?.remove();
  const bubble = document.createElement("p");
  bubble.className = `bubble ${role}`;
  bubble.textContent = text;
  $("log").append(bubble);
  bubble.scrollIntoView({ block: "end" });
}

function addNote(text) {
  $("empty")?.remove();
  const note = document.createElement("p");
  note.className = "note";
  note.textContent = text;
  $("log").append(note);
  note.scrollIntoView({ block: "end" });
}

/** Shows a tool call as a small chip; the returned function marks it finished. */
function addChip(name, args) {
  $("empty")?.remove();
  const chip = document.createElement("span");
  chip.className = "chip pending";
  const values = Object.values(args ?? {}).join(", ");
  chip.textContent = values ? `${name}(${values})` : name;
  $("log").append(chip);
  chip.scrollIntoView({ block: "end" });
  const started = performance.now();
  return (failed, measuredSeconds) => {
    const seconds = (measuredSeconds ?? (performance.now() - started) / 1000).toFixed(1);
    chip.className = `chip ${failed ? "failed" : "done"}`;
    const took = document.createElement("small");
    took.textContent = `${seconds} s`;
    chip.append(took);
  };
}

function setState(state, text) {
  orb.dataset.state = state;
  $("state").textContent = text;
}

function showLatency(ms) {
  $("latency").hidden = false;
  $("latency").textContent = `Last reply in ${(ms / 1000).toFixed(1)} s`;
}

function setRunning(running) {
  const talk = $("talk");
  talk.dataset.running = String(running);
  talk.textContent = running ? "End conversation" : "Start talking";
  $("mute").disabled = !running;
}

// ---- the orb follows the real audio level ----------------------------------------------

let level = 0;
let frame = 0;
// Runs only while a conversation exists, so an idle page does no work at all.
function animate() {
  if (!conversation) {
    frame = 0;
    return;
  }
  if (!reducedMotion) {
    const raw = mode === "speaking" ? conversation.getOutputVolume() : conversation.getInputVolume();
    // Smoothing keeps the orb from flickering on every audio frame.
    const next = level + (Math.min(raw * 1.6, 1) - level) * 0.25;
    if (Math.abs(next - level) > 0.004) orb.style.setProperty("--level", next.toFixed(3));
    level = next;
  }
  frame = requestAnimationFrame(animate);
}

// ---- tools, with a visible trace -------------------------------------------------------

function tracedTools() {
  return Object.fromEntries(
    Object.entries(clientTools).map(([name, run]) => [
      name,
      async (params) => {
        const finish = addChip(name, params);
        const result = await run(params);
        finish(String(result).includes('"error"'));
        return result;
      },
    ]),
  );
}

// ---- controls --------------------------------------------------------------------------

$("open-settings").addEventListener("click", () => $("settings").showModal());

$("login").addEventListener("click", async () => {
  try {
    await login($("user").value.trim(), $("password").value);
    $("password").value = "";
    $("login-state").textContent = "Logged in. The assistant can now read your data.";
  } catch (error) {
    $("login-state").textContent = `${error.message}. Check the username and password.`;
  }
});

async function start() {
  const agentId = $("agent").value.trim();
  if (!agentId) {
    $("settings").showModal();
    return;
  }
  localStorage.setItem("agentId", agentId);
  setState("connecting", "Connecting");
  try {
    // Starting a session asks for the microphone; the browser prompt must be accepted once.
    conversation = await Conversation.startSession({
      agentId,
      connectionType: "webrtc",
      clientTools: tracedTools(),
      // Fires when the agent asks for a tool that is not in clientTools: the usual sign that the
      // name in the dashboard differs from the one in tools.js.
      onUnhandledClientToolCall: ({ tool_name }) =>
        addNote(`The agent asked for a tool called ${tool_name}, which this page does not have.`),
      onDisconnect: (details) => {
        if (details.reason === "error") addNote(`Connection lost: ${details.message}`);
        conversation = null;
        level = 0;
        orb.style.removeProperty("--level");
        setRunning(false);
        setState("idle", "Conversation ended");
      },
      onError: (message) => addNote(`Error: ${message}`),
      onModeChange: ({ mode: next }) => {
        mode = next;
        if (muted) return;
        setState(next, next === "speaking" ? "Assistant is speaking" : "Listening");
        if (next === "speaking" && lastUserTranscriptAt !== null) {
          showLatency(performance.now() - lastUserTranscriptAt);
          lastUserTranscriptAt = null;
        }
      },
      onMessage: ({ role, message }) => {
        if (role === "user") lastUserTranscriptAt = performance.now();
        addBubble(role === "user" ? "user" : "agent", message);
      },
    });
    setRunning(true);
    setState("listening", "Listening");
    if (!frame) frame = requestAnimationFrame(animate);
  } catch (error) {
    addNote(`Could not start: ${error.message ?? error}`);
    setState("idle", "Ready when you are");
  }
}

$("talk").addEventListener("click", async () => {
  if (conversation) await conversation.endSession();
  else await start();
});

$("mute").addEventListener("click", () => {
  muted = !muted;
  conversation?.setMicMuted(muted);
  $("mute").setAttribute("aria-pressed", String(muted));
  $("mute-slash").hidden = !muted;
  setState(muted ? "muted" : mode, muted ? "Microphone is muted" : "Listening");
});

// A scripted conversation on invented data: for screenshots and demos, without a microphone,
// a login or any of the user's real apps in the frame.
if (new URLSearchParams(location.search).has("demo")) runDemo({ addBubble, addChip, setState, showLatency, setRunning, orb });
