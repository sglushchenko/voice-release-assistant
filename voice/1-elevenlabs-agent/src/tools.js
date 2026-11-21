// Client tools: functions the hosted agent can call. Each name and parameter must match, letter for
// letter, a Client tool defined in the agent's dashboard, and each tool must have "Wait for
// response" on, otherwise the agent never sees the result.
//
// These run in the browser, so a tampered page could call anything it likes. That is acceptable
// here only because every tool is a read through our API, which does its own authorisation. Writes
// are not exposed as tools at all: the agent can only ask the user, who confirms with a button.
import { get } from "./api.js";

// A voice agent reads results aloud and pays for every token, so tools return trimmed, compact
// JSON rather than whole API documents.
const compact = (value) => JSON.stringify(value);

async function safely(call) {
  try {
    return compact(await call());
  } catch (error) {
    return compact({ error: error.message });
  }
}

export const clientTools = {
  list_apps: () =>
    safely(async () => (await get("/v1/apps")).map(({ id, name }) => ({ id, name }))),

  latest_build: ({ app_id }) =>
    safely(async () => {
      const b = await get(`/v1/apps/${encodeURIComponent(app_id)}/builds/latest`);
      return { version: b.version, build: b.build_number, state: b.processing_state, uploaded: b.uploaded_at };
    }),

  list_builds: ({ app_id, limit }) =>
    safely(async () => {
      const count = Math.min(Number(limit) || 5, 10);
      const builds = await get(`/v1/apps/${encodeURIComponent(app_id)}/builds?limit=${count}`);
      return builds.map((b) => ({ version: b.version, build: b.build_number, state: b.processing_state, uploaded: b.uploaded_at }));
    }),

  versions_in_review: () => safely(() => get("/v1/versions/in-review")),
};
