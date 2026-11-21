// Open the page with ?demo to see a finished conversation on invented data. The orb moves on a
// synthetic audio level, because there is no real one without a microphone.
export function runDemo({ addBubble, addChip, setState, showLatency, setRunning, orb }) {
  addBubble("user", "Which apps do I have, and is anything waiting for review?");
  const first = addChip("list_apps", {});
  first(false, 0.4);
  const second = addChip("versions_in_review", {});
  second(false, 0.6);
  addBubble(
    "agent",
    "You have two apps: Pocket Notes and Habit Garden. Pocket Notes 1.4.0 is waiting for review; nothing is pending for Habit Garden.",
  );
  addBubble("user", "What is the latest build of Pocket Notes?");
  const third = addChip("latest_build", { app_id: "app-1" });
  third(false, 0.5);
  addBubble("agent", "Build 42 of version 1.4.0, uploaded yesterday. It has finished processing and is ready for TestFlight.");
  showLatency(1100);
  setState("speaking", "Assistant is speaking");
  setRunning(true);

  // Two overlapping sine waves look like speech; a still orb would make a poor screenshot.
  const started = performance.now();
  const tick = () => {
    const t = (performance.now() - started) / 1000;
    const level = Math.max(0, 0.45 + 0.3 * Math.sin(t * 5.1) + 0.2 * Math.sin(t * 2.3 + 1));
    orb.style.setProperty("--level", level.toFixed(3));
    requestAnimationFrame(tick);
  };
  tick();
}
