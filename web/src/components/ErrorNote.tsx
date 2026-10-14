import { ApiError } from "../api";

/** An API or network error, said plainly. */
export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  if (error instanceof ApiError && error.status === 401) {
    // Say what the server said, too: "Pomerium assertion rejected: <why>"
    // is what tells an intermittent sign-in problem apart from a real one.
    return (
      <p role="alert" className="error">
        You're not signed in. Reload the page to sign in again.
        <span className="block muted">{error.message}</span>
      </p>
    );
  }
  const text = error instanceof Error ? error.message : String(error);
  return <p role="alert" className="error">{text}</p>;
}
