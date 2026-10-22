import { NavLink, Routes, Route, Navigate } from "react-router-dom";
import { LayoutDashboard, Server, Monitor, Network, HardDrive, Users, LogOut, ShieldAlert, ScrollText, BarChart3, UserCog } from "lucide-react";
import { useAuth } from "./hooks/useAuth";
import { VERSION } from "./version";
import { isTestEnv, appName } from "./env";
import { brand } from "./branding";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import Nodes from "./pages/Nodes";
import NodeDetail from "./pages/NodeDetail";
import VMs from "./pages/VMs";
import VMDetail from "./pages/VMDetail";
import Networks from "./pages/Networks";
import Storage from "./pages/Storage";
import Tenants from "./pages/Tenants";
import HA from "./pages/HA";
import Logs from "./pages/Logs";
import Metrics from "./pages/Metrics";
import UsersAdmin from "./pages/UsersAdmin";

function Sidebar() {
  const logout      = useAuth((s) => s.logout);
  const isAdmin     = useAuth((s) => s.isAdmin);
  const username    = useAuth((s) => s.username);
  const displayName = useAuth((s) => s.displayName);
  const tenants     = useAuth((s) => s.tenants);

  const navItem =
    "flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors";
  const active = "bg-gray-800 text-white";
  const inactive = "text-gray-400 hover:bg-gray-800 hover:text-white";

  // Identity line under the user's name. Admins always get "Admin" here
  // (their tenant memberships are irrelevant — they see everything).
  // Non-admins get a comma-separated list of their AD-mapped tenants,
  // or a soft hint when they have none yet.
  const identityLine: string = isAdmin
    ? "Admin"
    : tenants.length > 0
      ? tenants.map((t) => t.name).join(", ")
      : "No tenant memberships";

  // Prefer the AD-supplied display_name; fall back to the CEC username
  // so the strip is never blank during the brief window between login
  // and the /api/auth/me round-trip.
  const primaryName: string = displayName ?? username ?? "";

  return (
    <div
      className={`flex flex-col w-56 min-h-screen px-3 py-4 ${
        isTestEnv() ? "bg-red-950 border-l-4 border-red-500" : "bg-gray-900 border-l-4 border-blue-900"
      }`}
    >
      <div className="px-3 py-3 mb-4">
        <h1 className="text-white font-bold text-lg">{appName()}</h1>
        <img
          src={brand().logo}
          alt={appName()}
          className="mt-2 w-24 h-24 rounded-full object-cover object-top mx-auto"
        />
      </div>

      <nav className="flex flex-col gap-1 flex-1">
        <NavLink
          to="/dashboard"
          end
          className={({ isActive }) => `${navItem} ${isActive ? active : inactive}`}
        >
          <LayoutDashboard size={18} />
          Dashboard
        </NavLink>
        <NavLink
          to="/nodes"
          className={({ isActive }) => `${navItem} ${isActive ? active : inactive}`}
        >
          <Server size={18} />
          Nodes
        </NavLink>
        <NavLink
          to="/vms"
          className={({ isActive }) => `${navItem} ${isActive ? active : inactive}`}
        >
          <Monitor size={18} />
          Virtual Machines
        </NavLink>
        {isAdmin && (
          <NavLink
            to="/networks"
            className={({ isActive }) => `${navItem} ${isActive ? active : inactive}`}
          >
            <Network size={18} />
            Networks
          </NavLink>
        )}
        <NavLink
          to="/storage"
          className={({ isActive }) => `${navItem} ${isActive ? active : inactive}`}
        >
          <HardDrive size={18} />
          Storage
        </NavLink>
        {isAdmin && (
          <NavLink
            to="/tenants"
            className={({ isActive }) => `${navItem} ${isActive ? active : inactive}`}
          >
            <Users size={18} />
            Tenants
          </NavLink>
        )}
        {isAdmin && (
          <NavLink
            to="/ha"
            className={({ isActive }) => `${navItem} ${isActive ? active : inactive}`}
          >
            <ShieldAlert size={18} />
            High Availability
          </NavLink>
        )}
        {isAdmin && (
          <NavLink
            to="/metrics"
            className={({ isActive }) => `${navItem} ${isActive ? active : inactive}`}
          >
            <BarChart3 size={18} />
            Metrics
          </NavLink>
        )}
        {isAdmin && (
          <NavLink
            to="/logs"
            className={({ isActive }) => `${navItem} ${isActive ? active : inactive}`}
          >
            <ScrollText size={18} />
            Logs
          </NavLink>
        )}
        {isAdmin && (
          <NavLink
            to="/users"
            className={({ isActive }) => `${navItem} ${isActive ? active : inactive}`}
          >
            <UserCog size={18} />
            Users
          </NavLink>
        )}
      </nav>

      <div className="mt-2 pt-3 border-t border-gray-800">
        {primaryName && (
          <div className="px-3 pb-2 flex items-center gap-2">
            {username && (
              brand().directoryPhotoBase ? (
                <img
                  src={`${brand().directoryPhotoBase}${username}.jpg`}
                  alt={primaryName}
                  className="h-8 w-8 rounded-full object-cover flex-shrink-0 bg-gray-800"
                  onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = "none"; }}
                />
              ) : (
                <div
                  aria-label={primaryName}
                  title={primaryName}
                  className="h-8 w-8 rounded-full flex items-center justify-center flex-shrink-0 bg-gray-700 text-gray-200 text-xs font-semibold uppercase"
                >
                  {(primaryName || username || "?").trim().charAt(0)}
                </div>
              )
            )}
            <div className="min-w-0">
              <p className="text-sm font-medium text-gray-200 truncate" title={primaryName}>
                {primaryName}
              </p>
              <p className="text-xs text-gray-500 truncate" title={identityLine}>
                {identityLine}
              </p>
            </div>
          </div>
        )}
        <button
          onClick={logout}
          className="w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium text-gray-400 hover:bg-gray-800 hover:text-white transition-colors"
        >
          <LogOut size={18} />
          Sign out
        </button>
        <div className="px-3 pt-2 pb-1">
          <span className="text-xs text-gray-600 font-mono">{VERSION}</span>
        </div>
      </div>
    </div>
  );
}

function App() {
  const isAuthenticated = useAuth((s) => s.isAuthenticated);
  const isAdmin = useAuth((s) => s.isAdmin);

  if (!isAuthenticated) {
    return <Login />;
  }

  return (
    <div className="flex h-screen bg-gray-50">
      <Sidebar />
      <main className="flex-1 min-w-0 overflow-y-auto">
        {isTestEnv() && (
          <div className="sticky top-0 z-50 bg-red-700 text-white text-center text-sm font-bold py-1 tracking-wide">
            ⚠ TEST ENVIRONMENT — {appName()} — NOT PRODUCTION ⚠
          </div>
        )}
        <Routes>
          <Route path="/" element={<Navigate to="/vms" replace />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/nodes" element={<Nodes />} />
          <Route path="/nodes/:id" element={<NodeDetail />} />
          <Route path="/vms" element={<VMs />} />
          <Route path="/vms/:id" element={<VMDetail />} />
          {isAdmin && <Route path="/networks" element={<Networks />} />}
          <Route path="/storage" element={<Storage />} />
          {isAdmin && <Route path="/tenants" element={<Tenants />} />}
          {isAdmin && <Route path="/ha" element={<HA />} />}
          {isAdmin && <Route path="/logs" element={<Logs />} />}
          {isAdmin && <Route path="/metrics" element={<Metrics />} />}
          {isAdmin && <Route path="/users" element={<UsersAdmin />} />}
          <Route path="*" element={<Navigate to="/vms" replace />} />
        </Routes>
      </main>
    </div>
  );
}

export default App;
