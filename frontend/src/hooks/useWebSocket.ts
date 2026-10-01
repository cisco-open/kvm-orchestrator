import { useEffect, useRef, useState } from "react";

interface UseWebSocketOptions {
  url: string;
  onMessage?: (data: MessageEvent) => void;
  onOpen?: () => void;
  onClose?: () => void;
  onError?: (error: Event) => void;
}

export function useWebSocket({ url, onMessage, onOpen, onClose, onError }: UseWebSocketOptions) {
  const wsRef = useRef<WebSocket | null>(null);
  const [isConnected, setIsConnected] = useState(false);

  useEffect(() => {
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      setIsConnected(true);
      onOpen?.();
    };

    ws.onmessage = (event) => {
      onMessage?.(event);
    };

    ws.onclose = () => {
      setIsConnected(false);
      onClose?.();
    };

    ws.onerror = (error) => {
      onError?.(error);
    };

    return () => {
      ws.close();
    };
  }, [url]);

  const send = (data: string | ArrayBuffer) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(data);
    }
  };

  return { isConnected, send, ws: wsRef };
}
