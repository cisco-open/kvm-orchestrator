import client from "./client";

export interface LogRecord {
  seq: number;
  ts: string;
  level: string;
  logger: string;
  message: string;
  exc?: string;
}

export async function getAdminLogs(params?: {
  limit?: number;
  level?: string;
  contains?: string;
}): Promise<LogRecord[]> {
  const res = await client.get<{ logs: LogRecord[] }>("/admin/logs", {
    params,
  });
  return res.data.logs;
}
