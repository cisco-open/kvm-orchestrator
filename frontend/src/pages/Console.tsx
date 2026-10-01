import VncScreen from "../components/VncScreen";

/**
 * Standalone console page (target of the pop-out /console.html?token=…&vm=…).
 * Thin wrapper: reads the one-shot token from the URL and hands it to the
 * shared VncScreen. The embedded Console tab on the VM detail page uses the
 * same VncScreen component directly.
 */
export default function Console() {
  const params = new URLSearchParams(window.location.search);
  const token = params.get("token") ?? "";
  const vmName = params.get("vm") ?? "VM Console";

  return <VncScreen token={token} vmName={vmName} embedded={false} />;
}
