import { ApiError } from "../api";

/** An API or network error, said plainly. */
export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  const text = error instanceof ApiError
    ? error.status === 401 ? "You're not signed in. Reload the page to sign in again." : error.message
    : error instanceof Error ? error.message : String(error);
  return <p role="alert" className="error">{text}</p>;
}
