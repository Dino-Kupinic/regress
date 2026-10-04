import { index, type RouteConfig, route } from "@react-router/dev/routes";

export default [
  index("./routes/dashboard.tsx"),
  route("runs", "./routes/runs.tsx"),
  route("runs/:runId", "./routes/run.tsx"),
  route("batches/:batchId", "./routes/batch.tsx"),
  route("evaluation", "./routes/evaluation.tsx"),
  route("settings", "./routes/settings.tsx"),
  route("setup", "./routes/setup.tsx"),
] satisfies RouteConfig;
