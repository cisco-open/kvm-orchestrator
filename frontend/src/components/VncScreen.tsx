/*
 * SPDX-License-Identifier: Apache-2.0
 *
 * Copyright 2026 Cisco Systems, Inc. and its affiliates
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

import { useEffect, useRef, useState } from "react";
import type RFBType from "@novnc/novnc";

type Status = "connecting" | "connected" | "disconnected" | "error";

/**
 * Reusable noVNC screen. Connects to the console-proxy WebSocket using a
 * one-shot `token` (obtained by the caller via requestConsole). Shared by the
 * standalone Console page (embedded=false, full-viewport) and the VM detail
 * page's Console tab (embedded=true, fills its pane).
 *
 * The token is single-use, so the parent should request a fresh one each time
 * this mounts and pass it in; changing `token` reconnects.
 */
export default function VncScreen({
  token,
  vmName = "VM Console",
  embedded = false,
}: {
  token: string;
  vmName?: string;
  embedded?: boolean;
}) {
  const screenRef = useRef<HTMLDivElement>(null);
  const rfbRef = useRef<RFBType | null>(null);
  const [status, setStatus] = useState<Status>("connecting");
  const [errorMsg, setErrorMsg] = useState("");

  useEffect(() => {
    if (!token) {
      setStatus("error");
      setErrorMsg("No console token — reopen the console.");
      return;
    }
    if (!screenRef.current) return;

    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${proto}//${window.location.host}/console-ws?token=${encodeURIComponent(token)}`;

    let rfb: RFBType | null = null;
    setStatus("connecting");

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    import("@novnc/novnc").then((mod: any) => {
      const RFB = mod.default as typeof RFBType;
      rfb = new RFB(screenRef.current!, wsUrl);
      rfbRef.current = rfb;
      rfb.scaleViewport = true;
      rfb.resizeSession = false;

      rfb.addEventListener("connect", () => setStatus("connected"));
      rfb.addEventListener("disconnect", (e: Event) => {
        rfbRef.current = null;
        const clean = (e as CustomEvent<{ clean: boolean }>).detail?.clean;
        if (clean) {
          setStatus("disconnected");
        } else {
          setStatus("error");
          setErrorMsg("Connection lost — the VM may have shut down or VNC closed.");
        }
      });
      // Most KVM VNC consoles have no VNC password — send empty string.
      rfb.addEventListener("credentialsrequired", () => {
        rfb!.sendCredentials({ password: "" });
      });
    }).catch((err: Error) => {
      setStatus("error");
      setErrorMsg("Failed to load noVNC: " + err.message);
    });

    return () => {
      rfb?.disconnect();
    };
  // Reconnect whenever the token changes (fresh token = fresh session).
  }, [token]);

  const sendCtrlAltDel = () => rfbRef.current?.sendCtrlAltDel();
  const toggleFullscreen = () => {
    if (!document.fullscreenElement) {
      document.documentElement.requestFullscreen().catch(() => {});
    } else {
      document.exitFullscreen().catch(() => {});
    }
  };

  const statusLabel: Record<Status, string> = {
    connecting: "Connecting…", connected: "Connected",
    disconnected: "Disconnected", error: "Error",
  };
  const statusColor: Record<Status, string> = {
    connecting: "#475569", connected: "#166534",
    disconnected: "#475569", error: "#7f1d1d",
  };
  const statusTextColor: Record<Status, string> = {
    connecting: "#94a3b8", connected: "#86efac",
    disconnected: "#94a3b8", error: "#fca5a5",
  };

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: embedded ? "100%" : "100dvh",
        background: "#111",
        overflow: "hidden",
      }}
    >
      {/* Toolbar */}
      <div style={{
        flexShrink: 0, display: "flex", alignItems: "center", gap: 8,
        padding: "6px 12px", background: "#1e293b", borderBottom: "1px solid #334155",
      }}>
        <span style={{ color: "#e2e8f0", fontSize: 13, fontWeight: 600, flex: 1 }}>
          {vmName}
        </span>
        <span style={{
          fontSize: 11, padding: "2px 8px", borderRadius: 9999,
          background: statusColor[status], color: statusTextColor[status],
        }}>
          {statusLabel[status]}
        </span>
        <button onClick={sendCtrlAltDel} disabled={status !== "connected"} style={btnStyle}>
          Ctrl+Alt+Del
        </button>
        <button onClick={toggleFullscreen} style={btnStyle}>
          Full Screen
        </button>
      </div>

      {/* VNC screen container */}
      <div style={{ flex: 1, position: "relative", overflow: "hidden" }}>
        <div ref={screenRef} style={{ width: "100%", height: "100%" }} />

        {status === "error" && (
          <div style={{
            position: "absolute", inset: 0, background: "rgba(0,0,0,0.8)",
            display: "flex", flexDirection: "column",
            alignItems: "center", justifyContent: "center",
            gap: 16, color: "#fca5a5", fontSize: 14, textAlign: "center", padding: 24,
          }}>
            <svg width="40" height="40" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.5">
              <path strokeLinecap="round" strokeLinejoin="round"
                d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z" />
            </svg>
            <p style={{ maxWidth: 400 }}>{errorMsg}</p>
          </div>
        )}

        {status === "disconnected" && (
          <div style={{
            position: "absolute", inset: 0, background: "rgba(0,0,0,0.7)",
            display: "flex", flexDirection: "column",
            alignItems: "center", justifyContent: "center",
            gap: 12, color: "#94a3b8", fontSize: 14,
          }}>
            <p>Session ended.</p>
          </div>
        )}
      </div>
    </div>
  );
}

const btnStyle: React.CSSProperties = {
  background: "#334155", color: "#cbd5e1", border: "none",
  borderRadius: 5, padding: "4px 10px", fontSize: 12, cursor: "pointer",
};
