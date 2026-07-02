export function LoadingRows({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-2">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="skeleton h-10 w-full" />
      ))}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="card p-6 text-center border-red-900/50">
      <p className="text-red-400 text-sm">⚠ {message}</p>
      <p className="text-slate-500 text-xs mt-1">
        Is the API running? <code className="text-slate-400">popper serve</code>
      </p>
      {onRetry && (
        <button
          onClick={onRetry}
          className="mt-3 px-3 py-1 text-xs rounded-md bg-ink-600 hover:bg-ink-500 transition"
        >
          Retry
        </button>
      )}
    </div>
  );
}

export function EmptyState({ message }: { message: string }) {
  return (
    <div className="card p-8 text-center text-slate-500 text-sm border-dashed">{message}</div>
  );
}
