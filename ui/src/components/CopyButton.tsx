import { useState } from "react";

export function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      window.prompt("Copy this value:", text);
    }
  };
  return (
    <button type="button" onClick={copy}>
      {copied ? "Copied" : "Copy"}
    </button>
  );
}
