import { ApiError } from "../api/client";

/** Shows a server error envelope (code + message) or any other failure. */
export function ErrorBox({ error, onClose }: { error: unknown; onClose?: () => void }) {
  if (!error) return null;
  const code = error instanceof ApiError ? error.code : "error";
  const message = error instanceof Error ? error.message : String(error);
  return (
    <div className="error-box" role="alert">
      <span className="error-code">{code}</span>
      <span className="error-message">{message}</span>
      {onClose && (
        <button className="link" onClick={onClose} aria-label="Dismiss">
          dismiss
        </button>
      )}
    </div>
  );
}
