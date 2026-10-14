import { useCallback, useEffect, useState } from "react";
import { api } from "../api";

/** The unread-notification count, kept fresh: on load, every minute, when
 *  the tab comes back into view, and whenever something calls `refresh`
 *  (the inbox does, after marking things read). */
export function useUnread(): { unread: number; refresh: () => void } {
  const [unread, setUnread] = useState(0);
  const refresh = useCallback(() => {
    api.inbox(true).then((r) => setUnread(r.unread), () => { /* the badge is a nicety; stay quiet */ });
  }, []);
  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, 60_000);
    const onVisible = () => document.visibilityState === "visible" && refresh();
    const onChange = () => refresh();
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("choreboard:inbox", onChange);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("choreboard:inbox", onChange);
    };
  }, [refresh]);
  return { unread, refresh };
}

/** Tell the badge the inbox changed. */
export function inboxChanged(): void {
  window.dispatchEvent(new Event("choreboard:inbox"));
}
