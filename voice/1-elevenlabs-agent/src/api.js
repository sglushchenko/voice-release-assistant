// A tiny client for our own API. The token lives in memory only: closing the tab ends the login,
// and nothing secret is written to localStorage.
let token = null;

export async function login(username, password) {
  const response = await fetch("/v1/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!response.ok) throw new Error(`login failed (${response.status})`);
  token = (await response.json()).access_token;
}

export const isLoggedIn = () => token !== null;

/** GET a path and return parsed JSON, or throw an Error whose message is safe to read aloud. */
export async function get(path) {
  const response = await fetch(path, { headers: { Authorization: `Bearer ${token}` } });
  if (response.status === 401) throw new Error("The login has expired; ask the user to log in again.");
  if (!response.ok) {
    const problem = await response.json().catch(() => ({}));
    throw new Error(problem.detail ?? `The server answered ${response.status}.`);
  }
  return response.json();
}
