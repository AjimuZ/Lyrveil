// Hands a downloaded file to the browser's own save step.
import type { DownloadedFile } from './characters-store';

/**
 * Saves `file` through a temporary link, so the browser shows its normal download. The object URL is released right
 * after the click, because the browser has already taken the bytes by then.
 */
export function saveFile(file: DownloadedFile): void {
  const url = URL.createObjectURL(file.blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = file.name;
  link.hidden = true;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
