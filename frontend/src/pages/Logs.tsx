import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ScrollText, RefreshCw } from "lucide-react";
import { getAdminLogs, type LogRecord } from "../api/admin";
import { useAuth } from "../hooks/useAuth";

const LEVELS = ["", "INFO", "WARNING", "ERROR"] as const;

function levelColor(level: string): string {
  switch (level) {
    case "ERROR":
    case "CRITICAL":
      return "text-red-600";
    case "WARNING":
      return "text-amber-600";
    case "DEBUG":
      return "text-gray-400";
    default:
      return "text-gray-600";
  }
}

export default function Logs() {
  const isAdmin = useAuth((s) => s.isAdmin);
  const [level, setLevel] = useState<string>("");
  const [contains, setContains] = useState("");
  const [auto, setAuto] = useState(true);

  const { data: logs = [], isFetching, refetch } = useQuery<LogRecord[]>({
    queryKey: ["admin-logs", level, contains],
    queryFn: () => getAdminLogs({ limit: 1000, level: level || undefined, contains: contains || undefined }),
    enabled: isAdmin,
    refetchInterval: auto ? 5000 : false,
  });

  if (!isAdmin) return <div className="p-6 text-gray-500">Admins only.</div>;

  return (
    <div className="p-6">
      <div className="flex items-center gap-2 mb-1">
        <ScrollText size={22} />
        <h1 className="text-2xl font-bold">Application Logs</h1>
      </div>
      <p className="text-sm text-gray-500 mb-4">
        Recent <code>wvc.*</code> logs from the API process — no more trawling{" "}
        <code>kubectl logs</code>. In-memory per-pod buffer (recent activity, not full history).
      </p>

      <div className="flex flex-wrap items-center gap-3 mb-3">
        <select
          value={level}
          onChange={(e) => setLevel(e.target.value)}
          className="border rounded-md px-2 py-1.5 text-sm"
        >
          {LEVELS.map((l) => (
            <option key={l} value={l}>{l || "All levels"}</option>
          ))}
        </select>
        <input
          value={contains}
          onChange={(e) => setContains(e.target.value)}
          placeholder="Filter text…"
          className="border rounded-md px-2 py-1.5 text-sm flex-1 min-w-[200px]"
        />
        <label className="flex items-center gap-1 text-sm text-gray-600">
          <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} />
          auto-refresh
        </label>
        <button
          onClick={() => refetch()}
          className="flex items-center gap-1 border rounded-md px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50"
        >
          <RefreshCw size={14} className={isFetching ? "animate-spin" : ""} /> Refresh
        </button>
      </div>

      <div className="rounded-lg border bg-gray-900 text-gray-100 font-mono text-xs overflow-x-auto">
        {logs.length === 0 ? (
          <div className="p-4 text-gray-400">No matching log records.</div>
        ) : (
          <table className="min-w-full">
            <tbody>
              {logs.map((r) => (
                <tr key={r.seq} className="border-b border-gray-800 hover:bg-gray-800/50 align-top">
                  <td className="px-3 py-1 whitespace-nowrap text-gray-500">
                    {new Date(r.ts).toLocaleTimeString()}
                  </td>
                  <td className={`px-2 py-1 whitespace-nowrap font-semibold ${levelColor(r.level)}`}>
                    {r.level}
                  </td>
                  <td className="px-2 py-1 whitespace-nowrap text-cyan-400">{r.logger}</td>
                  <td className="px-3 py-1 whitespace-pre-wrap break-words text-gray-100">
                    {r.message}
                    {r.exc && (
                      <pre className="mt-1 text-red-300 whitespace-pre-wrap">{r.exc}</pre>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
