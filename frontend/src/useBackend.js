import { useCallback, useEffect, useRef, useState } from "react";

// One WebSocket to the backend (INTERFACES.md §6). The backend pushes a state_update on every
// transition and once on connect; the HMI renders only the latest one. Reconnects on drop —
// the Pi may still be booting, or uvicorn restarting, when the screen comes up.
export function useBackend() {
  const [update, setUpdate] = useState(null);
  const [connected, setConnected] = useState(false);
  const socket = useRef(null);

  useEffect(() => {
    let retry;
    let closed = false;

    function connect() {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(`${proto}://${location.host}/ws`);
      socket.current = ws;
      ws.onopen = () => setConnected(true);
      ws.onmessage = (e) => {
        const msg = JSON.parse(e.data);
        if (msg.type === "state_update") setUpdate(msg);
      };
      ws.onclose = () => {
        setConnected(false);
        if (!closed) retry = setTimeout(connect, 1500);
      };
    }

    connect();
    return () => {
      closed = true;
      clearTimeout(retry);
      socket.current?.close();
    };
  }, []);

  const send = useCallback((msg) => {
    if (socket.current?.readyState === WebSocket.OPEN) socket.current.send(JSON.stringify(msg));
  }, []);

  return { update, connected, send };
}
