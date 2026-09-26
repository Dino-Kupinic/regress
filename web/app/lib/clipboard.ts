import { toast } from "sonner";

export function copyToClipboard(text: string, message = "Copied") {
  navigator.clipboard.writeText(text).then(
    () => toast.success(message),
    () => toast.error("Could not copy to the clipboard"),
  );
}
